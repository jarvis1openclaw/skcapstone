"""Fail-closed application service for governed corpus retrieval."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sklegal_persistence.features.governed_corpus.models import (
    Classification,
    CorpusAuditEvent,
    CorpusOutboxRecord,
    CorpusSourceVersion,
    GovernedCorpusHit,
    GovernedCorpusResult,
    ProjectionGuardStatus,
    ProjectionRegistryState,
    ProjectionState,
    RankEvidence,
    RetrievalMode,
    RetrievalQuery,
    Sha256,
    canonical_sha256,
)
from sklegal_persistence.features.governed_corpus.repository import (
    GovernedCorpusCoreRepository,
    GovernedCorpusIdempotencyConflict,
    GovernedCorpusRepositoryUnavailable,
    GovernedCorpusRetrievalRepository,
    GovernedCorpusVersionConflict,
    RankedSource,
)

from .contracts import (
    CorpusSearchCommand,
    CorpusSearchRead,
    CorpusSourceMutationReceipt,
    CorpusSourceRead,
    CorpusSpanCommand,
    CorpusSpanRead,
    GovernedCorpusResultRead,
    RecordCorpusSourceCommand,
)

Capability = Literal["corpus.search", "corpus.artifact.read", "corpus.ingest.submit"]
Purpose = Literal["legal_research", "corpus_ingestion"]
Operation = Literal["corpus.search", "corpus.span.read", "corpus.source.record"]


class GovernedCorpusServiceError(RuntimeError):
    """Sanitized error from the closed corpus vocabulary."""

    def __init__(
        self,
        code: Literal[
            "authentication_required",
            "access_denied",
            "not_found",
            "stale_projection",
            "validation_failed",
            "idempotency_conflict",
            "version_conflict",
            "policy_unavailable",
            "resource_unavailable",
            "internal_error",
        ],
        projection_status: ProjectionGuardStatus | None = None,
    ) -> None:
        self.code = code
        self.projection_status = projection_status
        super().__init__(code)


class GovernedCorpusAccessContext(BaseModel):
    """Trusted request-local identity derived from CapAuth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    capability: Capability
    purpose: Purpose
    authorization_decision_id: UUID
    authorization_context_sha256: Sha256
    credential_expires_at: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def validate_expiry(self) -> Self:
        if (
            self.credential_expires_at.tzinfo is None
            or self.credential_expires_at.utcoffset() is None
        ):
            raise ValueError("credential expiry must be timezone aware")
        return self


class GovernedCorpusPolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    operation: Operation
    capability: Capability
    purpose: Purpose
    allowed: bool
    classification_ceiling: Classification
    rights_revision: Sha256
    revision: Sha256
    evaluated_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (
            self.evaluated_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.valid_until <= self.evaluated_at
        ):
            raise ValueError("policy validity window is invalid")
        return self


