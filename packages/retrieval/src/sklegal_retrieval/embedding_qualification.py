"""Approved embedding qualification thresholds and the deterministic verdict.

This module implements the SKL-S3-04C slice of SKL-S3-04. It loads the
approved, versioned threshold set that pins the SKL-S3-04B comparison
harness to one dataset freeze, applies every bound deterministically to a
``ShadowComparisonReport``, and emits a verdict that cites the exact
measured value and the exact bound for every check on every route.

The verdict never promotes a model by itself. It records which route met
the approved bounds and which route did not, and the serving alias layer
in ``embedding_alias.py`` consumes the verdict to bind, roll back, or fail
closed. Fixture embedder waivers are explicit, reasoned, and recorded in
the verdict; leakage tolerates exactly zero events and is never waivable.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from .errors import RetrievalIntegrityError
from .models import OpaqueId, RetrievalValue, SafeVersion, Sha256
from .shadow_comparison import (
    FIXTURE_EMBEDDER_KIND,
    ShadowComparisonReport,
    ShadowRouteLabel,
    ShadowRouteReport,
)

THRESHOLDS_SCHEMA = "sklegal-embedding-qualification-thresholds/v1"
THRESHOLDS_VERSION = "1.0.0"
THRESHOLDS_FILE_NAME = "embedding-qualification-thresholds.json"
THRESHOLDS_DEFAULT_PATH = Path("config/retrieval") / THRESHOLDS_FILE_NAME

VERDICT_VERSION = "1.0.0"

#: Metrics a fixture embedder waiver may cover. Leakage is never waivable
#: and every other bound binds fixture routes exactly as it binds real
#: embedders, so the waivable set stays intentionally tiny.
WAIVABLE_METRICS = frozenset({"citation_accuracy"})

_IsoDate = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}-\d{2}-\d{2}$")
]


class EmbeddingQualificationError(RetrievalIntegrityError):
    """A threshold set or qualification verdict failed its gates."""


class FixtureEmbedderWaiver(RetrievalValue):
    """One explicit waiver carrying the floor that still binds real embedders."""

    metric: Literal["citation_accuracy"]
    applies_to_embedder_kind: str = Field(min_length=1, max_length=64)
    enforced_value_for_real_embedders: float
    reason: str = Field(min_length=16, max_length=2048)

    @model_validator(mode="after")
    def validate_waiver_shape(self) -> Self:
        if self.metric not in WAIVABLE_METRICS:
            raise ValueError(f"metric {self.metric} cannot be waived")
        if not 0.0 <= self.enforced_value_for_real_embedders <= 1.0:
            raise ValueError("the enforced real-embedder floor must lie in [0, 1]")
        return self


class QualificationThresholds(RetrievalValue):
    """The approved, versioned bound set pinned to one dataset freeze."""

    model_config = ConfigDict(populate_by_name=True)

    thresholds_schema: Literal[THRESHOLDS_SCHEMA] = Field(
        default=THRESHOLDS_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    card: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=64)
    parent_task: str = Field(min_length=1, max_length=64)
    amendment: str = Field(min_length=1, max_length=64)
    thresholds_version: SafeVersion = THRESHOLDS_VERSION
    approved_at: _IsoDate
    approval_basis: str = Field(min_length=1, max_length=2048)
    dataset_freeze_sha256: Sha256
    harness_version: SafeVersion
    k_values: tuple[int, ...] = Field(min_length=1)
    recall_at_k_minimum: tuple[float, ...]
    ndcg_at_k_minimum: tuple[float, ...]
    mrr_minimum: float
    citation_accuracy_minimum: float
    macro_latency_mean_maximum_seconds: float
    max_latency_maximum_seconds: float
    maximum_leakage_count: Literal[0] = 0
    fixture_embedder_waivers: tuple[FixtureEmbedderWaiver, ...] = ()
    notes: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if list(self.k_values) != sorted(set(self.k_values)) or not all(
            value >= 1 for value in self.k_values
        ):
            raise ValueError("k values must be unique positive integers")
        width = len(self.k_values)
        if (
            len(self.recall_at_k_minimum) != width
            or len(self.ndcg_at_k_minimum) != width
        ):
            raise ValueError("metric bounds must align with the pinned k values")
        for value in (
            *self.recall_at_k_minimum,
            *self.ndcg_at_k_minimum,
            self.mrr_minimum,
            self.citation_accuracy_minimum,
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError("ranking bounds must lie in [0, 1]")
        if self.macro_latency_mean_maximum_seconds <= 0.0:
            raise ValueError("the macro latency budget must be positive")
        if self.max_latency_maximum_seconds < self.macro_latency_mean_maximum_seconds:
            raise ValueError("the maximum latency budget cannot undercut the mean")
        waiver_metrics = [waiver.metric for waiver in self.fixture_embedder_waivers]
        if len(waiver_metrics) != len(set(waiver_metrics)):
            raise ValueError("each metric carries at most one fixture waiver")
        for waiver in self.fixture_embedder_waivers:
            if waiver.applies_to_embedder_kind != FIXTURE_EMBEDDER_KIND:
                raise ValueError(
                    "a waiver may only cover the deterministic fixture embedder kind"
                )
            enforced = {
                "citation_accuracy": self.citation_accuracy_minimum,
            }[waiver.metric]
            if waiver.enforced_value_for_real_embedders != enforced:
                raise ValueError(
                    "the enforced real-embedder floor must equal the bound it defers"
                )
        return self


class ThresholdSetFile(RetrievalValue):
    """One loaded threshold file pinned to its exact bytes."""

    thresholds: QualificationThresholds
    file_sha256: Sha256


def load_qualification_thresholds(path: Path) -> ThresholdSetFile:
    """Load and validate one approved threshold file from its exact bytes."""

    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise EmbeddingQualificationError(
            f"unable to read the qualification thresholds at {path}"
        ) from error
    try:
        thresholds = TypeAdapter(QualificationThresholds).validate_json(payload)
    except ValueError as error:
        raise EmbeddingQualificationError(
            f"qualification thresholds failed schema validation: {error}"
        ) from error
    return ThresholdSetFile(
        thresholds=thresholds,
        file_sha256=hashlib.sha256(payload).hexdigest(),
    )


class CheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WAIVED = "waived"


class QualificationDecision(StrEnum):
    CUSTOM_QUALIFIED = "custom_qualified"
    ROLLBACK_TO_BASE = "rollback_to_base"
    FAIL_CLOSED = "fail_closed"


class MetricCheck(RetrievalValue):
    """One measured metric against its approved bound, cited exactly."""

    metric: str = Field(min_length=3, max_length=64)
    operator: Literal[">=", "<=", "=="]
    measured: str = Field(min_length=1, max_length=64)
    bound: str = Field(min_length=1, max_length=64)
    outcome: CheckOutcome
    waiver_reason: str | None = Field(default=None, min_length=16, max_length=2048)

    @model_validator(mode="after")
    def validate_waiver_pairing(self) -> Self:
        waived = self.outcome is CheckOutcome.WAIVED
        if waived != (self.waiver_reason is not None):
            raise ValueError("exactly a waived check records its waiver reason")
        return self


class RouteQualificationVerdict(RetrievalValue):
    """One route's verdict with every check citation."""

    route_label: ShadowRouteLabel
    embedding_model_id: OpaqueId
    embedding_model_revision: SafeVersion
    embedder_kind: str = Field(min_length=1, max_length=64)
    projection_set_id: UUID
    projection_generation: int = Field(ge=1)
    qualified: bool
    checks: tuple[MetricCheck, ...] = Field(min_length=1)
    failed_checks: tuple[MetricCheck, ...]
    waived_checks: tuple[MetricCheck, ...]

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        metrics = [check.metric for check in self.checks]
        if len(metrics) != len(set(metrics)):
            raise ValueError("each metric appears once per route verdict")
        failed = tuple(
            check for check in self.checks if check.outcome is CheckOutcome.FAILED
        )
        waived = tuple(
            check for check in self.checks if check.outcome is CheckOutcome.WAIVED
        )
        if failed != self.failed_checks or waived != self.waived_checks:
            raise ValueError("recorded check outcomes must match the checks")
        if self.qualified != (not failed):
            raise ValueError(
                "a route qualifies exactly when no check fails, waivers included"
            )
        return self


