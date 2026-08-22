"""Scheduled deep corpus reconciliation against the materialized registry.

The reconciliation job is the only component allowed to walk corpus trees
and projection inventories, and it never runs on the user-facing health
path. It recomputes content-free counts from the pinned release expectation
and the observed inventories, detects missing decompositions, orphan vector
rows, stale graph projections, changed sources, and count drift, then writes
corrected counts and the last-reconciled state back through one
compare-and-set. A timeout, outage, or write conflict aborts the run without
touching registry state, and the report marks coverage incomplete.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import Field, model_validator

from .corpus_registry import (
    CorpusCountKind,
    CorpusCounts,
    CorpusRegistryEntry,
    CorpusRegistryStore,
)
from .models import OpaqueId, ProjectionLag, RetrievalValue, Sha256


class CorpusReconciliationUnavailable(RuntimeError):
    """Deep reconciliation could not complete fail-closed."""


class CorpusDiscrepancyKind(StrEnum):
    """Closed vocabulary of deep reconciliation findings."""

    MISSING_DECOMPOSITION = "missing_decomposition"
    ORPHAN_VECTOR = "orphan_vector"
    STALE_GRAPH = "stale_graph"
    CHANGED_SOURCE = "changed_source"
    MISSING_SOURCE = "missing_source"
    COUNT_DRIFT = "count_drift"


class CorpusSourcePin(RetrievalValue):
    """One content-free source pin: opaque identifier and content digest."""

    source_id: OpaqueId
    content_sha256: Sha256


class CorpusReleaseExpectation(RetrievalValue):
    """Pinned per-release expectation derived from the HammerTime manifest."""

    tenant_id: UUID
    release_id: OpaqueId
    sources: tuple[CorpusSourcePin, ...]
    normalized_document_ids: tuple[OpaqueId, ...]
    reject_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_unique_pins(self) -> Self:
        source_ids = [pin.source_id for pin in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("expected source identifiers must be unique")
        if len(self.normalized_document_ids) != len(set(self.normalized_document_ids)):
            raise ValueError("expected normalized identifiers must be unique")
        return self


class CorpusInventory(RetrievalValue):
    """Observed content-free corpus tree inventory for one release."""

    tenant_id: UUID
    release_id: OpaqueId
    sources: tuple[CorpusSourcePin, ...]
    normalized_document_ids: tuple[OpaqueId, ...]
    decomposition_document_ids: tuple[OpaqueId, ...]
    reject_count: int = Field(ge=0)
    release_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_unique_observations(self) -> Self:
        source_ids = [pin.source_id for pin in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("observed source identifiers must be unique")
        if len(self.decomposition_document_ids) != len(
            set(self.decomposition_document_ids)
        ):
            raise ValueError("observed decomposition identifiers must be unique")
        return self


class ProjectionInventory(RetrievalValue):
    """Observed content-free projection state for one release."""

    tenant_id: UUID
    release_id: OpaqueId
    vector_source_ids: tuple[OpaqueId, ...]
    graph_entity_count: int = Field(ge=0)
    lexical_watermark: int = Field(ge=0)
    vector_watermark: int = Field(ge=0)
    graph_watermark: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_unique_vectors(self) -> Self:
        if len(self.vector_source_ids) != len(set(self.vector_source_ids)):
            raise ValueError("observed vector source identifiers must be unique")
        return self


class CorpusInventoryPort(Protocol):
    """Full corpus tree scan; scheduled jobs only, never the health path."""

    def scan(self, *, tenant_id: UUID) -> tuple[CorpusInventory, ...]:
        """Return the observed inventory for every release of one Tenant."""


class ReleaseExpectationPort(Protocol):
    """Pinned release expectations owned by the HammerTime boundary."""

    def expectation(
        self, *, tenant_id: UUID, release_id: str
    ) -> CorpusReleaseExpectation:
        """Return the pinned manifest expectation for one release."""


class ProjectionInventoryPort(Protocol):
    """Projection-side counts and watermarks for one release."""

    def inventory(self, *, tenant_id: UUID, release_id: str) -> ProjectionInventory:
        """Return the observed projection state for one release."""


class CorpusDiscrepancy(RetrievalValue):
    """One content-free reconciliation finding for one release."""

    kind: CorpusDiscrepancyKind
    tenant_id: UUID
    release_id: OpaqueId
    subject_ids: tuple[OpaqueId, ...] = ()
    registry_count: int | None = Field(default=None, ge=0)
    observed_count: int | None = Field(default=None, ge=0)


class CorpusReconciliationReport(RetrievalValue):
    """Complete coverage outcome for one deep run; separate from health.

    ``complete`` is True only when every Tenant inventory, expectation, and
    projection read finished and the corrected state was written. A partial
    or timed-out run writes nothing and reports ``complete=False``.
    """

    tenant_id: UUID
    started_at: datetime
    completed_at: datetime | None = None
    complete: bool
    releases_reconciled: int = Field(ge=0)
    discrepancies: tuple[CorpusDiscrepancy, ...] = ()

    @model_validator(mode="after")
    def validate_report_shape(self) -> Self:
        for value in (self.started_at, self.completed_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("reconciliation times must be timezone aware")
        if self.complete and self.completed_at is None:
            raise ValueError("a complete run requires a completion time")
        if not self.complete and self.releases_reconciled != 0:
            raise ValueError("an incomplete run cannot claim reconciled releases")
        return self


class DeepCorpusReconciler:
    """Recompute materialized corpus counts and repair registry drift.

    The run reads the full corpus inventory, the pinned release expectation,
    and the projection inventory for every release of one Tenant, recomputes
    each materialized count, records one typed discrepancy per finding, and
    commits the corrected rows with the last-reconciled state through the
    store compare-and-set. Any port failure, deadline breach, or write
    conflict aborts the run with no state change.
    """

    def __init__(
        self,
        *,
        store: CorpusRegistryStore,
        corpus: CorpusInventoryPort,
        expectations: ReleaseExpectationPort,
        projections: ProjectionInventoryPort,
        deadline_seconds: float = 300.0,
        monotonic: Callable[[], float] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        for name, port in (
            ("corpus", corpus),
            ("expectations", expectations),
            ("projections", projections),
        ):
            if not all(
                callable(getattr(port, attribute, None))
                for attribute in _PORT_METHODS[name]
            ):
                raise ValueError(f"{name} port does not implement the contract")
        if not callable(getattr(store, "snapshot", None)) or not callable(
            getattr(store, "reconcile", None)
        ):
            raise ValueError("corpus reconciliation requires a materialized store")
        if deadline_seconds <= 0:
            raise ValueError("reconciliation deadline must be positive")
        self._store = store
        self._corpus = corpus
        self._expectations = expectations
        self._projections = projections
        self._deadline_seconds = deadline_seconds
        self._monotonic = monotonic or time.monotonic
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, *, tenant_id: UUID) -> CorpusReconciliationReport:
        """Execute one deep run for one Tenant; never partial writes."""

        started_at = self._clock()
        started = self._monotonic()
        deadline = started + self._deadline_seconds
        try:
            snapshot = self._store.snapshot()
            inventories = self._corpus.scan(tenant_id=tenant_id)
        except Exception:
            raise CorpusReconciliationUnavailable(
                "corpus reconciliation inputs unavailable"
            ) from None
        discrepancies: list[CorpusDiscrepancy] = []
        corrected: list[CorpusRegistryEntry] = []
        for inventory in inventories:
            if self._monotonic() > deadline:
                return CorpusReconciliationReport(
                    tenant_id=tenant_id,
                    started_at=started_at,
                    completed_at=None,
                    complete=False,
                    releases_reconciled=0,
                    discrepancies=(),
                )
            try:
                expectation = self._expectations.expectation(
                    tenant_id=tenant_id,
                    release_id=inventory.release_id,
                )
                projection = self._projections.inventory(
                    tenant_id=tenant_id,
                    release_id=inventory.release_id,
                )
            except Exception:
                raise CorpusReconciliationUnavailable(
                    "corpus reconciliation inputs unavailable"
                ) from None
            entry = snapshot.entry_for(
                tenant_id=tenant_id, release_id=inventory.release_id
            )
            counts, findings = self._recompute(inventory, expectation, projection)
            discrepancies.extend(findings)
            if entry is not None and entry.counts != counts:
                for kind in CorpusCountKind:
                    if entry.counts.count_for(kind) != counts.count_for(kind):
                        discrepancies.append(
                            CorpusDiscrepancy(
                                kind=CorpusDiscrepancyKind.COUNT_DRIFT,
                                tenant_id=tenant_id,
                                release_id=inventory.release_id,
                                subject_ids=(kind.value,),
                                registry_count=entry.counts.count_for(kind),
                                observed_count=counts.count_for(kind),
                            )
                        )
            corrected.append(
                CorpusRegistryEntry(
                    tenant_id=tenant_id,
                    release_id=inventory.release_id,
                    counts=counts,
                    core_watermark=entry.core_watermark if entry else 0,
                    core_event_sha256=entry.core_event_sha256 if entry else None,
                    required_replay_lsn=(
                        entry.required_replay_lsn if entry else None
                    ),
                    lag=(
                        entry.lag
                        if entry
                        else ProjectionLag(lag_events=0, lag_seconds=0.0)
                    ),
                    last_reconciled_at=self._clock(),
                )
            )
        if self._monotonic() > deadline:
            return CorpusReconciliationReport(
                tenant_id=tenant_id,
                started_at=started_at,
                completed_at=None,
                complete=False,
                releases_reconciled=0,
                discrepancies=(),
            )
        result = self._store.reconcile(
            tuple(corrected),
            expected_generated_at=snapshot.generated_at,
            reconciled_at=self._clock(),
        )
        if result is None:
            raise CorpusReconciliationUnavailable(
                "corpus registry compare-and-set conflict"
            )
        return CorpusReconciliationReport(
            tenant_id=tenant_id,
            started_at=started_at,
            completed_at=self._clock(),
            complete=True,
            releases_reconciled=len(corrected),
            discrepancies=tuple(discrepancies),
        )

    @staticmethod
    def _recompute(
        inventory: CorpusInventory,
        expectation: CorpusReleaseExpectation,
        projection: ProjectionInventory,
    ) -> tuple[CorpusCounts, list[CorpusDiscrepancy]]:
        findings: list[CorpusDiscrepancy] = []
        expected_sources = {pin.source_id: pin for pin in expectation.sources}
        observed_sources = {pin.source_id: pin for pin in inventory.sources}
        changed = tuple(
            sorted(
                source_id
                for source_id, pin in observed_sources.items()
                if source_id in expected_sources
                and expected_sources[source_id].content_sha256 != pin.content_sha256
            )
        )
        if changed:
            findings.append(
                CorpusDiscrepancy(
                    kind=CorpusDiscrepancyKind.CHANGED_SOURCE,
                    tenant_id=inventory.tenant_id,
                    release_id=inventory.release_id,
                    subject_ids=changed,
                    registry_count=len(expected_sources),
                    observed_count=len(observed_sources),
                )
            )
        missing_sources = tuple(
            sorted(set(expected_sources) - set(observed_sources))
        )
        if missing_sources:
            findings.append(
                CorpusDiscrepancy(
                    kind=CorpusDiscrepancyKind.MISSING_SOURCE,
                    tenant_id=inventory.tenant_id,
                    release_id=inventory.release_id,
                    subject_ids=missing_sources,
                    registry_count=len(expected_sources),
                    observed_count=len(observed_sources),
                )
            )
        decomposed = set(inventory.decomposition_document_ids)
        missing_decomposition = tuple(
            sorted(set(inventory.normalized_document_ids) - decomposed)
        )
        if missing_decomposition:
            findings.append(
                CorpusDiscrepancy(
                    kind=CorpusDiscrepancyKind.MISSING_DECOMPOSITION,
                    tenant_id=inventory.tenant_id,
                    release_id=inventory.release_id,
                    subject_ids=missing_decomposition,
                    registry_count=len(inventory.normalized_document_ids),
                    observed_count=len(decomposed),
                )
            )
        orphan_vectors = tuple(
            sorted(set(projection.vector_source_ids) - set(observed_sources))
        )
        if orphan_vectors:
            findings.append(
                CorpusDiscrepancy(
                    kind=CorpusDiscrepancyKind.ORPHAN_VECTOR,
                    tenant_id=inventory.tenant_id,
                    release_id=inventory.release_id,
                    subject_ids=orphan_vectors,
                    registry_count=len(observed_sources),
                    observed_count=len(projection.vector_source_ids),
                )
            )
        relational_watermark = max(
            projection.lexical_watermark, projection.vector_watermark
        )
        if projection.graph_watermark < relational_watermark:
            findings.append(
                CorpusDiscrepancy(
                    kind=CorpusDiscrepancyKind.STALE_GRAPH,
                    tenant_id=inventory.tenant_id,
                    release_id=inventory.release_id,
                    registry_count=relational_watermark,
                    observed_count=projection.graph_watermark,
                )
            )
        counts = CorpusCounts(
            source=len(observed_sources),
            normalized=len(inventory.normalized_document_ids),
            decomposition=len(decomposed),
            vector=len(projection.vector_source_ids),
            graph=projection.graph_entity_count,
            reject=inventory.reject_count,
            orphan=len(orphan_vectors),
            release=inventory.release_count,
        )
        return counts, findings


_PORT_METHODS = {
    "corpus": ("scan",),
    "expectations": ("expectation",),
    "projections": ("inventory",),
}


__all__ = [
    "CorpusDiscrepancy",
    "CorpusDiscrepancyKind",
    "CorpusInventory",
    "CorpusInventoryPort",
    "CorpusReconciliationReport",
    "CorpusReconciliationUnavailable",
    "CorpusReleaseExpectation",
    "CorpusSourcePin",
    "DeepCorpusReconciler",
    "ProjectionInventory",
    "ProjectionInventoryPort",
    "ReleaseExpectationPort",
]