class _CursorPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    operation: Literal["corpus.search"] = "corpus.search"
    capability: Literal["corpus.search"] = "corpus.search"
    purpose: Literal["legal_research"] = "legal_research"
    authorization_context_sha256: Sha256
    policy_revision: Sha256
    rights_revision: Sha256
    classification_ceiling: Classification
    query_sha256: Sha256
    release_id: str = Field(min_length=1, max_length=200)
    projection_generation: int = Field(ge=1)
    backend_watermark: int = Field(ge=0)
    core_watermark: int = Field(ge=0)
    mode: RetrievalMode
    page_size: int = Field(ge=1, le=50)
    snapshot_at: datetime
    last_score_hex: str = Field(min_length=1, max_length=64)
    last_source_version_id: UUID
    last_rank: int = Field(ge=1)

    @field_validator("snapshot_at")
    @classmethod
    def validate_snapshot(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cursor snapshot must be timezone aware")
        return value

    @field_validator("last_score_hex")
    @classmethod
    def validate_score(cls, value: str) -> str:
        try:
            score = float.fromhex(value)
        except ValueError:
            raise ValueError("cursor score is malformed") from None
        if not math.isfinite(score):
            raise ValueError("cursor score is malformed")
        return value

    @property
    def last_score(self) -> float:
        return float.fromhex(self.last_score_hex)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    encoded = value.encode("ascii")
    padding = b"=" * (-len(encoded) % 4)
    return base64.b64decode(encoded + padding, altchars=b"-_", validate=True)


class GovernedCorpusPolicy(Protocol):
    def authorize(
        self,
        *,
        context: GovernedCorpusAccessContext,
        operation: Operation,
        capability: Capability,
        purpose: Purpose,
        now: datetime,
    ) -> GovernedCorpusPolicyDecision: ...


class StaticGovernedCorpusPolicy:
    """Deterministic public-synthetic policy used only by isolated tests."""

    def __init__(
        self,
        *,
        grants: set[tuple[UUID, UUID, UUID, Capability, Purpose]],
        classification_ceiling: Classification,
        rights_revision: str,
        revision: str,
        valid_until: datetime,
    ) -> None:
        self.grants = grants
        self.classification_ceiling = classification_ceiling
        self.rights_revision = rights_revision
        self.revision = revision
        self.valid_until = valid_until
        self.available = True

    def authorize(
        self,
        *,
        context: GovernedCorpusAccessContext,
        operation: Operation,
        capability: Capability,
        purpose: Purpose,
        now: datetime,
    ) -> GovernedCorpusPolicyDecision:
        if not self.available:
            raise RuntimeError("policy unavailable")
        return GovernedCorpusPolicyDecision(
            decision_id=uuid5(
                NAMESPACE_URL,
                f"corpus-policy:{self.revision}:{context.tenant_id}:"
                f"{context.matter_id}:{context.principal_id}:{operation}",
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            capability=capability,
            purpose=purpose,
            allowed=(
                context.tenant_id,
                context.matter_id,
                context.principal_id,
                capability,
                purpose,
            )
            in self.grants,
            classification_ceiling=self.classification_ceiling,
            rights_revision=self.rights_revision,
            revision=self.revision,
            evaluated_at=now,
            valid_until=self.valid_until,
        )


def _uuid(label: str, *parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, ":".join((label, *(str(part) for part in parts))))


def _key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class GovernedCorpusService:
    def __init__(
        self,
        *,
        core_repository: GovernedCorpusCoreRepository,
        retrieval_repository: GovernedCorpusRetrievalRepository,
        policy: GovernedCorpusPolicy,
        clock: Callable[[], datetime],
        cursor_signing_key: bytes,
    ) -> None:
        if len(cursor_signing_key) < 32:
            raise ValueError("cursor signing key must contain at least 32 bytes")
        self._core_repository = core_repository
        self._retrieval_repository = retrieval_repository
        self._policy = policy
        self._clock = clock
        self._cursor_signing_key = bytes(cursor_signing_key)

    @staticmethod
    def _projection_failure(
        status: ProjectionGuardStatus,
    ) -> GovernedCorpusServiceError:
        if status in {
            ProjectionGuardStatus.UNAVAILABLE,
            ProjectionGuardStatus.UNKNOWN,
        }:
            return GovernedCorpusServiceError(
                "resource_unavailable", projection_status=status
            )
        if status is ProjectionGuardStatus.STALE:
            return GovernedCorpusServiceError(
                "stale_projection", projection_status=status
            )
        if status is ProjectionGuardStatus.UNAUTHORIZED:
            return GovernedCorpusServiceError("access_denied", projection_status=status)
        raise ValueError("current projection status is not a failure")

    def _encode_cursor(self, payload: _CursorPayload) -> str:
        body = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.digest(self._cursor_signing_key, body, "sha256")
        return f"{_base64url_encode(body)}.{_base64url_encode(signature)}"

    def _decode_cursor(self, cursor: str) -> _CursorPayload:
        try:
            body_token, signature_token = cursor.split(".")
            body = _base64url_decode(body_token)
            signature = _base64url_decode(signature_token)
            expected = hmac.digest(self._cursor_signing_key, body, "sha256")
            if not hmac.compare_digest(signature, expected):
                raise ValueError("cursor signature mismatch")
            return _CursorPayload.model_validate_json(body)
        except Exception:
            raise GovernedCorpusServiceError("validation_failed") from None

    def _authorize(
        self,
        *,
        context: GovernedCorpusAccessContext,
        matter_id: UUID,
        operation: Operation,
        capability: Capability,
        purpose: Purpose,
    ) -> GovernedCorpusPolicyDecision:
        now = self._clock()
        if (
            context.tenant_id is None
            or context.matter_id != matter_id
            or context.capability != capability
            or context.purpose != purpose
            or context.revoked
            or context.credential_expires_at <= now
        ):
            raise GovernedCorpusServiceError("access_denied")
        try:
            member = self._core_repository.is_matter_member(
                context.tenant_id, matter_id, context.principal_id
            )
        except Exception:
            raise GovernedCorpusServiceError("resource_unavailable") from None
        if not member:
            raise GovernedCorpusServiceError("access_denied")
        try:
            decision = self._policy.authorize(
                context=context,
                operation=operation,
                capability=capability,
                purpose=purpose,
                now=now,
            )
        except Exception:
            raise GovernedCorpusServiceError("policy_unavailable") from None
        if (
            not isinstance(decision, GovernedCorpusPolicyDecision)
            or not decision.allowed
            or decision.tenant_id != context.tenant_id
            or decision.matter_id != matter_id
            or decision.principal_id != context.principal_id
            or decision.operation != operation
            or decision.capability != capability
            or decision.purpose != purpose
            or decision.evaluated_at > now
            or decision.valid_until <= now
        ):
            raise GovernedCorpusServiceError("access_denied")
        return decision

    def _projection(
        self,
        *,
        context: GovernedCorpusAccessContext,
        expected_release_id: str,
        expected_generation: int,
        required_watermark: int,
        decision: GovernedCorpusPolicyDecision,
        verify_policy: bool = True,
    ) -> ProjectionState:
        try:
            registry = self._core_repository.projection_registry(
                context.tenant_id, context.matter_id
            )
        except Exception:
            raise self._projection_failure(ProjectionGuardStatus.UNAVAILABLE) from None
        if registry is None:
            raise self._projection_failure(ProjectionGuardStatus.UNKNOWN)
        if verify_policy and (
            registry.policy_revision != decision.revision
            or registry.rights_revision != decision.rights_revision
        ):
            raise self._projection_failure(ProjectionGuardStatus.UNAUTHORIZED)
        if (
            registry.release_id != expected_release_id
            or registry.projection_generation != expected_generation
            or registry.core_watermark != required_watermark
        ):
            raise self._projection_failure(ProjectionGuardStatus.STALE)
        try:
            projection = self._retrieval_repository.projection_state(
                context.tenant_id, context.matter_id
            )
        except Exception:
            raise self._projection_failure(ProjectionGuardStatus.UNAVAILABLE) from None
        if projection is None:
            raise self._projection_failure(ProjectionGuardStatus.UNKNOWN)
        if (
            projection.release_id != registry.release_id
            or projection.projection_generation != registry.projection_generation
            or projection.core_watermark != registry.core_watermark
            or not projection.current
        ):
            raise self._projection_failure(ProjectionGuardStatus.STALE)
        return projection

    def _write_projection(
        self,
        *,
        context: GovernedCorpusAccessContext,
        expected_release_id: str,
        expected_generation: int,
        decision: GovernedCorpusPolicyDecision,
    ) -> ProjectionRegistryState:
        try:
            registry = self._core_repository.projection_registry(
                context.tenant_id, context.matter_id
            )
        except Exception:
            raise self._projection_failure(ProjectionGuardStatus.UNAVAILABLE) from None
        if registry is None:
            raise self._projection_failure(ProjectionGuardStatus.UNKNOWN)
        if (
            registry.release_id != expected_release_id
            or registry.projection_generation != expected_generation
        ):
            raise self._projection_failure(ProjectionGuardStatus.STALE)
        if (
            registry.policy_revision != decision.revision
            or registry.rights_revision != decision.rights_revision
        ):
            raise self._projection_failure(ProjectionGuardStatus.UNAUTHORIZED)
        return registry

    @staticmethod
    def _rank_path(
        mode: RetrievalMode,
    ) -> tuple[Literal["full_text", "pgvector_exact", "hybrid_rrf"], ...]:
        if mode is RetrievalMode.FULL_TEXT:
            return ("full_text",)
        if mode is RetrievalMode.VECTOR_EXACT:
            return ("pgvector_exact",)
        return ("full_text", "pgvector_exact", "hybrid_rrf")

    def search(
        self,
        *,
        context: GovernedCorpusAccessContext,
        matter_id: UUID,
        command: CorpusSearchCommand,
    ) -> CorpusSearchRead:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="corpus.search",
            capability="corpus.search",
            purpose="legal_research",
        )
        cursor = (
            None
            if command.continuation_cursor is None
            else self._decode_cursor(command.continuation_cursor)
        )
        if cursor is not None and (
            cursor.authorization_context_sha256 != context.authorization_context_sha256
            or cursor.policy_revision != decision.revision
            or cursor.rights_revision != decision.rights_revision
            or cursor.classification_ceiling != decision.classification_ceiling
        ):
            raise GovernedCorpusServiceError("validation_failed")
        projection = self._projection(
            context=context,
            expected_release_id=command.expected_release_id,
            expected_generation=command.expected_projection_generation,
            required_watermark=command.required_core_watermark,
            decision=decision,
        )
        snapshot_at = self._clock() if cursor is None else cursor.snapshot_at
        query = RetrievalQuery(
            query=command.query,
            query_embedding=command.query_embedding,
            mode=command.mode,
            max_results=command.max_results,
            expected_release_id=command.expected_release_id,
            expected_projection_generation=command.expected_projection_generation,
            required_core_watermark=command.required_core_watermark,
            classification_ceiling=decision.classification_ceiling,
            rights_revision=decision.rights_revision,
            snapshot_at=snapshot_at,
            after_score=None if cursor is None else cursor.last_score,
            after_source_version_id=(
                None if cursor is None else cursor.last_source_version_id
            ),
        )
        query_sha256 = canonical_sha256(
            query.model_dump(
                mode="json",
                exclude={"snapshot_at", "after_score", "after_source_version_id"},
            )
        )
        if cursor is not None and (
            cursor.tenant_id != context.tenant_id
            or cursor.matter_id != matter_id
            or cursor.principal_id != context.principal_id
            or cursor.operation != "corpus.search"
            or cursor.capability != context.capability
            or cursor.purpose != context.purpose
            or cursor.authorization_context_sha256
            != context.authorization_context_sha256
            or cursor.policy_revision != decision.revision
            or cursor.rights_revision != decision.rights_revision
            or cursor.classification_ceiling != decision.classification_ceiling
            or cursor.query_sha256 != query_sha256
            or cursor.release_id != projection.release_id
            or cursor.projection_generation != projection.projection_generation
            or cursor.backend_watermark != projection.backend_watermark
            or cursor.core_watermark != projection.core_watermark
            or cursor.mode != query.mode
            or cursor.page_size != query.max_results
        ):
            raise GovernedCorpusServiceError("validation_failed")
        try:
            page = self._retrieval_repository.retrieve(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                principal_id=context.principal_id,
                query=query,
            )
            rows = page.rows
            has_more = page.has_more
        except GovernedCorpusRepositoryUnavailable:
            raise GovernedCorpusServiceError("resource_unavailable") from None
        except Exception:
            raise GovernedCorpusServiceError("internal_error") from None
        self._validate_ranked(
            rows,
            query=query,
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            principal_id=context.principal_id,
        )
        current = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="corpus.search",
            capability="corpus.search",
            purpose="legal_research",
        )
        ephemeral_policy_fields = {"decision_id", "evaluated_at", "valid_until"}
        if current.model_dump(exclude=ephemeral_policy_fields) != decision.model_dump(
            exclude=ephemeral_policy_fields
        ):
            raise GovernedCorpusServiceError("access_denied")
        path = self._rank_path(query.mode)
        first_rank = 1 if cursor is None else cursor.last_rank + 1
        hits = tuple(
            GovernedCorpusHit(
                source=row.source,
                rank=RankEvidence(
                    rank=rank,
                    score=row.score,
                    full_text_rank=row.full_text_rank,
                    vector_distance=row.vector_distance,
                    rank_path=path,
                ),
            )
            for rank, row in enumerate(rows, start=first_rank)
        )
        if has_more and len(rows) != query.max_results:
            raise GovernedCorpusServiceError("internal_error")
        continuation_cursor = None
        if has_more:
            last = rows[-1]
            continuation_cursor = self._encode_cursor(
                _CursorPayload(
                    tenant_id=context.tenant_id,
                    matter_id=matter_id,
                    principal_id=context.principal_id,
                    authorization_context_sha256=(context.authorization_context_sha256),
                    policy_revision=decision.revision,
                    rights_revision=decision.rights_revision,
                    classification_ceiling=decision.classification_ceiling,
                    query_sha256=query_sha256,
                    release_id=projection.release_id,
                    projection_generation=projection.projection_generation,
                    backend_watermark=projection.backend_watermark,
                    core_watermark=projection.core_watermark,
                    mode=query.mode,
                    page_size=query.max_results,
                    snapshot_at=query.snapshot_at,
                    last_score_hex=last.score.hex(),
                    last_source_version_id=last.source.source_version_id,
                    last_rank=first_rank + len(rows) - 1,
                )
            )
        result = GovernedCorpusResult(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            query_sha256=query_sha256,
            authorization_decision_id=context.authorization_decision_id,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            rights_revision=decision.rights_revision,
            classification_ceiling=decision.classification_ceiling,
            projection=projection,
            mode=query.mode,
            hits=hits,
            no_answer=not hits,
            continuation_cursor=continuation_cursor,
        )
        return CorpusSearchRead(result=GovernedCorpusResultRead.from_result(result))

    @staticmethod
    def _validate_ranked(
        rows: tuple[RankedSource, ...],
        *,
        query: RetrievalQuery,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
    ) -> None:
        if len(rows) > query.max_results:
            raise GovernedCorpusServiceError("internal_error")
        source_versions: set[UUID] = set()
        prior_score = math.inf
        prior_source_version_id: UUID | None = None
        for row in rows:
            source = row.source
            if (
                source.source_version_id in source_versions
                or source.tenant_id != tenant_id
                or source.matter_id != matter_id
                or principal_id not in source.permitted_principal_ids
                or source.release_id != query.expected_release_id
                or source.projection_generation != query.expected_projection_generation
                or source.classification > query.classification_ceiling
                or source.rights_revision != query.rights_revision
                or not math.isfinite(row.score)
                or row.score > prior_score
                or (
                    row.score == prior_score
                    and prior_source_version_id is not None
                    and source.source_version_id <= prior_source_version_id
                )
                or (
                    query.after_score is not None
                    and (
                        row.score > query.after_score
                        or (
                            row.score == query.after_score
                            and query.after_source_version_id is not None
                            and source.source_version_id
                            <= query.after_source_version_id
                        )
                    )
                )
            ):
                raise GovernedCorpusServiceError("internal_error")
            source_versions.add(source.source_version_id)
            prior_score = row.score
            prior_source_version_id = source.source_version_id

    def read_span(
        self,
        *,
        context: GovernedCorpusAccessContext,
        matter_id: UUID,
        command: CorpusSpanCommand,
    ) -> CorpusSpanRead:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="corpus.span.read",
            capability="corpus.artifact.read",
            purpose="legal_research",
        )
        projection = self._projection(
            context=context,
            expected_release_id=command.expected_release_id,
            expected_generation=command.expected_projection_generation,
            required_watermark=command.required_core_watermark,
            decision=decision,
            verify_policy=False,
        )
        try:
            source = self._core_repository.get_current_source(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                principal_id=context.principal_id,
                source_id=command.source_id,
                classification_ceiling=decision.classification_ceiling,
                rights_revision=decision.rights_revision,
                release_id=command.expected_release_id,
                projection_generation=command.expected_projection_generation,
            )
        except Exception:
            raise GovernedCorpusServiceError("resource_unavailable") from None
        if source is None:
            raise GovernedCorpusServiceError("not_found")
        return CorpusSpanRead(
            source=CorpusSourceRead.from_source(source), projection=projection
        )

    def record_source(
        self,
        *,
        context: GovernedCorpusAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: RecordCorpusSourceCommand,
    ) -> CorpusSourceMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="corpus.source.record",
            capability="corpus.ingest.submit",
            purpose="corpus_ingestion",
        )
        self._write_projection(
            context=context,
            expected_release_id=command.release_id,
            expected_generation=command.projection_generation,
            decision=decision,
        )
        if command.classification > decision.classification_ceiling:
            raise GovernedCorpusServiceError("access_denied")
        now = self._clock()
        source = CorpusSourceVersion(
            **command.model_dump(
                exclude={"schema_version"}, mode="python", by_alias=False
            ),
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            rights_revision=decision.rights_revision,
            recorded_at=now,
            recorded_by_principal_id=context.principal_id,
            authorization_decision_id=context.authorization_decision_id,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
        )
        request_sha256 = canonical_sha256(
            {
                "operation": "corpus.source.record",
                "matter_id": str(matter_id),
                "command": command.model_dump(mode="json", by_alias=True),
            }
        )
        key_sha256 = _key_sha256(idempotency_key)
        correlation_id = _uuid("corpus-correlation", key_sha256, request_sha256)
        audit_id = _uuid("corpus-audit", source.source_version_id, request_sha256)
        outbox_id = _uuid("corpus-outbox", source.source_version_id, request_sha256)
        source_record_sha256 = canonical_sha256(source)
        audit = CorpusAuditEvent(
            audit_id=audit_id,
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            source_version_id=source.source_version_id,
            action=(
                "corpus.source.superseded"
                if source.supersedes_source_version_id is not None
                else "corpus.source.recorded"
            ),
            actor_principal_id=context.principal_id,
            authorization_decision_id=context.authorization_decision_id,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            request_sha256=request_sha256,
            source_sha256=source_record_sha256,
            correlation_id=correlation_id,
            occurred_at=now,
        )
        outbox = CorpusOutboxRecord(
            outbox_id=outbox_id,
            audit_id=audit_id,
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            source_version_id=source.source_version_id,
            payload_sha256=source_record_sha256,
            created_at=now,
        )
        self._authorize(
            context=context,
            matter_id=matter_id,
            operation="corpus.source.record",
            capability="corpus.ingest.submit",
            purpose="corpus_ingestion",
        )
        try:
            committed = self._core_repository.commit_source(
                idempotency_key_sha256=key_sha256,
                request_sha256=request_sha256,
                source=source,
                audit=audit,
                outbox=outbox,
            )
        except GovernedCorpusIdempotencyConflict:
            raise GovernedCorpusServiceError("idempotency_conflict") from None
        except GovernedCorpusVersionConflict:
            raise GovernedCorpusServiceError("version_conflict") from None
        except GovernedCorpusRepositoryUnavailable:
            raise GovernedCorpusServiceError("resource_unavailable") from None
        except Exception:
            raise GovernedCorpusServiceError("internal_error") from None
        return CorpusSourceMutationReceipt(
            source=committed,
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            audit_id=audit_id,
            outbox_id=outbox_id,
            correlation_id=correlation_id,
        )


__all__ = [
    "GovernedCorpusAccessContext",
    "GovernedCorpusPolicy",
    "GovernedCorpusPolicyDecision",
    "GovernedCorpusService",
    "GovernedCorpusServiceError",
    "StaticGovernedCorpusPolicy",
]
