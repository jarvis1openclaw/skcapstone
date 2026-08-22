"""Materialized corpus registry tests: counts, idempotent projection, LSN pins."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_retrieval.corpus_registry import (
    CORPUS_REGISTRY_SCHEMA,
    CorpusCountKind,
    CorpusCounts,
    CorpusRegistryEntry,
    CorpusRegistryEvent,
    CorpusRegistryProjector,
    CorpusRegistrySnapshot,
    InMemoryCorpusRegistryStore,
    zero_corpus_counts,
)
from sklegal_retrieval.errors import (
    RetrievalIntegrityError,
    RetrievalUnavailableError,
)
from sklegal_retrieval.models import ProjectionLag

from tests.support.retrieval_pins import (
    HASH_A,
    HASH_B,
    OTHER_TENANT_ID,
    TENANT_ID,
)

OBSERVED_AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
RECONCILED_AT = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)
LSN_ONE = "0/10"
LSN_TWO = "0/20"


class FakeLsnIndex:
    """Watermark-to-LSN index stub with a pinned mapping."""

    def __init__(self, mapping: dict[int, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.queries: list[int] = []

    def required_lsn(self, *, event_sequence: int) -> str | None:
        self.queries.append(event_sequence)
        return self.mapping.get(event_sequence)


class NullLsnIndex:
    """Index stub that cannot pin the LSN for any watermark."""

    def required_lsn(self, *, event_sequence: int) -> None:
        del event_sequence
        return None


class ConstantLsnIndex:
    """Index stub that pins one LSN for every watermark."""

    def __init__(self, lsn: str = LSN_ONE) -> None:
        self.lsn = lsn

    def required_lsn(self, *, event_sequence: int) -> str:
        del event_sequence
        return self.lsn


class UnavailableStore:
    """Store stub whose every operation fails like an outage."""

    def snapshot(self) -> object:
        raise RuntimeError("store outage")

    def apply(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise RuntimeError("store outage")

    def reconcile(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("store outage")


def _event(
    sequence: int,
    *,
    kind: CorpusCountKind = CorpusCountKind.SOURCE,
    count: int = 1,
    tenant_id: UUID = TENANT_ID,
    release_id: str = "release-1",
    key: str | None = None,
    sha: str = HASH_B,
    lag: ProjectionLag | None = None,
) -> CorpusRegistryEvent:
    return CorpusRegistryEvent(
        event_sequence=sequence,
        idempotency_key=key or f"event-{sequence}",
        tenant_id=tenant_id,
        release_id=release_id,
        kind=kind,
        count=count,
        core_event_sha256=sha,
        lag=lag or ProjectionLag(lag_events=0, lag_seconds=0.0),
    )


def _entry(
    *,
    tenant_id: UUID = TENANT_ID,
    release_id: str = "release-1",
    counts: CorpusCounts | None = None,
    watermark: int = 5,
    lsn: str | None = LSN_ONE,
    lag: ProjectionLag | None = None,
    reconciled: datetime | None = None,
) -> CorpusRegistryEntry:
    return CorpusRegistryEntry(
        tenant_id=tenant_id,
        release_id=release_id,
        counts=counts or zero_corpus_counts(),
        core_watermark=watermark,
        core_event_sha256=HASH_A if watermark else None,
        required_replay_lsn=lsn if watermark else None,
        lag=lag or ProjectionLag(lag_events=0, lag_seconds=0.0),
        last_reconciled_at=reconciled,
    )


def test_zero_baseline_covers_every_materialized_count_kind() -> None:
    counts = zero_corpus_counts()
    assert set(CorpusCountKind) == {
        CorpusCountKind.SOURCE,
        CorpusCountKind.NORMALIZED,
        CorpusCountKind.DECOMPOSITION,
        CorpusCountKind.VECTOR,
        CorpusCountKind.GRAPH,
        CorpusCountKind.REJECT,
        CorpusCountKind.ORPHAN,
        CorpusCountKind.RELEASE,
    }
    for kind in CorpusCountKind:
        assert counts.count_for(kind) == 0


def test_counts_with_count_revalidates_and_rejects_negatives() -> None:
    counts = zero_corpus_counts().with_count(CorpusCountKind.SOURCE, 12)
    assert counts.count_for(CorpusCountKind.SOURCE) == 12
    assert counts.count_for(CorpusCountKind.VECTOR) == 0
    with pytest.raises(ValidationError):
        counts.with_count(CorpusCountKind.SOURCE, -1)
    with pytest.raises(ValidationError):
        CorpusCounts(source=-1)


def test_counts_are_immutable_values() -> None:
    counts = zero_corpus_counts()
    with pytest.raises(ValidationError):
        counts.source = 3


def test_entry_watermark_shape_must_agree_with_pins() -> None:
    assert _entry(watermark=5).core_event_sha256 == HASH_A
    assert _entry(watermark=0, lsn=None).core_event_sha256 is None
    with pytest.raises(ValidationError):
        CorpusRegistryEntry(
            tenant_id=TENANT_ID,
            release_id="release-1",
            counts=zero_corpus_counts(),
            core_watermark=0,
            core_event_sha256=HASH_A,
        )
    with pytest.raises(ValidationError):
        CorpusRegistryEntry(
            tenant_id=TENANT_ID,
            release_id="release-1",
            counts=zero_corpus_counts(),
            core_watermark=5,
            core_event_sha256=None,
        )
    with pytest.raises(ValidationError):
        CorpusRegistryEntry(
            tenant_id=TENANT_ID,
            release_id="release-1",
            counts=zero_corpus_counts(),
            core_watermark=5,
            core_event_sha256=HASH_A,
            required_replay_lsn=None,
        )


def test_entry_requires_timezone_aware_reconciled_time() -> None:
    with pytest.raises(ValidationError):
        _entry(reconciled=datetime(2026, 8, 22, 11, 0))
    assert _entry(reconciled=RECONCILED_AT).last_reconciled_at == RECONCILED_AT


def test_snapshot_entries_must_be_unique_per_tenant_and_release() -> None:
    first = _entry(release_id="release-1")
    second = _entry(release_id="release-2")
    snapshot = CorpusRegistrySnapshot(
        generated_at=OBSERVED_AT,
        entries=(first, second),
    )
    # revalidate_instances="always" rebuilds nested values, so compare by
    # equality rather than identity.
    assert snapshot.entry_for(tenant_id=TENANT_ID, release_id="release-1") == first
    assert snapshot.entry_for(tenant_id=TENANT_ID, release_id="release-9") is None
    assert snapshot.entry_for(tenant_id=OTHER_TENANT_ID, release_id="release-1") is None
    with pytest.raises(ValidationError):
        CorpusRegistrySnapshot(
            generated_at=OBSERVED_AT,
            entries=(first, _entry(release_id="release-1", watermark=6)),
        )
    with pytest.raises(ValidationError):
        CorpusRegistrySnapshot(generated_at=datetime(2026, 8, 22, 12, 0), entries=())


def test_replay_applies_events_and_pins_watermark_lsn_and_lag() -> None:
    index = FakeLsnIndex({1: LSN_ONE, 2: LSN_TWO})
    store = InMemoryCorpusRegistryStore()
    lag = ProjectionLag(lag_events=4, lag_seconds=2.5)
    snapshot = CorpusRegistryProjector(index).replay(
        (
            _event(1, kind=CorpusCountKind.SOURCE, count=7, sha=HASH_A),
            _event(2, kind=CorpusCountKind.VECTOR, count=5, sha=HASH_B, lag=lag),
        ),
        store,
        observed_at=OBSERVED_AT,
    )
    entry = snapshot.entry_for(tenant_id=TENANT_ID, release_id="release-1")
    assert entry is not None
    assert entry.counts.source == 7
    assert entry.counts.vector == 5
    assert entry.core_watermark == 2
    assert entry.core_event_sha256 == HASH_B
    assert entry.required_replay_lsn == LSN_TWO
    assert entry.lag == lag
    assert index.queries == [1, 2]


def test_replay_is_idempotent_under_duplicate_delivery() -> None:
    events = (
        _event(1, kind=CorpusCountKind.SOURCE, count=7),
        _event(2, kind=CorpusCountKind.RELEASE, count=1),
    )
    store = InMemoryCorpusRegistryStore()
    projector = CorpusRegistryProjector(ConstantLsnIndex())
    first = projector.replay(events, store, observed_at=OBSERVED_AT)
    second = projector.replay(events, store, observed_at=OBSERVED_AT)
    third = projector.replay(events[-1:], store, observed_at=OBSERVED_AT)
    assert first.canonical_sha256() == second.canonical_sha256()
    assert first.canonical_sha256() == third.canonical_sha256()


def test_absolute_counts_make_rebuild_converge_to_incremental_state() -> None:
    events = (
        _event(1, kind=CorpusCountKind.SOURCE, count=10),
        _event(2, kind=CorpusCountKind.NORMALIZED, count=9),
        _event(3, kind=CorpusCountKind.DECOMPOSITION, count=8),
        _event(4, kind=CorpusCountKind.VECTOR, count=7),
        _event(5, kind=CorpusCountKind.GRAPH, count=6),
    )
    incremental = InMemoryCorpusRegistryStore()
    CorpusRegistryProjector(ConstantLsnIndex()).replay(
        events, incremental, observed_at=OBSERVED_AT
    )
    rebuilt = InMemoryCorpusRegistryStore()
    CorpusRegistryProjector(ConstantLsnIndex()).replay(
        events, rebuilt, observed_at=OBSERVED_AT + timedelta(minutes=5)
    )
    # The rebuild observation time differs, so compare materialized rows,
    # not the snapshot envelope that carries the generation time.
    assert incremental.snapshot().entries == rebuilt.snapshot().entries


def test_replay_rejects_non_advancing_sequences() -> None:
    store = InMemoryCorpusRegistryStore()
    projector = CorpusRegistryProjector(ConstantLsnIndex())
    with pytest.raises(RetrievalIntegrityError):
        projector.replay(
            (_event(2), _event(1)),
            store,
            observed_at=OBSERVED_AT,
        )
    with pytest.raises(RetrievalIntegrityError):
        projector.replay(
            (_event(2), _event(2, key="other-key")),
            store,
            observed_at=OBSERVED_AT,
        )


def test_apply_rejects_an_event_that_does_not_advance_the_watermark() -> None:
    store = InMemoryCorpusRegistryStore()
    store.apply(_event(5), required_replay_lsn=LSN_ONE, observed_at=OBSERVED_AT)
    with pytest.raises(RetrievalIntegrityError):
        store.apply(
            _event(5, key="other-key"),
            required_replay_lsn=LSN_ONE,
            observed_at=OBSERVED_AT,
        )


def test_apply_validates_payload_and_observation_time() -> None:
    store = InMemoryCorpusRegistryStore()
    with pytest.raises(RetrievalIntegrityError):
        store.apply(
            "not-an-event",  # type: ignore[arg-type]
            required_replay_lsn=None,
            observed_at=OBSERVED_AT,
        )
    with pytest.raises(RetrievalIntegrityError):
        store.apply(
            _event(1),
            required_replay_lsn=LSN_ONE,
            observed_at=datetime(2026, 8, 22, 12, 0),
        )


def test_unknown_lsn_fails_the_apply_closed() -> None:
    store = InMemoryCorpusRegistryStore()
    with pytest.raises(RetrievalIntegrityError):
        CorpusRegistryProjector(NullLsnIndex()).replay(
            (_event(1, count=3),),
            store,
            observed_at=OBSERVED_AT,
        )
    assert store.snapshot().entries == ()


def test_events_track_releases_and_tenants_independently() -> None:
    store = InMemoryCorpusRegistryStore()
    snapshot = CorpusRegistryProjector(ConstantLsnIndex()).replay(
        (
            _event(1, kind=CorpusCountKind.SOURCE, count=2),
            _event(
                2,
                kind=CorpusCountKind.SOURCE,
                count=4,
                release_id="release-2",
            ),
            _event(
                3,
                kind=CorpusCountKind.SOURCE,
                count=6,
                tenant_id=OTHER_TENANT_ID,
                release_id="release-1",
            ),
        ),
        store,
        observed_at=OBSERVED_AT,
    )
    assert len(snapshot.entries) == 3
    first = snapshot.entry_for(tenant_id=TENANT_ID, release_id="release-1")
    second = snapshot.entry_for(tenant_id=TENANT_ID, release_id="release-2")
    other = snapshot.entry_for(tenant_id=OTHER_TENANT_ID, release_id="release-1")
    assert first is not None and first.counts.source == 2
    assert second is not None and second.counts.source == 4
    assert other is not None and other.counts.source == 6


def test_seed_installs_state_and_snapshot_orders_entries() -> None:
    store = InMemoryCorpusRegistryStore()
    store.seed(
        (
            _entry(tenant_id=OTHER_TENANT_ID, release_id="release-2"),
            _entry(tenant_id=TENANT_ID, release_id="release-1"),
        )
    )
    snapshot = store.snapshot()
    assert [
        (entry.tenant_id, entry.release_id) for entry in snapshot.entries
    ] == sorted((entry.tenant_id, entry.release_id) for entry in snapshot.entries)


def test_reconcile_is_gated_on_the_snapshot_it_read() -> None:
    store = InMemoryCorpusRegistryStore()
    store.seed((_entry(release_id="release-1"),))
    before = store.snapshot()
    corrected = (
        _entry(
            release_id="release-1",
            counts=zero_corpus_counts().with_count(CorpusCountKind.SOURCE, 3),
            reconciled=RECONCILED_AT,
        ),
    )
    result = store.reconcile(
        corrected,
        expected_generated_at=before.generated_at,
        reconciled_at=OBSERVED_AT,
    )
    assert result is not None
    entry = result.entry_for(tenant_id=TENANT_ID, release_id="release-1")
    assert entry is not None
    assert entry.counts.source == 3
    assert entry.last_reconciled_at == RECONCILED_AT
    stale = store.reconcile(
        corrected,
        expected_generated_at=before.generated_at - timedelta(seconds=1),
        reconciled_at=OBSERVED_AT,
    )
    assert stale is None


def test_reconcile_preserves_watermark_lsn_and_lag_pins() -> None:
    store = InMemoryCorpusRegistryStore()
    lag = ProjectionLag(lag_events=9, lag_seconds=4.0)
    store.seed((_entry(watermark=5, lsn=LSN_ONE, lag=lag),))
    before = store.snapshot()
    result = store.reconcile(
        (
            _entry(
                counts=zero_corpus_counts().with_count(CorpusCountKind.GRAPH, 2),
                watermark=0,
                lsn=None,
                reconciled=RECONCILED_AT,
            ),
        ),
        expected_generated_at=before.generated_at,
        reconciled_at=OBSERVED_AT,
    )
    assert result is not None
    entry = result.entries[0]
    assert entry.core_watermark == 5
    assert entry.core_event_sha256 == HASH_A
    assert entry.required_replay_lsn == LSN_ONE
    assert entry.lag == lag
    assert entry.counts.graph == 2


def test_projector_requires_an_lsn_index() -> None:
    with pytest.raises(ValueError):
        CorpusRegistryProjector(object())  # type: ignore[arg-type]


def test_projector_wraps_store_outage_as_unavailable() -> None:
    with pytest.raises(RetrievalUnavailableError):
        CorpusRegistryProjector(ConstantLsnIndex()).replay(
            (_event(1),),
            UnavailableStore(),  # type: ignore[arg-type]
            observed_at=OBSERVED_AT,
        )


def test_schema_identifier_is_pinned() -> None:
    assert CORPUS_REGISTRY_SCHEMA == "sklegal-corpus-registry/v1"
