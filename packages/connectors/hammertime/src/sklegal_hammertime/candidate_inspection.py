"""Bounded inspection of an immutable official drafting release candidate.

The inspector follows only paths pinned by one release manifest. It provides a
fast deterministic gate before live retrieval, deep health, secondary review,
promotion, and rollback. It has no mutation surface and never lists the corpus.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from pydantic import model_validator
from sklegal_domain.value_objects import FrozenValue, Sha256

from .adapter import HammerTimeReleaseAdapter
from .errors import HammerTimeAdapterError
from .frontmatter import split_frontmatter
from .models import AliasTarget, ReleaseId
from .release_qualification import (
    QualificationFinding,
    QualificationFindingCode,
    QualificationStatus,
)


class OfficialDraftingCandidateReport(FrozenValue):
    """Content-free result from following one candidate's explicit paths."""

    status: QualificationStatus
    release_id: ReleaseId
    release_manifest_sha256: Sha256
    source_count: int
    decomposition_count: int
    verified_artifact_sha256: tuple[Sha256, ...] = ()
    findings: tuple[QualificationFinding, ...] = ()

    @model_validator(mode="after")
    def validate_verdict(self) -> OfficialDraftingCandidateReport:
        if (self.status is QualificationStatus.QUALIFIED) == bool(self.findings):
            raise ValueError("qualified reports have no findings; blocked reports do")
        return self


