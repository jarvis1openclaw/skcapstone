"""Bounded corpus health tests: budget, fail-closed shape, and no-scan."""

from __future__ import annotations

import inspect
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sklegal_retrieval.corpus_health import (
    BoundedCorpusHealthReader,
    CorpusHealthStatus,
    CorpusHealthUnavailableReason,
    CorpusHealthView,
)
from sklegal_retrieval.corpus_registry import (
    CorpusCountKind,
    CorpusRegistrySnapshot,
    InMemoryCorpusRegistryStore,
    zero_corpus_counts,
)
from sklegal_retrieval.models import ProjectionLag

from tests.support.retrieval_pins import OTHER_TENANT_ID, TENANT_ID

# The corpus registry contract pins the user-facing health budget at 100 ms.
CONTRACT_BUDGET_MS = 100
CONTRACT_DEGRADED_LAG_EVENTS = 1000
RECONCILED_AT = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
LATER_RECONCILED_AT = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)


class CountingStore:
    """Registry store double that counts snapshot reads."""

    def __init__(self, snapshot: CorpusRegistrySnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_calls = 0

    def snapshot(self) -> CorpusRegistrySnapshot:
        self.snapshot_calls += 1
        return self._snapshot


class WedgedStore:
    """Registry store double whose snapshot never returns in time."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def snapshot(self) -> CorpusRegistrySnapshot:
        self.release.wait(timeout=30)
        raise AssertionError("a wedged snapshot must never be consumed")


class CrashingStore:
    """Registry store double that fails like an outage."""

    def snapshot(self) -> CorpusRegistrySnapshot:
        raise RuntimeError("registry store outage")


class JunkStore:
    """Registry store double that returns malformed state."""

    def snapshot(self) -> object:
        return {"entries": "not-a-snapshot"}


class SteppedMonotonic:
    """Deterministic clock that advances by a fixed step per call."""

    def __init__(self, step: float) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def _entry(
    tenant_id: UUID,
    release_ordinal: int,
    *,
    reconciled: datetime | None = RECONCILED_AT,
    lag_events: int = 0,
    counts: dict[str, int] | None = None,
) -> Any:
    from sklegal_retrieval.corpus_registry import CorpusRegistryEntry

    materialized = zero_corpus_counts()
    for kind, count in (counts or {}).items():
        materialized = materialized.with_count(CorpusCountKind(kind), count)
    return CorpusRegistryEntry(
        tenant_id=tenant_id,
        release_id=f"release-{release_ordinal}",
        counts=materialized,
        core_watermark=5,
        core_event_sha256="a" * 64,
        required_replay_lsn="0/10",
        lag=ProjectionLag(lag_events=lag_events, lag_seconds=0.0),
        last_reconciled_at=reconciled,
    )


def _reader(
    store: Any,
    *,
    budget_ms: int = CONTRACT_BUDGET_MS,
    degraded_lag_events: int = CONTRACT_DEGRADED_LAG_EVENTS,
) -> BoundedCorpusHealthReader:
    return BoundedCorpusHealthReader(
        store,
        budget_ms=budget_ms,
        degraded_lag_events=degraded_lag_events,
    )


def test_read_aggregates_counts_across_tenants_and_releases() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(
            _entry(
                TENANT_ID,
                1,
                counts={"source": 10, "vector": 8, "release": 1},
                lag_events=4,
            ),
            _entry(
                TENANT_ID,
                2,
                counts={"source": 7, "reject": 2},
                lag_events=9,
                reconciled=LATER_RECONCILED_AT,
            ),
            _entry(
                OTHER_TENANT_ID,
                1,
                counts={"source": 5, "graph": 12},
                lag_events=2,
            ),
        ),
    )
    view = _reader(CountingStore(snapshot)).read()
    assert view.status is CorpusHealthStatus.HEALTHY
    assert view.unavailable_reason is None
    assert view.counts is not None
    assert view.counts.source == 22
    assert view.counts.vector == 8
    assert view.counts.graph == 12
    assert view.counts.reject == 2
    assert view.counts.release == 1
    assert view.entry_count == 3
    assert view.tenant_count == 2
    assert view.lag is not None and view.lag.lag_events == 9
    assert view.last_reconciled_at == RECONCILED_AT
    assert view.budget_ms == CONTRACT_BUDGET_MS


def test_never_reconciled_entry_reports_degraded() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1, reconciled=None),),
    )
    view = _reader(CountingStore(snapshot)).read()
    assert view.status is CorpusHealthStatus.DEGRADED
    assert view.counts is not None
    assert view.last_reconciled_at is None


def test_lag_over_the_pinned_threshold_reports_degraded() -> None:
    lagging = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1, lag_events=CONTRACT_DEGRADED_LAG_EVENTS + 1),),
    )
    view = _reader(CountingStore(lagging)).read()
    assert view.status is CorpusHealthStatus.DEGRADED
    at_threshold = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1, lag_events=CONTRACT_DEGRADED_LAG_EVENTS),),
    )
    view = _reader(CountingStore(at_threshold)).read()
    assert view.status is CorpusHealthStatus.HEALTHY


def test_budget_breach_fails_closed_with_null_counts() -> None:
    store = WedgedStore()
    started = time.perf_counter()
    view = _reader(store, budget_ms=40).read()
    elapsed = time.perf_counter() - started
    store.release.set()
    assert view.status is CorpusHealthStatus.UNAVAILABLE
    assert view.unavailable_reason is CorpusHealthUnavailableReason.BUDGET_EXCEEDED
    assert view.counts is None
    assert view.entry_count == 0
    assert view.tenant_count == 0
    assert view.lag is None
    assert view.last_reconciled_at is None
    assert elapsed < 1.0


def test_budget_accounting_is_enforced_even_when_the_store_is_fast() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1),),
    )
    # Two clock reads happen on the happy path: read start and post-join
    # check. Advancing past the budget between them must fail closed.
    clock = SteppedMonotonic(step=float(CONTRACT_BUDGET_MS + 1))
    reader = BoundedCorpusHealthReader(
        CountingStore(snapshot),
        budget_ms=CONTRACT_BUDGET_MS,
        degraded_lag_events=CONTRACT_DEGRADED_LAG_EVENTS,
        monotonic=clock,
    )
    view = reader.read()
    assert view.status is CorpusHealthStatus.UNAVAILABLE
    assert view.unavailable_reason is CorpusHealthUnavailableReason.BUDGET_EXCEEDED


def test_store_outage_fails_closed() -> None:
    view = _reader(CrashingStore()).read()
    assert view.status is CorpusHealthStatus.UNAVAILABLE
    assert view.unavailable_reason is CorpusHealthUnavailableReason.STORE_UNAVAILABLE
    assert view.counts is None


def test_invalid_state_fails_closed() -> None:
    view = _reader(JunkStore()).read()
    assert view.status is CorpusHealthStatus.UNAVAILABLE
    assert view.unavailable_reason is CorpusHealthUnavailableReason.INVALID_STATE
    assert view.counts is None


def test_failed_read_never_reports_zero_counts() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1, counts={"source": 3}),),
    )
    stores = (
        WedgedStore(),
        CrashingStore(),
        JunkStore(),
    )
    try:
        for store in stores:
            view = _reader(store, budget_ms=25).read()
            assert view.status is CorpusHealthStatus.UNAVAILABLE
            assert view.counts is None
            assert view.unavailable_reason is not None
    finally:
        for store in stores:
            if isinstance(store, WedgedStore):
                store.release.set()
    assert _reader(CountingStore(snapshot)).read().counts is not None


def test_large_materialized_tree_answers_inside_the_fixed_budget() -> None:
    entries = []
    tenants = 200
    releases = 10
    for tenant_ordinal in range(tenants):
        tenant = UUID(int=UUID(int=0).int + tenant_ordinal + 1)
        for release_ordinal in range(1, releases + 1):
            entries.append(
                _entry(
                    tenant,
                    release_ordinal,
                    counts={
                        "source": 40,
                        "normalized": 38,
                        "decomposition": 36,
                        "vector": 34,
                        "graph": 500,
                        "reject": 1,
                        "orphan": 0,
                        "release": 1,
                    },
                )
            )
    store = InMemoryCorpusRegistryStore()
    store.seed(tuple(entries))
    reader = _reader(store)
    started = time.perf_counter()
    view = reader.read()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert view.status is CorpusHealthStatus.HEALTHY
    assert view.entry_count == tenants * releases
    assert view.tenant_count == tenants
    assert view.counts is not None
    assert view.counts.source == tenants * releases * 40
    assert view.counts.graph == tenants * releases * 500
    assert elapsed_ms < CONTRACT_BUDGET_MS


def test_read_performs_exactly_one_bounded_snapshot_read() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1, counts={"source": 1}),),
    )
    store = CountingStore(snapshot)
    view = _reader(store).read()
    assert view.status is CorpusHealthStatus.HEALTHY
    assert store.snapshot_calls == 1


def test_reader_holds_no_corpus_scan_or_projection_port() -> None:
    signature = inspect.signature(BoundedCorpusHealthReader.__init__)
    parameters = set(signature.parameters) - {"self"}
    assert parameters == {"store", "budget_ms", "degraded_lag_events", "monotonic"}
    source = inspect.getsource(
        __import__(
            "sklegal_retrieval.corpus_health", fromlist=["BoundedCorpusHealthReader"]
        )
    )
    for forbidden in (
        "os.walk",
        "rglob",
        "glob(",
        "CorpusInventory",
        "ProjectionInventory",
        "ReleaseExpectation",
        "CorpusInventoryPort",
    ):
        assert forbidden not in source


def test_empty_registry_reads_as_a_successful_zero_view() -> None:
    store = InMemoryCorpusRegistryStore()
    view = _reader(store).read()
    assert view.status is CorpusHealthStatus.HEALTHY
    assert view.counts is not None
    assert view.counts == zero_corpus_counts()
    assert view.entry_count == 0
    assert view.tenant_count == 0
    assert view.last_reconciled_at is None


def test_view_shape_is_validated() -> None:
    healthy_counts = zero_corpus_counts()
    with pytest.raises(ValidationError):
        CorpusHealthView(
            status=CorpusHealthStatus.HEALTHY,
            budget_ms=CONTRACT_BUDGET_MS,
            counts=None,
            entry_count=0,
            tenant_count=0,
        )
    with pytest.raises(ValidationError):
        CorpusHealthView(
            status=CorpusHealthStatus.UNAVAILABLE,
            budget_ms=CONTRACT_BUDGET_MS,
            counts=healthy_counts,
            entry_count=0,
            tenant_count=0,
            unavailable_reason=CorpusHealthUnavailableReason.STORE_UNAVAILABLE,
        )
    with pytest.raises(ValidationError):
        CorpusHealthView(
            status=CorpusHealthStatus.UNAVAILABLE,
            budget_ms=CONTRACT_BUDGET_MS,
            counts=None,
            entry_count=0,
            tenant_count=0,
        )
    with pytest.raises(ValidationError):
        CorpusHealthView(
            status=CorpusHealthStatus.HEALTHY,
            budget_ms=CONTRACT_BUDGET_MS,
            counts=healthy_counts,
            entry_count=0,
            tenant_count=0,
            last_reconciled_at=datetime(2026, 8, 22, 10, 0),
        )


def test_reader_rejects_invalid_construction() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1),),
    )
    with pytest.raises(ValueError):
        BoundedCorpusHealthReader(
            object(),  # type: ignore[arg-type]
            budget_ms=CONTRACT_BUDGET_MS,
            degraded_lag_events=CONTRACT_DEGRADED_LAG_EVENTS,
        )
    with pytest.raises(ValueError):
        BoundedCorpusHealthReader(
            CountingStore(snapshot), budget_ms=0, degraded_lag_events=1
        )
    with pytest.raises(ValueError):
        BoundedCorpusHealthReader(
            CountingStore(snapshot),
            budget_ms=CONTRACT_BUDGET_MS,
            degraded_lag_events=-1,
        )


def test_unique_tenant_count_ignores_release_multiplicity() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(
            _entry(TENANT_ID, 1),
            _entry(TENANT_ID, 2, reconciled=LATER_RECONCILED_AT),
            _entry(OTHER_TENANT_ID, 1, reconciled=LATER_RECONCILED_AT),
        ),
    )
    view = _reader(CountingStore(snapshot)).read()
    assert view.tenant_count == 2
    assert view.last_reconciled_at == LATER_RECONCILED_AT - timedelta(hours=1)


def test_snapshot_generator_is_not_trusted_beyond_entries() -> None:
    # A stale snapshot time never fabricates reconciliation: only per-entry
    # last_reconciled_at feeds the view.
    stale_generated = CorpusRegistrySnapshot(
        generated_at=datetime(2020, 1, 1, tzinfo=UTC),
        entries=(_entry(TENANT_ID, 1, reconciled=None),),
    )
    view = _reader(CountingStore(stale_generated)).read()
    assert view.status is CorpusHealthStatus.DEGRADED
    assert view.last_reconciled_at is None


def test_uuid4_tenant_ids_are_supported() -> None:
    snapshot = CorpusRegistrySnapshot(
        generated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        entries=(_entry(uuid4(), 1),),
    )
    view = _reader(CountingStore(snapshot)).read()
    assert view.status is CorpusHealthStatus.HEALTHY
    assert view.tenant_count == 1
