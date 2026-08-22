"""Synthetic SKL-S2-05 corpus state for pilot verification wiring.

Builds the materialized corpus registry for one synthetic Tenant and one
synthetic HammerTime release from the pilot plan's pinned source files by
driving the real S2-05 components end to end: deterministic outbox events
replayed through the CorpusRegistryProjector into the in-memory registry
store, one deep reconciliation over synthetic corpus, expectation, and
projection ports, and the bounded health read. Every value is synthetic;
no real Tenant, release, corpus file, or backend is used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sklegal_migration import PilotImportPlan
from sklegal_retrieval.corpus_health import (
    BoundedCorpusHealthReader,
    CorpusHealthView,
)
from sklegal_retrieval.corpus_reconciliation import (
    CorpusInventory,
    CorpusReconciliationReport,
    CorpusReleaseExpectation,
    CorpusSourcePin,
    DeepCorpusReconciler,
    ProjectionInventory,
)
from sklegal_retrieval.corpus_registry import (
    CorpusCountKind,
    CorpusRegistryEvent,
    CorpusRegistryProjector,
    CorpusRegistrySnapshot,
    CorpusRegistryStore,
    InMemoryCorpusRegistryStore,
)
from sklegal_retrieval.models import ProjectionLag

HEALTH_BUDGET_MS = 100
DEGRADED_LAG_EVENTS = 1000
LSN_PIN = "0/10"
EVENT_OBSERVED_AT = datetime(2099, 1, 4, 5, 0, 0, tzinfo=UTC)
RECONCILED_AT = datetime(2099, 1, 4, 6, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class PilotCorpusState:
    """S2-05 registry, reconciliation, and health inputs for one release."""

    registry: CorpusRegistrySnapshot
    reconciliation: CorpusReconciliationReport
    health: CorpusHealthView
    store: CorpusRegistryStore


class _ConstantLsnIndex:
    """Watermark index stub that pins one replay LSN for every event."""

    def required_lsn(self, *, event_sequence: int) -> str:
        del event_sequence
        return LSN_PIN


class _CorpusPort:
    """Observed corpus inventory for the single synthetic release."""

    def __init__(self, inventory: CorpusInventory) -> None:
        self._inventory = inventory

    def scan(self, *, tenant_id: UUID) -> tuple[CorpusInventory, ...]:
        if self._inventory.tenant_id != tenant_id:
            return ()
        return (self._inventory,)


class _ExpectationPort:
    """Pinned manifest expectation for the single synthetic release."""

    def __init__(self, expectation: CorpusReleaseExpectation) -> None:
        self._expectation = expectation

    def expectation(
        self, *, tenant_id: UUID, release_id: str
    ) -> CorpusReleaseExpectation:
        if (
            self._expectation.tenant_id != tenant_id
            or self._expectation.release_id != release_id
        ):
            raise RuntimeError("no expectation recorded for that Tenant and release")
        return self._expectation


class _ProjectionPort:
    """Observed projection-side state for the single synthetic release."""

    def __init__(self, projection: ProjectionInventory) -> None:
        self._projection = projection

    def inventory(self, *, tenant_id: UUID, release_id: str) -> ProjectionInventory:
        if (
            self._projection.tenant_id != tenant_id
            or self._projection.release_id != release_id
        ):
            raise RuntimeError("no projection recorded for that Tenant and release")
        return self._projection


def pilot_source_pins(plan: PilotImportPlan) -> tuple[CorpusSourcePin, ...]:
    """Recompute the importer's deterministic source pins for the plan."""

    return tuple(
        CorpusSourcePin(
            source_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"source:{item.relative_path}:{item.content_sha256}",
                )
            ),
            content_sha256=item.content_sha256,
        )
        for item in sorted(plan.source_files, key=lambda item: item.relative_path)
    )