class EmbeddingQualificationVerdict(RetrievalValue):
    """The overall deterministic verdict over one comparison report."""

    verdict_version: SafeVersion = VERDICT_VERSION
    harness_version: SafeVersion
    thresholds_sha256: Sha256
    thresholds_version: SafeVersion
    dataset_freeze_sha256: Sha256
    k_values: tuple[int, ...] = Field(min_length=1)
    routes: tuple[RouteQualificationVerdict, ...] = Field(min_length=2, max_length=2)
    custom_route_label: ShadowRouteLabel = ShadowRouteLabel.CUSTOM_LEGAL
    base_route_label: ShadowRouteLabel = ShadowRouteLabel.BASE_BGE_M3
    decision: QualificationDecision

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        labels = [verdict.route_label for verdict in self.routes]
        if len(labels) != len(set(labels)):
            raise ValueError("each embedding route appears once per verdict")
        if self.custom_route_label not in labels or self.base_route_label not in labels:
            raise ValueError("the verdict needs one custom and one base route")
        if list(self.k_values) != sorted(set(self.k_values)):
            raise ValueError("verdict k values must be unique")
        by_label = {verdict.route_label: verdict for verdict in self.routes}
        custom_qualified = by_label[self.custom_route_label].qualified
        base_qualified = by_label[self.base_route_label].qualified
        expected = (
            QualificationDecision.CUSTOM_QUALIFIED
            if custom_qualified
            else (
                QualificationDecision.ROLLBACK_TO_BASE
                if base_qualified
                else QualificationDecision.FAIL_CLOSED
            )
        )
        if self.decision is not expected:
            raise ValueError(
                f"decision {self.decision.value} disagrees with the route verdicts"
            )
        return self

    def route_verdict(self, label: ShadowRouteLabel) -> RouteQualificationVerdict:
        """Return one route's verdict by label."""

        for verdict in self.routes:
            if verdict.route_label is label:
                return verdict
        raise EmbeddingQualificationError(
            "the verdict carries no route verdict for that label"
        )


