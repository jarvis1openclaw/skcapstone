"""Deterministic scoped lexical retrieval for one official drafting candidate.

The runner follows only the artifacts pinned by one release manifest. It
reads each batch source's hash-pinned decomposition through the read-only
adapter, evaluates explicit typed queries over those chunks, and emits
``ScopedRetrievalEvidence`` objects that feed directly into
``OfficialDraftingReleaseRequest``.

Guarantees:

- No repository listing, no Inbox reads, no vector or graph lookups.
- Every read is hash-pinned against the candidate manifest.
- Missing expected hits, collapsed conflicts, and unpinned currentness
  evidence all fail closed as typed findings.
- The runner has no promotion, rollback, write, delete, move, or dispatch
  surface; it only proves what the pinned chunks say.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import model_validator
from sklegal_domain.value_objects import FrozenValue, NonEmptyText, Sha256

from .adapter import HammerTimeReleaseAdapter
from .errors import HammerTimeAdapterError
from .frontmatter import split_frontmatter
from .models import AliasTarget, ReleaseId
from .release_qualification import (
    QualificationFinding,
    QualificationFindingCode,
    QualificationStatus,
    RetrievalHitEvidence,
    ScopedRetrievalEvidence,
)


class RetrievalQuerySpec(FrozenValue):
    """One explicit scoped query with its source-linked derivation."""

    query_id: NonEmptyText
    terms: tuple[NonEmptyText, ...]
    expected_source_ids: tuple[NonEmptyText, ...]
    conflict_source_ids: tuple[NonEmptyText, ...] = ()
    derivation: NonEmptyText
    derivation_sha256: Sha256

    @property
    def referenced_source_ids(self) -> frozenset[str]:
        return frozenset((*self.expected_source_ids, *self.conflict_source_ids))


class RetrievalSourceExpectation(FrozenValue):
    """Typed, source-linked metadata and currentness for one batch source."""

    source_id: NonEmptyText
    issuing_body: NonEmptyText
    version: NonEmptyText
    scope: NonEmptyText
    source_sha256: Sha256
    currentness: NonEmptyText
    presented_as_current: bool


class ScopedRetrievalBundle(FrozenValue):
    """Content-free result of scoped retrieval over one candidate release."""

    release_id: ReleaseId
    release_manifest_sha256: Sha256
    checks: tuple[ScopedRetrievalEvidence, ...]
    findings: tuple[QualificationFinding, ...] = ()

    @model_validator(mode="after")
    def validate_verdict(self) -> ScopedRetrievalBundle:
        if not self.checks and not self.findings:
            raise ValueError("an empty scoped retrieval result cannot be clean")
        return self

    @property
    def status(self) -> QualificationStatus:
        return (
            QualificationStatus.BLOCKED
            if self.findings
            else QualificationStatus.QUALIFIED
        )


class OfficialDraftingScopedRetrievalRunner:
    """Evaluate typed queries over only a candidate's pinned chunks."""

    def __init__(self, adapter: HammerTimeReleaseAdapter) -> None:
        if not isinstance(adapter, HammerTimeReleaseAdapter):
            raise ValueError(
                "scoped retrieval requires the read-only HammerTime adapter"
            )
        self._adapter = adapter

    def run(
        self,
        *,
        release_id: ReleaseId,
        expected_manifest_sha256: Sha256,
        expected_target: AliasTarget,
        queries: tuple[RetrievalQuerySpec, ...],
        sources: tuple[RetrievalSourceExpectation, ...],
    ) -> ScopedRetrievalBundle:
        if not queries:
            raise ValueError("scoped retrieval requires at least one query")
        findings: list[QualificationFinding] = []

        try:
            manifest = self._adapter.get_release_manifest(release_id)
        except HammerTimeAdapterError as exc:
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                str(release_id),
                str(exc),
            )
            return ScopedRetrievalBundle(
                release_id=release_id,
                release_manifest_sha256=expected_manifest_sha256,
                checks=(),
                findings=tuple(findings),
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
        raw_snapshot = manifest.decomposed_snapshot or {}
        if not isinstance(batch, Mapping) or not isinstance(
            raw_snapshot.get("files"), list
        ):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "candidate lacks official_drafting_batch metadata or a snapshot",
            )
            return self._bundle(release_id, expected_manifest_sha256, (), findings)

        raw_sources = [
            item for item in (batch.get("sources") or []) if isinstance(item, Mapping)
        ]
        raw_files = [
            item
            for item in raw_snapshot.get("files") or []
            if isinstance(item, Mapping)
        ]
        source_by_id = {str(item.get("source_id", "")): item for item in raw_sources}
        decomposition_by_source = {
            str(item.get("source_id", "")): item for item in raw_files
        }
        if len(source_by_id) != len(raw_sources):
            self._add(
                findings,
                QualificationFindingCode.RELEASE_MISMATCH,
                str(release_id),
                "candidate source identifiers are missing or duplicated",
            )

        expectations_by_id = {
            expectation.source_id: expectation for expectation in sources
        }
        if len(expectations_by_id) != len(sources):
            self._add(
                findings,
                QualificationFindingCode.RETRIEVAL_EVIDENCE_MISMATCH,
                str(release_id),
                "source expectations are missing or duplicated",
            )

        chunks_by_source = self._index_chunks(
            source_by_id, decomposition_by_source, expectations_by_id, findings
        )

        checks: list[ScopedRetrievalEvidence] = []
        for query in queries:
            checks.append(
                self._evaluate_query(
                    query, source_by_id, expectations_by_id, chunks_by_source, findings
                )
            )

        return self._bundle(
            release_id, expected_manifest_sha256, tuple(checks), findings
        )

    def _index_chunks(
        self,
        source_by_id: Mapping[str, Mapping[str, Any]],
        decomposition_by_source: Mapping[str, Mapping[str, Any]],
        expectations_by_id: Mapping[str, RetrievalSourceExpectation],
        findings: list[QualificationFinding],
    ) -> dict[str, tuple[tuple[str, str], ...]]:
        chunks: dict[str, tuple[tuple[str, str], ...]] = {}
        for source_id in sorted(source_by_id):
            expectation = expectations_by_id.get(source_id)
            if expectation is None:
                self._add(
                    findings,
                    QualificationFindingCode.RETRIEVAL_EVIDENCE_MISMATCH,
                    source_id,
                    "candidate source lacks typed retrieval metadata",
                )
                continue
            raw_source = source_by_id[source_id]
            if str(raw_source.get("source_sha256", "")) != expectation.source_sha256:
                self._add(
                    findings,
                    QualificationFindingCode.WRONG_SOURCE_VERSION,
                    source_id,
                    "source hash differs from the typed expectation",
                )
            decomposition_pin = decomposition_by_source.get(source_id)
            if decomposition_pin is None:
                self._add(
                    findings,
                    QualificationFindingCode.MISSING_ARTIFACT,
                    source_id,
                    "candidate source lacks a pinned decomposition",
                )
                continue
            decomposition_id = str(decomposition_pin.get("path", "")).rsplit("/", 1)[-1]
            decomposition_id = decomposition_id.removesuffix(".json")
            try:
                normalized = self._adapter.read_artifact(
                    str(raw_source.get("normalized_path", "")),
                    expected_sha256=str(raw_source.get("normalized_sha256", "")),
                )
                frontmatter, _ = split_frontmatter(
                    normalized.text,
                    relative_path=str(raw_source.get("normalized_path", "")),
                )
                decomposition = self._adapter.get_decomposition(
                    decomposition_id,
                    expected_sha256=str(decomposition_pin.get("sha256", "")),
                )
            except HammerTimeAdapterError as exc:
                self._add(
                    findings,
                    QualificationFindingCode.MISSING_ARTIFACT,
                    source_id,
                    str(exc),
                )
                continue
            if frontmatter.get("source_sha256") != expectation.source_sha256:
                self._add(
                    findings,
                    QualificationFindingCode.WRONG_SOURCE_VERSION,
                    source_id,
                    "normalized frontmatter differs from the sealed source hash",
                )
            if decomposition.source_file != raw_source.get("normalized_path"):
                self._add(
                    findings,
                    QualificationFindingCode.RELEASE_MISMATCH,
                    source_id,
                    "decomposition parent differs from the normalized source",
                )
            if not decomposition.chunks:
                self._add(
                    findings,
                    QualificationFindingCode.MISSING_ARTIFACT,
                    source_id,
                    "decomposition holds no chunks to retrieve",
                )
                continue
            chunks[source_id] = tuple(
                (
                    chunk.text.casefold(),
                    f"json/decomposed/{decomposition_id}.json#chunk-{chunk_index}",
                )
                for chunk_index, chunk in enumerate(decomposition.chunks)
            )
        return chunks

    def _evaluate_query(
        self,
        query: RetrievalQuerySpec,
        source_by_id: Mapping[str, Mapping[str, Any]],
        expectations_by_id: Mapping[str, RetrievalSourceExpectation],
        chunks_by_source: Mapping[str, tuple[tuple[str, str], ...]],
        findings: list[QualificationFinding],
    ) -> ScopedRetrievalEvidence:
        unknown = sorted(
            source_id
            for source_id in query.referenced_source_ids
            if source_id not in source_by_id
        )
        for source_id in unknown:
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                source_id,
                "query references a source absent from the candidate",
            )

        hits: list[RetrievalHitEvidence] = []
        for source_id in sorted(chunks_by_source):
            if source_id not in query.referenced_source_ids:
                continue
            expectation = expectations_by_id.get(source_id)
            if expectation is None:
                continue
            match = self._first_match(chunks_by_source[source_id], query.terms)
            if match is None:
                continue
            _matched_text, locator = match
            hits.append(
                RetrievalHitEvidence(
                    source_id=source_id,
                    issuing_body=expectation.issuing_body,
                    version=expectation.version,
                    scope=expectation.scope,
                    source_sha256=expectation.source_sha256,
                    locator=locator,
                    presented_as_current=expectation.presented_as_current,
                )
            )

        hit_ids = {hit.source_id for hit in hits}
        for source_id in sorted(set(query.expected_source_ids) - hit_ids):
            self._add(
                findings,
                QualificationFindingCode.MISSING_ARTIFACT,
                query.query_id,
                f"targeted retrieval omitted expected source {source_id}",
            )
        for source_id in sorted(set(query.conflict_source_ids) - hit_ids):
            self._add(
                findings,
                QualificationFindingCode.SCOPE_COLLAPSE,
                query.query_id,
                f"targeted retrieval collapsed preserved conflict source {source_id}",
            )
        return ScopedRetrievalEvidence(
            query_id=query.query_id,
            expected_source_ids=query.expected_source_ids,
            conflict_source_ids=query.conflict_source_ids,
            hits=tuple(hits),
        )

    @staticmethod
    def _first_match(
        chunks: tuple[tuple[str, str], ...], terms: tuple[str, ...]
    ) -> tuple[str, str] | None:
        lowered = tuple(term.casefold() for term in terms)
        for text, locator in chunks:
            if all(term in text for term in lowered):
                return text, locator
        return None

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
    def _bundle(
        release_id: ReleaseId,
        release_manifest_sha256: Sha256,
        checks: tuple[ScopedRetrievalEvidence, ...],
        findings: list[QualificationFinding],
    ) -> ScopedRetrievalBundle:
        return ScopedRetrievalBundle(
            release_id=release_id,
            release_manifest_sha256=release_manifest_sha256,
            checks=checks,
            findings=tuple(findings),
        )


__all__ = [
    "OfficialDraftingScopedRetrievalRunner",
    "RetrievalQuerySpec",
    "RetrievalSourceExpectation",
    "ScopedRetrievalBundle",
]