class OfficialDraftingCandidateInspector:
    """Inspect only the immutable artifacts named by one release manifest."""

    def __init__(self, adapter: HammerTimeReleaseAdapter) -> None:
        if not isinstance(adapter, HammerTimeReleaseAdapter):
            raise ValueError("candidate inspection requires the read-only adapter")
        self._adapter = adapter

    def inspect(
        self,
        *,
        release_id: ReleaseId,
        expected_manifest_sha256: Sha256,
        expected_target: AliasTarget,
        expected_source_count: int,
    ) -> OfficialDraftingCandidateReport:
        if expected_source_count < 1:
            raise ValueError("expected_source_count must be positive")
        findings: list[QualificationFinding] = []
        verified: list[str] = []
        source_count = 0
        decomposition_count = 0
        try:
            manifest = self._adapter.get_release_manifest(release_id)
        except (HammerTimeAdapterError, ValueError) as exc:
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                str(release_id),
                str(exc),
            )
            return self._report(
                release_id,
                expected_manifest_sha256,
                source_count,
                decomposition_count,
                verified,
                findings,
            )

        if (
            manifest.pin.content_sha256 != expected_manifest_sha256
            or manifest.release_target != expected_target
        ):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "candidate manifest hash or target differs from the expected pin",
            )

        batch = manifest.raw.get("official_drafting_batch")
        if not isinstance(batch, Mapping):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "candidate lacks official_drafting_batch metadata",
            )
            return self._report(
                release_id,
                expected_manifest_sha256,
                source_count,
                decomposition_count,
                verified,
                findings,
            )

        raw_sources = batch.get("sources")
        raw_snapshot = manifest.decomposed_snapshot or {}
        raw_decompositions = raw_snapshot.get("files")
        if not isinstance(raw_sources, list) or not isinstance(
            raw_decompositions, list
        ):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "candidate source or decomposition inventory is malformed",
            )
            return self._report(
                release_id,
                expected_manifest_sha256,
                source_count,
                decomposition_count,
                verified,
                findings,
            )

        sources = [item for item in raw_sources if isinstance(item, Mapping)]
        decompositions = [
            item for item in raw_decompositions if isinstance(item, Mapping)
        ]
        source_count = len(sources)
        decomposition_count = len(decompositions)
        source_ids = [str(item.get("source_id", "")) for item in sources]
        normalized_paths = [str(item.get("normalized_path", "")) for item in sources]
        document_paths = {
            path
            for kind, paths in manifest.documents.items()
            if kind != "deleted"
            for path in paths
        }
        if (
            source_count != expected_source_count
            or batch.get("source_count") != expected_source_count
            or raw_snapshot.get("file_count") != expected_source_count
            or decomposition_count != expected_source_count
            or len(set(source_ids)) != source_count
            or len(set(normalized_paths)) != source_count
            or set(normalized_paths) != document_paths
        ):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "candidate source, document, or decomposition sets do not reconcile",
            )

        decomposition_by_source = {
            str(item.get("source_id", "")): item for item in decompositions
        }
        if len(decomposition_by_source) != decomposition_count:
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "decomposition source identifiers are missing or duplicated",
            )

        for source in sources:
            self._inspect_source(source, decomposition_by_source, findings, verified)

        return self._report(
            release_id,
            expected_manifest_sha256,
            source_count,
            decomposition_count,
            verified,
            findings,
        )

    def _inspect_source(
        self,
        source: Mapping[str, Any],
        decomposition_by_source: Mapping[str, Mapping[str, Any]],
        findings: list[QualificationFinding],
        verified: list[str],
    ) -> None:
        source_id = str(source.get("source_id", ""))
        normalized_path = str(source.get("normalized_path", ""))
        source_sha256 = str(source.get("source_sha256", ""))
        normalized_sha256 = str(source.get("normalized_sha256", ""))
        decomposition_pin = decomposition_by_source.get(source_id)
        if not source_id or decomposition_pin is None:
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                source_id or "unknown-source",
                "candidate source lacks one decomposition pin",
            )
            return
        decomposition_path = str(decomposition_pin.get("path", ""))
        decomposition_sha256 = str(decomposition_pin.get("sha256", ""))
        decomposition_id = self._decomposition_id(decomposition_path)
        if decomposition_id is None:
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                source_id,
                "decomposition path is not a normalized json/decomposed artifact",
            )
            return
        try:
            normalized = self._adapter.read_artifact(
                normalized_path,
                expected_sha256=normalized_sha256,
            )
            frontmatter, _ = split_frontmatter(
                normalized.text,
                relative_path=normalized_path,
            )
            decomposition = self._adapter.get_decomposition(
                decomposition_id,
                expected_sha256=decomposition_sha256,
            )
            verified.extend(
                (normalized.pin.content_sha256, decomposition.pin.content_sha256)
            )
            if frontmatter.get("source_sha256") != source_sha256:
                self._add(
                    findings,
                    QualificationFindingCode.WRONG_SOURCE_VERSION,
                    source_id,
                    "normalized source metadata differs from the sealed source hash",
                )
            if (
                decomposition.source_file != normalized_path
                or decomposition.frontmatter.get("source_sha256") != source_sha256
                or not decomposition.chunks
                or decomposition_pin.get("chunk_count") != len(decomposition.chunks)
            ):
                self._add(
                    findings,
                    QualificationFindingCode.RELEASE_MISMATCH,
                    source_id,
                    "decomposition parent, source hash, or chunk count differs",
                )
        except (HammerTimeAdapterError, KeyError, TypeError, ValueError) as exc:
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                source_id,
                str(exc),
            )

    @staticmethod
    def _decomposition_id(path: str) -> str | None:
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or pure.parent != PurePosixPath("json/decomposed")
            or pure.suffix != ".json"
            or not pure.stem
        ):
            return None
        return pure.stem

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
        release_id: ReleaseId,
        release_manifest_sha256: Sha256,
        source_count: int,
        decomposition_count: int,
        verified: list[str],
        findings: list[QualificationFinding],
    ) -> OfficialDraftingCandidateReport:
        return OfficialDraftingCandidateReport(
            status=(
                QualificationStatus.BLOCKED
                if findings
                else QualificationStatus.QUALIFIED
            ),
            release_id=release_id,
            release_manifest_sha256=release_manifest_sha256,
            source_count=source_count,
            decomposition_count=decomposition_count,
            verified_artifact_sha256=tuple(dict.fromkeys(verified)),
            findings=tuple(findings),
        )


__all__ = [
    "OfficialDraftingCandidateInspector",
    "OfficialDraftingCandidateReport",
]