def _format_ratio(value: float) -> str:
    return f"{value:.6f}"


def _format_seconds(value: float) -> str:
    return f"{value:.6f}"


def _ratio_check(metric: str, measured: float, minimum: float) -> MetricCheck:
    return MetricCheck(
        metric=metric,
        operator=">=",
        measured=_format_ratio(measured),
        bound=_format_ratio(minimum),
        outcome=(CheckOutcome.PASSED if measured >= minimum else CheckOutcome.FAILED),
    )


def _seconds_check(metric: str, measured: float, maximum: float) -> MetricCheck:
    return MetricCheck(
        metric=metric,
        operator="<=",
        measured=_format_seconds(measured),
        bound=_format_seconds(maximum),
        outcome=CheckOutcome.PASSED if measured <= maximum else CheckOutcome.FAILED,
    )


def _leakage_check(measured: int) -> MetricCheck:
    return MetricCheck(
        metric="leakage_count",
        operator="==",
        measured=str(measured),
        bound="0",
        outcome=CheckOutcome.PASSED if measured == 0 else CheckOutcome.FAILED,
    )


def _citation_check(
    route: ShadowRouteReport, measured: float, minimum: float, waivers
) -> MetricCheck:
    for waiver in waivers:
        if route.route.embedder_kind == waiver.applies_to_embedder_kind:
            return MetricCheck(
                metric="citation_accuracy",
                operator=">=",
                measured=_format_ratio(measured),
                bound=_format_ratio(minimum),
                outcome=CheckOutcome.WAIVED,
                waiver_reason=waiver.reason,
            )
    return _ratio_check("citation_accuracy", measured, minimum)


