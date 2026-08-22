"""Serving alias binding with an exercised rollback path to base BGE-M3.

This module implements the SKL-S3-04C serving-alias slice. One logical
serving alias (``embedding.serving.primary``) binds to exactly one
qualified shadow route at a time. Every binding, rollback, and fail-close
appends one immutable revision citing the qualification verdict sha that
justified it, so the alias history is reconstructable and never rewritten.

Rollback is a real state transition, not a documented intent: the registry
refuses to roll back unless a verdict actually failed the custom route and
the base BGE-M3 route actually qualified, and queries served after the
rollback execute through the base route generation. When no route
qualifies the alias fails closed and serving refuses every query.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from .embedding_qualification import (
    EmbeddingQualificationVerdict,
    QualificationDecision,
    RouteQualificationVerdict,
    ThresholdSetFile,
)
from .errors import RetrievalIntegrityError
from .evaluation_dataset import EvalQuery
from .models import OpaqueId, RetrievalValue, SafeVersion, Sha256
from .shadow_comparison import (
    DEFAULT_MAX_RESULTS,
    Clock,
    ShadowCandidate,
    ShadowEvaluationPlane,
    ShadowRouteLabel,
)

DEFAULT_ALIAS_NAME = "embedding.serving.primary"


class EmbeddingAliasError(RetrievalIntegrityError):
    """A serving alias binding, rollback, or resolution failed its gates."""


class AliasStatus(StrEnum):
    UNBOUND = "unbound"
    ACTIVE = "active"
    FAIL_CLOSED = "fail_closed"


class AliasAction(StrEnum):
    BIND = "bind"
    ROLLBACK = "rollback"
    FAIL_CLOSED = "fail_closed"


class AliasBinding(RetrievalValue):
    """One route pinned as the alias target by one qualification verdict."""

    route_label: ShadowRouteLabel
    embedding_model_id: OpaqueId
    embedding_model_revision: SafeVersion
    embedder_kind: str = Field(min_length=1, max_length=64)
    projection_set_id: UUID
    projection_generation: int = Field(ge=1)
    justification_verdict_sha256: Sha256


class AliasRevision(RetrievalValue):
    """One append-only alias transition citing its justifying verdict."""

    sequence: int = Field(ge=1)
    action: AliasAction
    binding: AliasBinding | None
    reason: str = Field(min_length=1, max_length=2048)
    verdict_sha256: Sha256

    @model_validator(mode="after")
    def validate_action_binding(self) -> Self:
        closed = self.action is AliasAction.FAIL_CLOSED
        if closed != (self.binding is None):
            raise ValueError("exactly a fail-closed revision carries no binding")
        return self


class EmbeddingAliasState(RetrievalValue):
    """The immutable alias state at one point in its history."""

    alias_name: OpaqueId
    thresholds_sha256: Sha256
    thresholds_version: SafeVersion
    dataset_freeze_sha256: Sha256
    status: AliasStatus
    current_binding: AliasBinding | None
    history: tuple[AliasRevision, ...]

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if (self.status is AliasStatus.ACTIVE) != (self.current_binding is not None):
            raise ValueError("an active alias carries exactly one binding")
        if self.status is AliasStatus.UNBOUND and self.history:
            raise ValueError("an unbound alias has no history")
        if self.status is AliasStatus.FAIL_CLOSED and not self.history:
            raise ValueError("a fail-closed alias records the closing verdict")
        sequences = [revision.sequence for revision in self.history]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("revision sequences must be contiguous from one")
        for revision in self.history:
            if revision.binding is not None and (
                revision.verdict_sha256 != revision.binding.justification_verdict_sha256
            ):
                raise ValueError("each revision cites the verdict behind its binding")
        if self.current_binding is not None:
            if not self.history or self.history[-1].binding != self.current_binding:
                raise ValueError("the current binding comes from the latest revision")
        if self.status is AliasStatus.FAIL_CLOSED and (
            self.history[-1].action is not AliasAction.FAIL_CLOSED
        ):
            raise ValueError("a fail-closed alias ends on its fail-closed revision")
        return self


def _failed_citations(*verdicts: RouteQualificationVerdict) -> str:
    joined = "; ".join(
        f"{check.metric} measured {check.measured} bound {check.operator} {check.bound}"
        for verdict in verdicts
        for check in verdict.failed_checks
    )
    return joined if joined else "no failed check"


class AliasServedQuery(RetrievalValue):
    """One query served through the alias by its currently bound route."""

    query_id: OpaqueId
    alias_name: OpaqueId
    revision_sequence: int = Field(ge=1)
    route_label: ShadowRouteLabel
    embedding_model_id: OpaqueId
    embedding_model_revision: SafeVersion
    ranked_document_ids: tuple[str, ...]
    latency_seconds: float

    @model_validator(mode="after")
    def validate_served_query(self) -> Self:
        if not (self.latency_seconds >= 0):
            raise ValueError("served query latency must be non-negative")
        return self


class EmbeddingAliasRegistry:
    """Append-only serving alias over qualified shadow routes."""

    def __init__(
        self, thresholds: ThresholdSetFile, *, alias_name: str = DEFAULT_ALIAS_NAME
    ) -> None:
        self._thresholds = thresholds
        self._state = EmbeddingAliasState(
            alias_name=alias_name,
            thresholds_sha256=thresholds.file_sha256,
            thresholds_version=thresholds.thresholds.thresholds_version,
            dataset_freeze_sha256=thresholds.thresholds.dataset_freeze_sha256,
            status=AliasStatus.UNBOUND,
            current_binding=None,
            history=(),
        )
        self._candidates: dict[ShadowRouteLabel, ShadowCandidate] = {}
        self._planes: dict[ShadowRouteLabel, ShadowEvaluationPlane] = {}

    @property
    def state(self) -> EmbeddingAliasState:
        return self._state

    def register_candidate(self, candidate: ShadowCandidate) -> None:
        """Register one candidate generation the alias may bind."""

        if candidate.label in self._candidates:
            raise EmbeddingAliasError(
                f"the alias already registered a {candidate.label.value} candidate"
            )
        if (
            candidate.generation.spec.dataset_freeze_sha256
            != self._state.dataset_freeze_sha256
        ):
            raise EmbeddingAliasError(
                "a candidate generation must share the approved dataset freeze"
            )
        self._candidates[candidate.label] = candidate
        self._planes[candidate.label] = ShadowEvaluationPlane(candidate.generation)

    def _require_verdict_pins(self, verdict: EmbeddingQualificationVerdict) -> None:
        if verdict.thresholds_sha256 != self._state.thresholds_sha256:
            raise EmbeddingAliasError("the verdict cites a different threshold file")
        if verdict.dataset_freeze_sha256 != self._state.dataset_freeze_sha256:
            raise EmbeddingAliasError("the verdict cites a different dataset freeze")

    def _binding_for(
        self, candidate: ShadowCandidate, verdict: EmbeddingQualificationVerdict
    ) -> AliasBinding:
        route = candidate.generation.spec.route
        return AliasBinding(
            route_label=route.label,
            embedding_model_id=route.vector.embedding_model_id,
            embedding_model_revision=route.vector.embedding_model_revision,
            embedder_kind=route.embedder_kind,
            projection_set_id=candidate.generation.projection_set_id,
            projection_generation=candidate.generation.spec.projection_generation,
            justification_verdict_sha256=verdict.canonical_sha256(),
        )

    def _append(
        self,
        action: AliasAction,
        binding: AliasBinding | None,
        reason: str,
        verdict: EmbeddingQualificationVerdict,
    ) -> AliasRevision:
        revision = AliasRevision(
            sequence=len(self._state.history) + 1,
            action=action,
            binding=binding,
            reason=reason,
            verdict_sha256=verdict.canonical_sha256(),
        )
        self._state = EmbeddingAliasState(
            alias_name=self._state.alias_name,
            thresholds_sha256=self._state.thresholds_sha256,
            thresholds_version=self._state.thresholds_version,
            dataset_freeze_sha256=self._state.dataset_freeze_sha256,
            status=AliasStatus.ACTIVE
            if binding is not None
            else AliasStatus.FAIL_CLOSED,
            current_binding=binding,
            history=self._state.history + (revision,),
        )
        return revision

    def bind_candidate(
        self, candidate: ShadowCandidate, verdict: EmbeddingQualificationVerdict
    ) -> AliasRevision:
        """Bind one registered candidate whose route verdict qualified."""

        self._require_verdict_pins(verdict)
        if self._candidates.get(candidate.label) is not candidate:
            raise EmbeddingAliasError("only a registered candidate can be bound")
        route_verdict = verdict.route_verdict(candidate.label)
        if not route_verdict.qualified:
            raise EmbeddingAliasError(
                "an alias binds only a route whose verdict qualified"
            )
        binding = self._binding_for(candidate, verdict)
        reason = (
            f"bind {binding.route_label.value} ({binding.embedding_model_id})"
            f" at revision {binding.projection_generation} on freeze"
            f" {verdict.dataset_freeze_sha256[:16]}...;"
            f" {len(route_verdict.checks)} checks,"
            f" {len(route_verdict.failed_checks)} failed,"
            f" {len(route_verdict.waived_checks)} waived"
        )
        return self._append(AliasAction.BIND, binding, reason, verdict)

    def rollback(
        self,
        target: ShadowCandidate,
        failed_verdict: EmbeddingQualificationVerdict,
    ) -> AliasRevision:
        """Roll the alias back to base BGE-M3 after a failed custom verdict."""

        self._require_verdict_pins(failed_verdict)
        if failed_verdict.decision is not QualificationDecision.ROLLBACK_TO_BASE:
            raise EmbeddingAliasError(
                "rollback requires a verdict that failed the custom route"
            )
        if target.label is not ShadowRouteLabel.BASE_BGE_M3 or (
            target.label is not failed_verdict.base_route_label
        ):
            raise EmbeddingAliasError("rollback targets the base BGE-M3 route")
        if self._candidates.get(target.label) is not target:
            raise EmbeddingAliasError("only a registered candidate can be a target")
        current = self._state.current_binding
        if current is None:
            raise EmbeddingAliasError("rollback requires an existing binding")
        if current.route_label is not failed_verdict.custom_route_label:
            raise EmbeddingAliasError(
                "the alias is not serving the route the verdict failed"
            )
        base_verdict = failed_verdict.route_verdict(target.label)
        if not base_verdict.qualified:
            raise EmbeddingAliasError("the rollback target did not qualify")
        binding = self._binding_for(target, failed_verdict)
        failed = failed_verdict.route_verdict(failed_verdict.custom_route_label)
        citations = _failed_citations(failed)
        reason = (
            f"rollback to {binding.route_label.value}"
            f" ({binding.embedding_model_id}) because"
            f" {failed_verdict.custom_route_label.value} failed qualification on"
            f" freeze {failed_verdict.dataset_freeze_sha256[:16]}...: {citations}"
        )
        return self._append(AliasAction.ROLLBACK, binding, reason, failed_verdict)

    def fail_close(
        self, failed_verdict: EmbeddingQualificationVerdict
    ) -> AliasRevision:
        """Fail the alias closed when no route qualifies to serve."""

        self._require_verdict_pins(failed_verdict)
        if failed_verdict.decision is not QualificationDecision.FAIL_CLOSED:
            raise EmbeddingAliasError(
                "fail-close requires a verdict where no route qualifies"
            )
        custom = failed_verdict.route_verdict(failed_verdict.custom_route_label)
        base = failed_verdict.route_verdict(failed_verdict.base_route_label)
        citations = _failed_citations(custom, base)
        reason = (
            f"fail closed: no route qualifies on freeze"
            f" {failed_verdict.dataset_freeze_sha256[:16]}...;"
            f" {failed_verdict.custom_route_label.value}: {citations};"
            f" {failed_verdict.base_route_label.value} qualified checks also failed"
        )
        return self._append(AliasAction.FAIL_CLOSED, None, reason, failed_verdict)

    def resolve(self) -> AliasBinding:
        """Return the active binding or fail closed."""

        if self._state.status is not AliasStatus.ACTIVE:
            raise EmbeddingAliasError(
                f"alias {self._state.alias_name} is"
                f" {self._state.status.value} and fails closed"
            )
        binding = self._state.current_binding
        if binding is None:
            raise EmbeddingAliasError("an active alias lost its binding")
        return binding

    def serve_query(
        self,
        query: EvalQuery,
        *,
        max_results: int = DEFAULT_MAX_RESULTS,
        clock: Clock = time.perf_counter,
    ) -> AliasServedQuery:
        """Serve one query through the route the alias currently binds."""

        binding = self.resolve()
        candidate = self._candidates.get(binding.route_label)
        if candidate is None:
            raise EmbeddingAliasError("the bound route has no registered candidate")
        outcome = self._planes[binding.route_label].execute_query(
            query, candidate.embedder, max_results=max_results, clock=clock
        )
        return AliasServedQuery(
            query_id=query.query_id,
            alias_name=self._state.alias_name,
            revision_sequence=self._state.history[-1].sequence,
            route_label=binding.route_label,
            embedding_model_id=binding.embedding_model_id,
            embedding_model_revision=binding.embedding_model_revision,
            ranked_document_ids=outcome.ranked_document_ids(),
            latency_seconds=outcome.latency_seconds,
        )


__all__ = [
    "AliasAction",
    "AliasBinding",
    "AliasRevision",
    "AliasServedQuery",
    "AliasStatus",
    "DEFAULT_ALIAS_NAME",
    "EmbeddingAliasError",
    "EmbeddingAliasRegistry",
    "EmbeddingAliasState",
]