def build_pilot_corpus_state(
    *,
    tenant_id: UUID,
    release_id: str,
    source_pins: tuple[CorpusSourcePin, ...],
    normalized_document_ids: tuple[str, ...] | None = None,
    graph_entity_count: int = 12,
) -> PilotCorpusState:
    """Materialize, reconcile, and read the synthetic pilot corpus registry.

    The projected counts follow the pinned corpus exactly: one vector
    projection per pinned source, one decomposition per normalized
    document, no rejects, no orphans, one release. The bounded health
    read therefore reports the same source count as the pinned corpus.
    """

    if not source_pins:
        raise ValueError("pilot corpus requires at least one pinned source")
    normalized = normalized_document_ids or tuple(
        f"synthetic-doc-{ordinal:02d}" for ordinal in range(1, len(source_pins) + 1)
    )
    pins_by_id = {pin.source_id: pin for pin in source_pins}
    observed_counts = {
        CorpusCountKind.SOURCE: len(pins_by_id),
        CorpusCountKind.NORMALIZED: len(normalized),
        CorpusCountKind.DECOMPOSITION: len(set(normalized)),
        CorpusCountKind.VECTOR: len(pins_by_id),
        CorpusCountKind.GRAPH: graph_entity_count,
        CorpusCountKind.REJECT: 0,
        CorpusCountKind.ORPHAN: 0,
        CorpusCountKind.RELEASE: 1,
    }

    inventory = CorpusInventory(
        tenant_id=tenant_id,
        release_id=release_id,
        sources=source_pins,
        normalized_document_ids=normalized,
        decomposition_document_ids=normalized,
        reject_count=observed_counts[CorpusCountKind.REJECT],
        release_count=observed_counts[CorpusCountKind.RELEASE],
    )
    expectation = CorpusReleaseExpectation(
        tenant_id=tenant_id,
        release_id=release_id,
        sources=source_pins,
        normalized_document_ids=normalized,
        reject_count=observed_counts[CorpusCountKind.REJECT],
    )
    projection = ProjectionInventory(
        tenant_id=tenant_id,
        release_id=release_id,
        vector_source_ids=tuple(pins_by_id),
        graph_entity_count=observed_counts[CorpusCountKind.GRAPH],
        lexical_watermark=len(pins_by_id),
        vector_watermark=len(pins_by_id),
        graph_watermark=len(pins_by_id),
    )

    store = InMemoryCorpusRegistryStore()
    events = tuple(
        CorpusRegistryEvent(
            event_sequence=sequence,
            idempotency_key=f"pilot-corpus:{release_id}:{kind.value}",
            tenant_id=tenant_id,
            release_id=release_id,
            kind=kind,
            count=count,
            core_event_sha256=hashlib.sha256(
                f"pilot-corpus-event:{release_id}:{kind.value}:{count}".encode()
            ).hexdigest(),
            lag=ProjectionLag(lag_events=0, lag_seconds=0.0),
        )
        for sequence, (kind, count) in enumerate(observed_counts.items(), start=1)
    )
    registry_after_replay = CorpusRegistryProjector(_ConstantLsnIndex()).replay(
        events, store, observed_at=EVENT_OBSERVED_AT
    )
    del registry_after_replay

    reconciliation = DeepCorpusReconciler(
        store=store,
        corpus=_CorpusPort(inventory),
        expectations=_ExpectationPort(expectation),
        projections=_ProjectionPort(projection),
        clock=lambda: RECONCILED_AT,
    ).run(tenant_id=tenant_id)

    health = BoundedCorpusHealthReader(
        store,
        budget_ms=HEALTH_BUDGET_MS,
        degraded_lag_events=DEGRADED_LAG_EVENTS,
    ).read()

    return PilotCorpusState(
        registry=store.snapshot(),
        reconciliation=reconciliation,
        health=health,
        store=store,
    )


__all__ = [
    "PilotCorpusState",
    "build_pilot_corpus_state",
    "pilot_source_pins",
]