def _route_verdict(
    route: ShadowRouteReport, thresholds: QualificationThresholds
) -> RouteQualificationVerdict:
    checks: list[MetricCheck] = []
    for k, measured, minimum in zip(
        thresholds.k_values,
        route.macro_recall_at_k,
        thresholds.recall_at_k_minimum,
        strict=True,
    ):
        checks.append(_ratio_check(f"recall@{k}", measured, minimum))
    for k, measured, minimum in zip(
        thresholds.k_values,
        route.macro_ndcg_at_k,
        thresholds.ndcg_at_k_minimum,
        strict=True,
    ):
        checks.append(_ratio_check(f"ndcg@{k}", measured, minimum))
    checks.append(_ratio_check("mrr", route.macro_mrr, thresholds.mrr_minimum))
    checks.append(
        _citation_check(
            route,
            route.macro_citation_accuracy,
            thresholds.citation_accuracy_minimum,
            thresholds.fixture_embedder_waivers,
        )
    )
    checks.append(
        _seconds_check(
            "macro_latency_mean_seconds",
            route.macro_latency_mean_seconds,
            thresholds.macro_latency_mean_maximum_seconds,
        )
    )
    checks.append(
        _seconds_check(
            "max_latency_seconds",
            route.max_latency_seconds,
            thresholds.max_latency_maximum_seconds,
        )
    )
    checks.append(_leakage_check(route.leakage_count))
    verdict = RouteQualificationVerdict(
        route_label=route.route.label,
        embedding_model_id=route.route.vector.embedding_model_id,
        embedding_model_revision=route.route.vector.embedding_model_revision,
        embedder_kind=route.route.embedder_kind,
        projection_set_id=route.projection_set_id,
        projection_generation=route.projection_generation,
        qualified=not any(check.outcome is CheckOutcome.FAILED for check in checks),
        checks=tuple(checks),
        failed_checks=tuple(
            check for check in checks if check.outcome is CheckOutcome.FAILED
        ),
        waived_checks=tuple(
            check for check in checks if check.outcome is CheckOutcome.WAIVED
        ),
    )
    return verdict


def _decision_for(
    custom: RouteQualificationVerdict, base: RouteQualificationVerdict
) -> QualificationDecision:
    if custom.qualified:
        return QualificationDecision.CUSTOM_QUALIFIED
    if base.qualified:
        return QualificationDecision.ROLLBACK_TO_BASE
    return QualificationDecision.FAIL_CLOSED


def apply_qualification_thresholds(
    report: ShadowComparisonReport, thresholds: ThresholdSetFile
) -> EmbeddingQualificationVerdict:
    """Apply every approved bound to one comparison report, citing each metric."""

    pinned = thresholds.thresholds
    if report.dataset_freeze_sha256 != pinned.dataset_freeze_sha256:
        raise EmbeddingQualificationError(
            "the comparison freeze does not match the approved threshold freeze"
        )
    if report.harness_version != pinned.harness_version:
        raise EmbeddingQualificationError(
            "the comparison harness version does not match the approved thresholds"
        )
    if report.k_values != pinned.k_values:
        raise EmbeddingQualificationError(
            "the comparison k values do not match the approved thresholds"
        )
    by_label: dict[ShadowRouteLabel, ShadowRouteReport] = {}
    for route in report.routes:
        if route.route.label in by_label:
            raise EmbeddingQualificationError(
                "the comparison repeats one embedding route"
            )
        by_label[route.route.label] = route
    for label in (ShadowRouteLabel.CUSTOM_LEGAL, ShadowRouteLabel.BASE_BGE_M3):
        if label not in by_label:
            raise EmbeddingQualificationError(
                f"the comparison is missing the {label.value} route"
            )
    custom = _route_verdict(by_label[ShadowRouteLabel.CUSTOM_LEGAL], pinned)
    base = _route_verdict(by_label[ShadowRouteLabel.BASE_BGE_M3], pinned)
    return EmbeddingQualificationVerdict(
        harness_version=report.harness_version,
        thresholds_sha256=thresholds.file_sha256,
        thresholds_version=pinned.thresholds_version,
        dataset_freeze_sha256=report.dataset_freeze_sha256,
        k_values=report.k_values,
        routes=(custom, base),
        decision=_decision_for(custom, base),
    )


__all__ = [
    "CheckOutcome",
    "EmbeddingQualificationError",
    "EmbeddingQualificationVerdict",
    "FixtureEmbedderWaiver",
    "MetricCheck",
    "QualificationDecision",
    "QualificationThresholds",
    "RouteQualificationVerdict",
    "THRESHOLDS_DEFAULT_PATH",
    "THRESHOLDS_FILE_NAME",
    "THRESHOLDS_SCHEMA",
    "THRESHOLDS_VERSION",
    "ThresholdSetFile",
    "VERDICT_VERSION",
    "WAIVABLE_METRICS",
    "apply_qualification_thresholds",
    "load_qualification_thresholds",
]
