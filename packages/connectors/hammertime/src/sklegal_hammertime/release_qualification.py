"""Fail-closed qualification for an official drafting corpus release.

This module evaluates immutable evidence through the read-only HammerTime
adapter. It never promotes or rolls back an alias. HammerTime remains the
owner of those mutations; this verifier only proves that their pinned
receipts describe a safe dev round trip.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator
from sklegal_domain.value_objects import (
    FrozenValue,
    NonEmptyText,
    Sha256,
    ShortText,
    UtcDateTime,
)

from .adapter import HammerTimeReleaseAdapter
from .errors import HammerTimeAdapterError
from .frontmatter import split_frontmatter
from .models import AliasTarget, DecompositionId, RelativePosixPath, ReleaseId


class SourceRightsState(StrEnum):
    """Closed source-rights outcome for one release source."""

    VERIFIED = "verified"
    QUARANTINED = "quarantined"


class QualificationFindingCode(StrEnum):
    """Stable reason codes emitted by release qualification."""

    MISSING_ARTIFACT = "missing_artifact"
    WRONG_SOURCE_VERSION = "wrong_source_version"
    STALE_PROJECTION = "stale_projection"
    SCOPE_COLLAPSE = "scope_collapse"
    SUPERSEDED_AS_CURRENT = "superseded_as_current"
    ALIAS_DRIFT = "alias_drift"
    PROMOTION_FAILURE = "promotion_failure"
    ROLLBACK_FAILURE = "rollback_failure"
    SOURCE_RIGHTS_BLOCKED = "source_rights_blocked"
    SECONDARY_REVIEW_FAILED = "secondary_review_failed"
    RELEASE_MISMATCH = "release_mismatch"


class QualificationStatus(StrEnum):
    QUALIFIED = "qualified"
    BLOCKED = "blocked"


class OfficialDraftingSource(FrozenValue):
    """Exact source and derived-artifact expectation for the candidate."""

    source_id: NonEmptyText
    issuing_body: NonEmptyText
    version: NonEmptyText
    scope: NonEmptyText
    normalized_path: RelativePosixPath
    source_sha256: Sha256
    normalized_sha256: Sha256
    decomposition_id: DecompositionId
    decomposition_sha256: Sha256
    rights_state: SourceRightsState
    superseded: bool = False


class RetrievalHitEvidence(FrozenValue):
    """One pinned result returned by a scoped retrieval check."""

    source_id: NonEmptyText
    issuing_body: NonEmptyText
    version: NonEmptyText
    scope: NonEmptyText
    source_sha256: Sha256
    locator: NonEmptyText
    presented_as_current: bool


class ScopedRetrievalEvidence(FrozenValue):
    """Expected and observed sources for one bounded retrieval query."""

    query_id: NonEmptyText
    expected_source_ids: tuple[NonEmptyText, ...]
    conflict_source_ids: tuple[NonEmptyText, ...] = ()
    hits: tuple[RetrievalHitEvidence, ...]

    @model_validator(mode="after")
    def validate_unique_source_sets(self) -> ScopedRetrievalEvidence:
        for values in (self.expected_source_ids, self.conflict_source_ids):
            if len(values) != len(set(values)):
                raise ValueError("retrieval source identifiers must be unique")
        return self


class ProjectionQualificationEvidence(FrozenValue):
    """Pinned lexical, vector, and graph projection evidence."""

    release_id: ReleaseId
    release_manifest_sha256: Sha256
    lexical_release_id: ReleaseId
    vector_release_id: ReleaseId
    graph_release_id: ReleaseId
    deep_health_passed: bool
    reconciliation_complete: bool
    stale: bool = False


class SecondaryReviewEvidence(FrozenValue):
    """Pinned secondary local Qwen review result."""

    logical_route: NonEmptyText
    served_model: NonEmptyText
    prompt_sha256: Sha256
    output_sha256: Sha256
    reviewed_at: UtcDateTime
    passed: bool


class AliasRoundTripEvidence(FrozenValue):
    """Immutable evidence for guarded promotion and tested rollback."""

    target: AliasTarget
    prior_current_release_id: ReleaseId
    promoted_release_id: ReleaseId
    promoted_previous_release_id: ReleaseId
    promotion_succeeded: bool
    rollback_succeeded: bool
    rollback_current_release_id: ReleaseId
    rollback_previous_release_id: ReleaseId


class OfficialDraftingReleaseRequest(FrozenValue):
    """All evidence required to qualify one bounded release."""

    release_id: ReleaseId
    release_manifest_sha256: Sha256
    target: AliasTarget
    sources: tuple[OfficialDraftingSource, ...]
    retrieval_checks: tuple[ScopedRetrievalEvidence, ...]
    projection: ProjectionQualificationEvidence
    secondary_review: SecondaryReviewEvidence
    alias_round_trip: AliasRoundTripEvidence

    @model_validator(mode="after")
    def validate_unique_sources(self) -> OfficialDraftingReleaseRequest:
        source_ids = [source.source_id for source in self.sources]
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("release sources must be non-empty and unique")
        return self


class QualificationFinding(FrozenValue):
    code: QualificationFindingCode
    subject: NonEmptyText
    detail: ShortText


class OfficialDraftingReleaseReport(FrozenValue):
    """Content-free verdict with immutable references and typed findings."""

    status: QualificationStatus
    release_id: ReleaseId
    release_manifest_sha256: Sha256
    aliases_sha256: Sha256 | None = None
    verified_artifact_sha256: tuple[Sha256, ...] = ()
    findings: tuple[QualificationFinding, ...] = ()

    @model_validator(mode="after")
    def validate_verdict(self) -> OfficialDraftingReleaseReport:
        if (self.status is QualificationStatus.QUALIFIED) == bool(self.findings):
            raise ValueError("qualified reports have no findings; blocked reports do")
        return self


class OfficialDraftingReleaseQualifier:
    """Verify a release without exposing a HammerTime mutation surface."""

    def __init__(self, adapter: HammerTimeReleaseAdapter) -> None:
        if not isinstance(adapter, HammerTimeReleaseAdapter):
            raise ValueError("qualification requires the read-only HammerTime adapter")
        self._adapter = adapter

    def qualify(
        self, request: OfficialDraftingReleaseRequest
    ) -> OfficialDraftingReleaseReport:
        findings: list[QualificationFinding] = []
        verified_hashes: list[str] = []
        aliases_sha256: str | None = None

        try:
            manifest = self._adapter.get_release_manifest(request.release_id)
        except HammerTimeAdapterError as exc:
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                request.release_id,
                str(exc),
            )
            return self._report(request, findings, verified_hashes, aliases_sha256)

        if (
            manifest.pin.content_sha256 != request.release_manifest_sha256
            or manifest.release_target != request.target
        ):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                request.release_id,
                "candidate release manifest hash or target differs from evidence",
            )

        manifest_documents = {
            path for paths in manifest.documents.values() for path in paths
        }
        manifest_counts = {
            "new": manifest.document_counts.new,
            "changed": manifest.document_counts.changed,
            "deleted": manifest.document_counts.deleted,
            "unchanged": manifest.document_counts.unchanged,
        }
        verification_passed = (
            manifest.verification.get("qdrant_collection_ok") is True
            and manifest.verification.get("graph_rebuild_ok") is True
        )
        if (
            any(
                manifest_counts[kind] != len(manifest.documents.get(kind, []))
                for kind in manifest_counts
            )
            or not verification_passed
        ):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                request.release_id,
                "release counts or deterministic verification do not pass",
            )
        if (
            manifest.decomposed_snapshot is None
            or manifest.decomposed_snapshot.get("file_count", 0) < len(request.sources)
            or not manifest.decomposed_snapshot.get("snapshot_hash")
        ):
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                request.release_id,
                "release does not seal every expected decomposition",
            )
        source_by_id = {source.source_id: source for source in request.sources}
        for source in request.sources:
            if source.rights_state is not SourceRightsState.VERIFIED:
                self._add(
                    findings,
                    QualificationFindingCode.SOURCE_RIGHTS_BLOCKED,
                    source.source_id,
                    "source rights are not verified for this release",
                )
            if source.normalized_path not in manifest_documents:
                self._add(
                    findings,
                    QualificationFindingCode.MISSING_ARTIFACT,
                    source.source_id,
                    "normalized source is absent from the candidate manifest",
                )
            try:
                normalized = self._adapter.read_artifact(
                    source.normalized_path,
                    expected_sha256=source.normalized_sha256,
                )
                frontmatter, _ = split_frontmatter(
                    normalized.text,
                    relative_path=source.normalized_path,
                )
                decomposition = self._adapter.get_decomposition(
                    source.decomposition_id,
                    expected_sha256=source.decomposition_sha256,
                )
                verified_hashes.extend(
                    (normalized.pin.content_sha256, decomposition.pin.content_sha256)
                )
                if frontmatter.get("source_sha256") != source.source_sha256:
                    self._add(
                        findings,
                        QualificationFindingCode.WRONG_SOURCE_VERSION,
                        source.source_id,
                        "normalized source metadata differs from the sealed source hash",
                    )
                if decomposition.source_file != source.normalized_path:
                    self._add(
                        findings,
                        QualificationFindingCode.RELEASE_MISMATCH,
                        source.source_id,
                        "decomposition source does not match the normalized source",
                    )
                if (
                    decomposition.frontmatter.get("source_sha256")
                    != source.source_sha256
                ):
                    self._add(
                        findings,
                        QualificationFindingCode.RELEASE_MISMATCH,
                        source.source_id,
                        "decomposition source hash differs from the sealed source",
                    )
            except HammerTimeAdapterError as exc:
                self._add(
                    findings,
                    QualificationFindingCode.MISSING_ARTIFACT,
                    source.source_id,
                    str(exc),
                )

        self._check_retrieval(request, source_by_id, findings)
        self._check_projection(request, findings)
        self._check_review(request, findings)

        try:
            resolved = self._adapter.resolve_current_release(request.target)
            aliases_sha256 = resolved.aliases_pin.content_sha256
            if resolved.drift:
                self._add(
                    findings,
                    QualificationFindingCode.ALIAS_DRIFT,
                    request.target,
                    "; ".join(resolved.drift),
                )
            if (
                resolved.manifest.release_id != request.release_id
                or resolved.alias.previous is None
                or resolved.alias.previous.release_id
                != request.alias_round_trip.prior_current_release_id
            ):
                self._add(
                    findings,
                    QualificationFindingCode.ALIAS_DRIFT,
                    request.target,
                    "current or previous alias does not match promotion evidence",
                )
        except HammerTimeAdapterError as exc:
            self._add(
                findings,
                QualificationFindingCode.ALIAS_DRIFT,
                request.target,
                str(exc),
            )

        self._check_round_trip(request, findings)
        return self._report(request, findings, verified_hashes, aliases_sha256)

    @staticmethod
    def _check_retrieval(
        request: OfficialDraftingReleaseRequest,
        source_by_id: dict[str, OfficialDraftingSource],
        findings: list[QualificationFinding],
    ) -> None:
        for check in request.retrieval_checks:
            hit_ids = {hit.source_id for hit in check.hits}
            if not set(check.expected_source_ids).issubset(hit_ids):
                OfficialDraftingReleaseQualifier._add(
                    findings,
                    QualificationFindingCode.MISSING_ARTIFACT,
                    check.query_id,
                    "targeted retrieval omitted an expected source",
                )
            if not set(check.conflict_source_ids).issubset(hit_ids):
                OfficialDraftingReleaseQualifier._add(
                    findings,
                    QualificationFindingCode.SCOPE_COLLAPSE,
                    check.query_id,
                    "targeted retrieval collapsed a preserved source conflict",
                )
            for hit in check.hits:
                expected = source_by_id.get(hit.source_id)
                if expected is None:
                    continue
                if (
                    hit.issuing_body != expected.issuing_body
                    or hit.version != expected.version
                    or hit.source_sha256 != expected.source_sha256
                ):
                    OfficialDraftingReleaseQualifier._add(
                        findings,
                        QualificationFindingCode.WRONG_SOURCE_VERSION,
                        check.query_id,
                        f"retrieval metadata differs for source {hit.source_id}",
                    )
                if hit.scope != expected.scope:
                    OfficialDraftingReleaseQualifier._add(
                        findings,
                        QualificationFindingCode.SCOPE_COLLAPSE,
                        check.query_id,
                        f"retrieval scope differs for source {hit.source_id}",
                    )
                if expected.superseded and hit.presented_as_current:
                    OfficialDraftingReleaseQualifier._add(
                        findings,
                        QualificationFindingCode.SUPERSEDED_AS_CURRENT,
                        check.query_id,
                        f"superseded source {hit.source_id} was presented as current",
                    )

    @staticmethod
    def _check_projection(
        request: OfficialDraftingReleaseRequest,
        findings: list[QualificationFinding],
    ) -> None:
        projection = request.projection
        release_ids = {
            projection.release_id,
            projection.lexical_release_id,
            projection.vector_release_id,
            projection.graph_release_id,
        }
        if (
            projection.stale
            or not projection.deep_health_passed
            or not projection.reconciliation_complete
            or release_ids != {request.release_id}
            or projection.release_manifest_sha256 != request.release_manifest_sha256
        ):
            OfficialDraftingReleaseQualifier._add(
                findings,
                QualificationFindingCode.STALE_PROJECTION,
                request.release_id,
                "lexical, vector, or graph projection is not pinned to the candidate",
            )

    @staticmethod
    def _check_review(
        request: OfficialDraftingReleaseRequest,
        findings: list[QualificationFinding],
    ) -> None:
        review = request.secondary_review
        if not review.passed or "qwen3.8" not in review.served_model.lower():
            OfficialDraftingReleaseQualifier._add(
                findings,
                QualificationFindingCode.SECONDARY_REVIEW_FAILED,
                request.release_id,
                "secondary local Qwen3.8 review did not pass",
            )

    @staticmethod
    def _check_round_trip(
        request: OfficialDraftingReleaseRequest,
        findings: list[QualificationFinding],
    ) -> None:
        evidence = request.alias_round_trip
        if (
            not evidence.promotion_succeeded
            or evidence.target != request.target
            or evidence.promoted_release_id != request.release_id
            or evidence.promoted_previous_release_id
            != evidence.prior_current_release_id
        ):
            OfficialDraftingReleaseQualifier._add(
                findings,
                QualificationFindingCode.PROMOTION_FAILURE,
                request.target,
                "guarded promotion receipt does not preserve the prior alias",
            )
        if (
            not evidence.rollback_succeeded
            or evidence.rollback_current_release_id != evidence.prior_current_release_id
            or evidence.rollback_previous_release_id != request.release_id
        ):
            OfficialDraftingReleaseQualifier._add(
                findings,
                QualificationFindingCode.ROLLBACK_FAILURE,
                request.target,
                "rollback receipt does not prove the candidate and prior alias swap",
            )

    @staticmethod
    def _add(
        findings: list[QualificationFinding],
        code: QualificationFindingCode,
        subject: str,
        detail: str,
    ) -> None:
        findings.append(
            QualificationFinding(code=code, subject=subject, detail=detail[:512])
        )

    @staticmethod
    def _report(
        request: OfficialDraftingReleaseRequest,
        findings: list[QualificationFinding],
        verified_hashes: list[str],
        aliases_sha256: str | None,
    ) -> OfficialDraftingReleaseReport:
        return OfficialDraftingReleaseReport(
            status=(
                QualificationStatus.BLOCKED
                if findings
                else QualificationStatus.QUALIFIED
            ),
            release_id=request.release_id,
            release_manifest_sha256=request.release_manifest_sha256,
            aliases_sha256=aliases_sha256,
            verified_artifact_sha256=tuple(dict.fromkeys(verified_hashes)),
            findings=tuple(findings),
        )


__all__ = [
    "AliasRoundTripEvidence",
    "OfficialDraftingReleaseQualifier",
    "OfficialDraftingReleaseReport",
    "OfficialDraftingReleaseRequest",
    "OfficialDraftingSource",
    "ProjectionQualificationEvidence",
    "QualificationFinding",
    "QualificationFindingCode",
    "QualificationStatus",
    "RetrievalHitEvidence",
    "ScopedRetrievalEvidence",
    "SecondaryReviewEvidence",
    "SourceRightsState",
]
