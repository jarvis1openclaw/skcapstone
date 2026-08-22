"""Deep corpus reconciliation tests: findings, timeouts, and convergence."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_retrieval.corpus_health import (
    BoundedCorpusHealthReader,
    CorpusHealthStatus,
)
from sklegal_retrieval.corpus_reconciliation import (
    CorpusDiscrepancy,
    CorpusDiscrepancyKind,
    CorpusInventory,
    CorpusReconciliationReport,
    CorpusReconciliationUnavailable,
    CorpusReleaseExpectation,
    CorpusSourcePin,
    DeepCorpusReconciler,
    ProjectionInventory,
)
from sklegal_retrieval.corpus_registry import (
    CorpusCountKind,
    CorpusRegistryEntry,
    CorpusRegistryEvent,
    CorpusRegistryProjector,
    InMemoryCorpusRegistryStore,
    zero_corpus_counts,
)
from sklegal_retrieval.models import ProjectionLag

from tests.support.retrieval_pins import HASH_A, HASH_B, OTHER_TENANT_ID, TENANT_ID

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
RECONCILED_AT = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)
LSN_ONE = "0/10"


class ConstantLsnIndex:
    """Index stub that pins one LSN for every watermark."""

    def required_lsn(self, *, event_sequence: int) -> str:
        del event_sequence
        return LSN_ONE


class FakeCorpusPort:
    """Observed corpus tree inventory for every release of one Tenant."""

    def __init__(
        self,
        inventories: tuple[CorpusInventory, ...],
        *,
        on_scan: Any = None,
    ) -> None:
        self._inventories = inventories
        self._on_scan = on_scan
        self.scan_calls: list[UUID] = []

    def scan(self, *, tenant_id: UUID) -> tuple[CorpusInventory, ...]:
        self.scan_calls.append(tenant_id)
        if self._on_scan is not None:
            self._on_scan()
        return tuple(
            inventory
            for inventory in self._inventories
            if inventory.tenant_id == tenant_id
        )


class FakeExpectationPort:
    """Pinned manifest expectations from the HammerTime boundary."""

    def __init__(
        self,
        expectations: tuple[CorpusReleaseExpectation, ...],
        *,
        fail_for: set[str] = frozenset(),
    ) -> None:
        self._expectations = {
            (item.tenant_id, item.release_id): item for item in expectations
        }
        self._fail_for = set(fail_for)
        self.calls: list[str] = []

    def expectation(
        self, *, tenant_id: UUID, release_id: str
    ) -> CorpusReleaseExpectation:
        self.calls.append(release_id)
        if release_id in self._fail_for:
            raise RuntimeError("expectation port outage")
        return self._expectations[(tenant_id, release_id)]


class FakeProjectionPort:
    """Observed projection-side state for one release."""

    def __init__(
        self,
        projections: tuple[ProjectionInventory, ...],
        *,
        fail_for: set[str] = frozenset(),
    ) -> None:
        self._projections = {
            (item.tenant_id, item.release_id): item for item in projections
        }
        self._fail_for = set(fail_for)

    def inventory(self, *, tenant_id: UUID, release_id: str) -> ProjectionInventory:
        if release_id in self._fail_for:
            raise RuntimeError("projection port outage")
        return self._projections[(tenant_id, release_id)]


class SteppedMonotonic:
    """Deterministic clock that advances by a fixed step per call."""

    def __init__(self, step: float) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def _pins(*source_ids: str, sha: str = HASH_A) -> tuple[CorpusSourcePin, ...]:
    return tuple(
        CorpusSourcePin(source_id=source_id, content_sha256=sha)
        for source_id in source_ids
    )


def _inventory(
    *,
    tenant_id: UUID = TENANT_ID,
    release_id: str = "release-1",
    sources: tuple[CorpusSourcePin, ...] | None = None,
    normalized: tuple[str, ...] = ("doc-1", "doc-2", "doc-3"),
    decompositions: tuple[str, ...] = ("doc-1", "doc-2", "doc-3"),
    rejects: int = 0,
    releases: int = 1,
) -> CorpusInventory:
    return CorpusInventory(
        tenant_id=tenant_id,
        release_id=release_id,
        sources=sources
        if sources is not None
        else _pins("source-1", "source-2", "source-3"),
        normalized_document_ids=normalized,
        decomposition_document_ids=decompositions,
        reject_count=rejects,
        release_count=releases,
    )


def _expectation(
    *,
    tenant_id: UUID = TENANT_ID,
    release_id: str = "release-1",
    sources: tuple[CorpusSourcePin, ...] | None = None,
    normalized: tuple[str, ...] = ("doc-1", "doc-2", "doc-3"),
    rejects: int = 0,
) -> CorpusReleaseExpectation:
    return CorpusReleaseExpectation(
        tenant_id=tenant_id,
        release_id=release_id,
        sources=sources
        if sources is not None
        else _pins("source-1", "source-2", "source-3"),
        normalized_document_ids=normalized,
        reject_count=rejects,
    )


def _projection(
    *,
    tenant_id: UUID = TENANT_ID,
    release_id: str = "release-1",
    vector_ids: tuple[str, ...] = ("source-1", "source-2", "source-3"),
    graph_entities: int = 12,
    lexical_watermark: int = 30,
    vector_watermark: int = 30,
    graph_watermark: int = 30,
) -> ProjectionInventory:
    return ProjectionInventory(
        tenant_id=tenant_id,
        release_id=release_id,
        vector_source_ids=vector_ids,
        graph_entity_count=graph_entities,
        lexical_watermark=lexical_watermark,
        vector_watermark=vector_watermark,
        graph_watermark=graph_watermark,
    )


def _reconciler(
    store: Any,
    *,
    inventories: tuple[CorpusInventory, ...] | None = None,
    expectations: tuple[CorpusReleaseExpectation, ...] | None = None,
    projections: tuple[ProjectionInventory, ...] | None = None,
    corpus: Any = None,
    expectation_port: Any = None,
    deadline_seconds: float = 300.0,
    monotonic: Any = None,
) -> DeepCorpusReconciler:
    inventory = inventories if inventories is not None else (_inventory(),)
    expectation = expectations if expectations is not None else (_expectation(),)
    projection = projections if projections is not None else (_projection(),)
    return DeepCorpusReconciler(
        store=store,
        corpus=corpus or FakeCorpusPort(inventory),
        expectations=expectation_port or FakeExpectationPort(expectation),
        projections=FakeProjectionPort(projection),
        deadline_seconds=deadline_seconds,
        monotonic=monotonic or time.monotonic,
        clock=lambda: NOW,
    )


def _seeded_store(**kwargs: Any) -> InMemoryCorpusRegistryStore:
    release_id = kwargs.pop("release_id", "release-1")
    tenant_id = kwargs.pop("tenant_id", TENANT_ID)
    counts = kwargs.pop("counts", {})
    materialized = zero_corpus_counts()
    for kind, count in counts.items():
        materialized = materialized.with_count(CorpusCountKind(kind), count)
    store = InMemoryCorpusRegistryStore()
    store.seed(
        (
            CorpusRegistryEntry(
                tenant_id=tenant_id,
                release_id=release_id,
                counts=materialized,
                core_watermark=kwargs.pop("watermark", 5),
                core_event_sha256=kwargs.pop("sha", HASH_A),
                required_replay_lsn=kwargs.pop("lsn", LSN_ONE),
                lag=kwargs.pop("lag", ProjectionLag(lag_events=0, lag_seconds=0.0)),
                last_reconciled_at=kwargs.pop("reconciled", None),
            ),
        )
    )
    return store


def _find(
    report: CorpusReconciliationReport, kind: CorpusDiscrepancyKind
) -> tuple[CorpusDiscrepancy, ...]:
    return tuple(item for item in report.discrepancies if item.kind is kind)


def test_healthy_run_completes_and_writes_last_reconciled_state() -> None:
    store = _seeded_store(
        counts={
            "source": 3,
            "normalized": 3,
            "decomposition": 3,
            "vector": 3,
            "graph": 12,
            "reject": 0,
            "orphan": 0,
            "release": 1,
        }
    )
    report = _reconciler(store).run(tenant_id=TENANT_ID)
    assert report.complete is True
    assert report.releases_reconciled == 1
    assert report.discrepancies == ()
    assert report.completed_at == NOW
    entry = store.snapshot().entry_for(tenant_id=TENANT_ID, release_id="release-1")
    assert entry is not None
    assert entry.last_reconciled_at == NOW
    assert entry.counts.source == 3
    assert entry.counts.release == 1


def test_changed_source_is_detected() -> None:
    store = _seeded_store(counts={"source": 3})
    report = _reconciler(
        store,
        inventories=(
            _inventory(sources=_pins("source-1", "source-2", "source-3", sha=HASH_B)),
        ),
    ).run(tenant_id=TENANT_ID)
    changed = _find(report, CorpusDiscrepancyKind.CHANGED_SOURCE)
    assert len(changed) == 1
    assert changed[0].subject_ids == ("source-1", "source-2", "source-3")
    assert changed[0].registry_count == 3
    assert changed[0].observed_count == 3
    assert report.complete is True


def test_missing_source_is_detected() -> None:
    store = _seeded_store(counts={"source": 3})
    report = _reconciler(
        store,
        inventories=(_inventory(sources=_pins("source-1", "source-2")),),
    ).run(tenant_id=TENANT_ID)
    missing = _find(report, CorpusDiscrepancyKind.MISSING_SOURCE)
    assert len(missing) == 1
    assert missing[0].subject_ids == ("source-3",)
    assert missing[0].registry_count == 3
    assert missing[0].observed_count == 2
    entry = store.snapshot().entries[0]
    assert entry.counts.source == 2


def test_orphan_vector_is_detected_and_materialized() -> None:
    store = _seeded_store(counts={"source": 3, "vector": 4})
    report = _reconciler(
        store,
        projections=(
            _projection(
                vector_ids=("source-1", "source-2", "source-3", "source-ghost")
            ),
        ),
    ).run(tenant_id=TENANT_ID)
    orphans = _find(report, CorpusDiscrepancyKind.ORPHAN_VECTOR)
    assert len(orphans) == 1
    assert orphans[0].subject_ids == ("source-ghost",)
    assert orphans[0].registry_count == 3
    assert orphans[0].observed_count == 4
    entry = store.snapshot().entries[0]
    assert entry.counts.orphan == 1
    assert entry.counts.vector == 4


def test_missing_decomposition_is_detected() -> None:
    store = _seeded_store(counts={"normalized": 3, "decomposition": 3})
    report = _reconciler(
        store,
        inventories=(_inventory(decompositions=("doc-1", "doc-2")),),
    ).run(tenant_id=TENANT_ID)
    missing = _find(report, CorpusDiscrepancyKind.MISSING_DECOMPOSITION)
    assert len(missing) == 1
    assert missing[0].subject_ids == ("doc-3",)
    assert missing[0].registry_count == 3
    assert missing[0].observed_count == 2
    entry = store.snapshot().entries[0]
    assert entry.counts.decomposition == 2


def test_stale_graph_is_detected_against_relational_watermarks() -> None:
    store = _seeded_store()
    report = _reconciler(
        store,
        projections=(
            _projection(lexical_watermark=41, vector_watermark=39, graph_watermark=37),
        ),
    ).run(tenant_id=TENANT_ID)
    stale = _find(report, CorpusDiscrepancyKind.STALE_GRAPH)
    assert len(stale) == 1
    assert stale[0].registry_count == 41
    assert stale[0].observed_count == 37


def test_count_drift_reports_registry_and_observed_counts() -> None:
    store = _seeded_store(
        counts={
            "source": 3,
            "normalized": 3,
            "decomposition": 3,
            "vector": 3,
            "graph": 99,
            "reject": 1,
            "orphan": 0,
            "release": 1,
        }
    )
    report = _reconciler(store).run(tenant_id=TENANT_ID)
    drift = _find(report, CorpusDiscrepancyKind.COUNT_DRIFT)
    by_kind = {item.subject_ids[0]: item for item in drift}
    assert set(by_kind) == {"graph", "reject"}
    assert by_kind["graph"].registry_count == 99
    assert by_kind["graph"].observed_count == 12
    assert by_kind["reject"].registry_count == 1
    assert by_kind["reject"].observed_count == 0
    entry = store.snapshot().entries[0]
    assert entry.counts.graph == 12
    assert entry.counts.reject == 0


def test_mid_run_deadline_aborts_with_no_state_write() -> None:
    store = _seeded_store(counts={"source": 3})
    reconciler = _reconciler(
        store,
        inventories=(
            _inventory(release_id="release-1"),
            _inventory(release_id="release-2"),
        ),
        expectations=(
            _expectation(release_id="release-1"),
            _expectation(release_id="release-2"),
        ),
        projections=(
            _projection(release_id="release-1"),
            _projection(release_id="release-2"),
        ),
        deadline_seconds=300.0,
        monotonic=SteppedMonotonic(step=1000.0),
    )
    report = reconciler.run(tenant_id=TENANT_ID)
    assert report.complete is False
    assert report.releases_reconciled == 0
    assert report.completed_at is None
    assert report.discrepancies == ()
    entry = store.snapshot().entries[0]
    assert entry.last_reconciled_at is None
    assert entry.counts.source == 3


def test_post_scan_deadline_aborts_before_the_write() -> None:
    store = _seeded_store(counts={"source": 3})
    reconciler = _reconciler(
        store,
        deadline_seconds=300.0,
        monotonic=SteppedMonotonic(step=200.0),
    )
    report = reconciler.run(tenant_id=TENANT_ID)
    assert report.complete is False
    assert report.releases_reconciled == 0
    entry = store.snapshot().entries[0]
    assert entry.last_reconciled_at is None
    assert entry.counts.source == 3


def test_port_failure_aborts_without_writing_state() -> None:
    store = _seeded_store(counts={"source": 3})
    with pytest.raises(CorpusReconciliationUnavailable):
        _reconciler(
            store,
            expectation_port=FakeExpectationPort(
                (_expectation(),), fail_for={"release-1"}
            ),
        ).run(tenant_id=TENANT_ID)
    entry = store.snapshot().entries[0]
    assert entry.last_reconciled_at is None


def test_projection_and_scan_and_snapshot_failures_abort_closed() -> None:
    store = _seeded_store()
    with pytest.raises(CorpusReconciliationUnavailable):
        _reconciler(
            store,
            projections=(),
            expectation_port=FakeExpectationPort((_expectation(),)),
        ).run(tenant_id=TENANT_ID)
    crashing_corpus = FakeCorpusPort(
        (_inventory(),),
        on_scan=lambda: (_ for _ in ()).throw(RuntimeError("scan outage")),
    )
    with pytest.raises(CorpusReconciliationUnavailable):
        _reconciler(store, corpus=crashing_corpus).run(tenant_id=TENANT_ID)

    class CrashingStore:
        def snapshot(self) -> object:
            raise RuntimeError("store outage")

        def reconcile(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("store outage")

    with pytest.raises(CorpusReconciliationUnavailable):
        _reconciler(CrashingStore()).run(tenant_id=TENANT_ID)  # type: ignore[arg-type]


def test_concurrent_outbox_write_aborts_the_compare_and_set() -> None:
    store = _seeded_store(counts={"source": 3}, watermark=5)

    def concurrent_apply() -> None:
        projector = CorpusRegistryProjector(ConstantLsnIndex())
        projector.replay(
            (
                CorpusRegistryEvent(
                    event_sequence=6,
                    idempotency_key="event-6",
                    tenant_id=TENANT_ID,
                    release_id="release-1",
                    kind=CorpusCountKind.SOURCE,
                    count=4,
                    core_event_sha256=HASH_B,
                ),
            ),
            store,
            observed_at=NOW,
        )

    corpus = FakeCorpusPort((_inventory(),), on_scan=concurrent_apply)
    with pytest.raises(CorpusReconciliationUnavailable):
        _reconciler(store, corpus=corpus).run(tenant_id=TENANT_ID)
    entry = store.snapshot().entries[0]
    # The concurrently applied outbox event wins; the reconciled correction
    # is discarded rather than overwriting live incremental state.
    assert entry.core_watermark == 6
    assert entry.counts.source == 4
    assert entry.counts == zero_corpus_counts().with_count(CorpusCountKind.SOURCE, 4)
    assert entry.last_reconciled_at is None


def test_reconciliation_preserves_watermark_lsn_and_lag_pins() -> None:
    lag = ProjectionLag(lag_events=7, lag_seconds=3.0)
    store = _seeded_store(counts={"source": 3}, lag=lag)
    report = _reconciler(store).run(tenant_id=TENANT_ID)
    assert report.complete is True
    entry = store.snapshot().entries[0]
    assert entry.core_watermark == 5
    assert entry.core_event_sha256 == HASH_A
    assert entry.required_replay_lsn == LSN_ONE
    assert entry.lag == lag
    assert entry.last_reconciled_at == NOW


def test_new_release_gets_a_zero_watermark_entry() -> None:
    store = _seeded_store(release_id="release-9")
    report = _reconciler(store).run(tenant_id=TENANT_ID)
    assert report.complete is True
    entry = store.snapshot().entry_for(tenant_id=TENANT_ID, release_id="release-1")
    assert entry is not None
    assert entry.core_watermark == 0
    assert entry.core_event_sha256 is None
    assert entry.required_replay_lsn is None
    assert entry.counts.source == 3
    assert entry.last_reconciled_at == NOW


def test_empty_tenant_completes_with_zero_releases() -> None:
    store = _seeded_store()
    report = _reconciler(store).run(tenant_id=OTHER_TENANT_ID)
    assert report.complete is True
    assert report.releases_reconciled == 0


def test_multi_release_run_reports_every_release() -> None:
    store = _seeded_store()
    report = _reconciler(
        store,
        inventories=(
            _inventory(release_id="release-1"),
            _inventory(release_id="release-2", decompositions=("doc-1",)),
        ),
        expectations=(
            _expectation(release_id="release-1"),
            _expectation(release_id="release-2"),
        ),
        projections=(
            _projection(release_id="release-1"),
            _projection(release_id="release-2"),
        ),
    ).run(tenant_id=TENANT_ID)
    assert report.complete is True
    assert report.releases_reconciled == 2
    assert _find(report, CorpusDiscrepancyKind.MISSING_DECOMPOSITION)
    assert all(
        item.release_id in {"release-1", "release-2"} for item in report.discrepancies
    )


def test_large_fixture_tree_reconciles_within_the_deadline() -> None:
    releases = 200
    inventories = tuple(
        _inventory(
            release_id=f"release-{ordinal}",
            sources=_pins(*(f"source-{ordinal}-{inner}" for inner in range(40))),
            normalized=tuple(f"doc-{ordinal}-{inner}" for inner in range(40)),
            decompositions=tuple(f"doc-{ordinal}-{inner}" for inner in range(40)),
        )
        for ordinal in range(1, releases + 1)
    )
    expectations = tuple(
        _expectation(
            release_id=f"release-{ordinal}",
            sources=_pins(*(f"source-{ordinal}-{inner}" for inner in range(40))),
            normalized=tuple(f"doc-{ordinal}-{inner}" for inner in range(40)),
        )
        for ordinal in range(1, releases + 1)
    )
    projections = tuple(
        _projection(
            release_id=f"release-{ordinal}",
            vector_ids=tuple(f"source-{ordinal}-{inner}" for inner in range(40)),
        )
        for ordinal in range(1, releases + 1)
    )
    store = _seeded_store(
        counts={
            "source": 40,
            "normalized": 40,
            "decomposition": 40,
            "vector": 40,
            "graph": 12,
            "reject": 0,
            "orphan": 0,
            "release": 1,
        }
    )
    report = _reconciler(
        store,
        inventories=inventories,
        expectations=expectations,
        projections=projections,
        deadline_seconds=300.0,
    ).run(tenant_id=TENANT_ID)
    assert report.complete is True
    assert report.releases_reconciled == releases
    assert report.discrepancies == ()
    entry = store.snapshot().entry_for(tenant_id=TENANT_ID, release_id="release-1")
    assert entry is not None
    assert entry.counts.source == 40


def test_incremental_updates_converge_with_deep_reconciliation() -> None:
    store = InMemoryCorpusRegistryStore()
    projector = CorpusRegistryProjector(ConstantLsnIndex())

    def event(sequence: int, kind: str, count: int) -> CorpusRegistryEvent:
        return CorpusRegistryEvent(
            event_sequence=sequence,
            idempotency_key=f"event-{sequence}",
            tenant_id=TENANT_ID,
            release_id="release-1",
            kind=CorpusCountKind(kind),
            count=count,
            core_event_sha256=HASH_A,
        )

    projector.replay(
        (event(1, "source", 3), event(2, "vector", 3)),
        store,
        observed_at=NOW,
    )
    entry = store.snapshot().entries[0]
    assert entry.core_watermark == 2
    assert entry.counts.source == 3

    report = _reconciler(store).run(tenant_id=TENANT_ID)
    assert report.complete is True
    drift_kinds = {
        item.subject_ids[0] for item in _find(report, CorpusDiscrepancyKind.COUNT_DRIFT)
    }
    # Incremental events updated source and vector to their observed values;
    # deep reconciliation reports drift only where the materialized count
    # differs from the observed count, so zero-valued reject and orphan
    # categories match and produce no finding.
    assert drift_kinds == {
        "normalized",
        "decomposition",
        "graph",
        "release",
    }
    entry = store.snapshot().entries[0]
    assert entry.core_watermark == 2
    assert entry.counts.decomposition == 3

    projector.replay((event(3, "graph", 12),), store, observed_at=NOW)
    duplicate = projector.replay((event(3, "graph", 12),), store, observed_at=NOW)
    entry = duplicate.entries[0]
    assert entry.core_watermark == 3
    assert entry.counts.graph == 12

    view = BoundedCorpusHealthReader(
        store, budget_ms=100, degraded_lag_events=1000
    ).read()
    assert view.status is CorpusHealthStatus.HEALTHY
    assert view.counts is not None
    assert view.counts.source == 3
    assert view.counts.graph == 12
    assert view.counts.decomposition == 3


def test_health_and_coverage_are_reported_separately() -> None:
    store = _seeded_store(counts={"source": 3})
    reader = BoundedCorpusHealthReader(store, budget_ms=100, degraded_lag_events=1000)
    reconciler = _reconciler(
        store,
        deadline_seconds=300.0,
        monotonic=SteppedMonotonic(step=1000.0),
    )
    incomplete = reconciler.run(tenant_id=TENANT_ID)
    assert incomplete.complete is False
    # Health still answers from the materialized registry and reports the
    # missing reconciliation as degradation, never as a coverage claim.
    view = reader.read()
    assert view.status is CorpusHealthStatus.DEGRADED
    assert view.last_reconciled_at is None
    complete = _reconciler(store).run(tenant_id=TENANT_ID)
    assert complete.complete is True
    assert reader.read().status is CorpusHealthStatus.HEALTHY


def test_constructor_validates_ports_store_and_deadline() -> None:
    store = _seeded_store()
    with pytest.raises(ValueError):
        DeepCorpusReconciler(
            store=store,
            corpus=object(),  # type: ignore[arg-type]
            expectations=FakeExpectationPort((_expectation(),)),
            projections=FakeProjectionPort((_projection(),)),
        )
    with pytest.raises(ValueError):
        DeepCorpusReconciler(
            store=store,
            corpus=FakeCorpusPort((_inventory(),)),
            expectations=object(),  # type: ignore[arg-type]
            projections=FakeProjectionPort((_projection(),)),
        )
    with pytest.raises(ValueError):
        DeepCorpusReconciler(
            store=store,
            corpus=FakeCorpusPort((_inventory(),)),
            expectations=FakeExpectationPort((_expectation(),)),
            projections=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        DeepCorpusReconciler(
            store=object(),  # type: ignore[arg-type]
            corpus=FakeCorpusPort((_inventory(),)),
            expectations=FakeExpectationPort((_expectation(),)),
            projections=FakeProjectionPort((_projection(),)),
        )
    with pytest.raises(ValueError):
        DeepCorpusReconciler(
            store=store,
            corpus=FakeCorpusPort((_inventory(),)),
            expectations=FakeExpectationPort((_expectation(),)),
            projections=FakeProjectionPort((_projection(),)),
            deadline_seconds=0.0,
        )


def test_report_shape_is_validated() -> None:
    with pytest.raises(ValidationError):
        CorpusReconciliationReport(
            tenant_id=TENANT_ID,
            started_at=NOW,
            complete=False,
            releases_reconciled=1,
        )
    with pytest.raises(ValidationError):
        CorpusReconciliationReport(
            tenant_id=TENANT_ID,
            started_at=NOW,
            complete=True,
            releases_reconciled=1,
            completed_at=None,
        )
    with pytest.raises(ValidationError):
        CorpusReconciliationReport(
            tenant_id=TENANT_ID,
            started_at=datetime(2026, 8, 22, 12, 0),
            complete=True,
            releases_reconciled=1,
            completed_at=NOW,
        )


def test_inventory_and_expectation_shapes_are_validated() -> None:
    with pytest.raises(ValidationError):
        CorpusInventory(
            tenant_id=TENANT_ID,
            release_id="release-1",
            sources=_pins("source-1", "source-1"),
            normalized_document_ids=("doc-1",),
            decomposition_document_ids=("doc-1",),
            reject_count=0,
            release_count=1,
        )
    with pytest.raises(ValidationError):
        CorpusReleaseExpectation(
            tenant_id=TENANT_ID,
            release_id="release-1",
            sources=_pins("source-1"),
            normalized_document_ids=("doc-1", "doc-1"),
            reject_count=0,
        )
    with pytest.raises(ValidationError):
        ProjectionInventory(
            tenant_id=TENANT_ID,
            release_id="release-1",
            vector_source_ids=("source-1", "source-1"),
            graph_entity_count=1,
            lexical_watermark=1,
            vector_watermark=1,
            graph_watermark=1,
        )
