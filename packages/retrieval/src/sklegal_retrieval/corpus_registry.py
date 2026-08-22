"""Materialized corpus registry: counts, watermarks, and reconciled state.

The registry is the only read source for the bounded corpus health view. It
materializes content-free counts (source, normalized, decomposition, vector,
graph, reject, orphan, and release) per Tenant and release, plus the core
outbox watermark, the replica gating LSN, projection lag, and the
last-reconciled state. Incremental updates arrive through the core
transactional outbox as idempotent events; deep counts are corrected only by
the scheduled reconciliation job in ``corpus_reconciliation``. No code path
in this module scans a corpus tree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .errors import RetrievalIntegrityError, RetrievalUnavailableError
from .models import (
    OpaqueId,
    PostgresLsn,
    ProjectionLag,
    ReplicaPins,
    RetrievalValue,
    Sha256,
)

CORPUS_REGISTRY_SCHEMA = "sklegal-corpus-registry/v1"


class CorpusCountKind(StrEnum):
    """Closed vocabulary of materialized corpus count categories."""

    SOURCE = "source"
    NORMALIZED = "normalized"
    DECOMPOSITION = "decomposition"
    VECTOR = "vector"
    GRAPH = "graph"
    REJECT = "reject"
    ORPHAN = "orphan"
    RELEASE = "release"


class CorpusCounts(RetrievalValue):
    """Content-free materialized counts for one Tenant and release."""

    source: int = Field(ge=0)
    normalized: int = Field(ge=0)
    decomposition: int = Field(ge=0)
    vector: int = Field(ge=0)
    graph: int = Field(ge=0)
    reject: int = Field(ge=0)
    orphan: int = Field(ge=0)
    release: int = Field(ge=0)

    def count_for(self, kind: CorpusCountKind) -> int:
        """Return the materialized count for one category."""

        return int(getattr(self, kind.value))

    def with_count(self, kind: CorpusCountKind, count: int) -> CorpusCounts:
        """Return a revalidated copy with one category replaced."""

        return CorpusCounts.model_validate(
            {**self.model_dump(), kind.value: count}
        )


def zero_corpus_counts() -> CorpusCounts:
    """Return the all-zero count baseline for a new registry entry."""

    return CorpusCounts(
        source=0,
        normalized=0,
        decomposition=0,
        vector=0,
        graph=0,
        reject=0,
        orphan=0,
        release=0,
    )


class CorpusRegistryEntry(RetrievalValue):
    """One materialized registry row for one Tenant and release.

    Carries only counts, opaque identifiers, digests, watermarks, and times.
    ``required_replay_lsn`` is the watermark-to-LSN mapping the replica gate
    consumes; the registry producer populates it from the core outbox.
    """

    tenant_id: UUID
    release_id: OpaqueId
    counts: CorpusCounts
    core_watermark: int = Field(ge=0)
    core_event_sha256: Sha256 | None = None
    required_replay_lsn: PostgresLsn | None = None
    lag: ProjectionLag = Field(
        default_factory=lambda: ProjectionLag(lag_events=0, lag_seconds=0.0)
    )
    last_reconciled_at: datetime | None = None

    @model_validator(mode="after")
    def validate_watermark_shape(self) -> Self:
        if (self.core_watermark == 0) != (self.core_event_sha256 is None):
            raise ValueError("corpus registry watermark shape disagrees")
        if (self.core_watermark == 0) != (self.required_replay_lsn is None):
            raise ValueError("corpus registry replay-LSN shape disagrees")
        if self.last_reconciled_at is not None and (
            self.last_reconciled_at.tzinfo is None
        ):
            raise ValueError("reconciled time must be timezone aware")
        return self

    def replica_pins(self, replica_replay_lsn: str) -> ReplicaPins:
        """Build replica gating pins from the materialized mapping."""

        return ReplicaPins(
            replica_replay_lsn=replica_replay_lsn,
            required_replay_lsn=self.required_replay_lsn,
        )


class CorpusRegistrySnapshot(RetrievalValue):
    """One consistent immutable view of every materialized registry row."""

    generated_at: datetime
    entries: tuple[CorpusRegistryEntry, ...] = ()

    @field_validator("generated_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("snapshot time must be timezone aware")
        return value

    @model_validator(mode="after")
    def validate_unique_entries(self) -> Self:
        keys = [(entry.tenant_id, entry.release_id) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("corpus registry entries must be unique per release")
        return self

    def entry_for(
        self, *, tenant_id: UUID, release_id: str
    ) -> CorpusRegistryEntry | None:
        """Return the materialized row for one Tenant and release, if any."""

        for entry in self.entries:
            if entry.tenant_id == tenant_id and entry.release_id == release_id:
                return entry
        return None


class CorpusRegistryEvent(RetrievalValue):
    """One ordered, idempotent incremental count update from the outbox.

    Carries the absolute observed count for one category, never a delta, so
    duplicate or replayed delivery converges to the same materialized state.
    """

    event_sequence: int = Field(ge=1)
    idempotency_key: OpaqueId
    tenant_id: UUID
    release_id: OpaqueId
    kind: CorpusCountKind
    count: int = Field(ge=0)
    core_event_sha256: Sha256
    lag: ProjectionLag = Field(
        default_factory=lambda: ProjectionLag(lag_events=0, lag_seconds=0.0)
    )


class CoreWatermarkLsnIndex(Protocol):
    """Maps a core outbox watermark to the replica gating LSN."""

    def required_lsn(self, *, event_sequence: int) -> PostgresLsn | None:
        """Return the required replay LSN for one core watermark, if known."""


class CorpusRegistryStore(Protocol):
    """Atomic materialized registry persistence owned by the core cluster."""

    def snapshot(self) -> CorpusRegistrySnapshot:
        """Return one consistent immutable view; bounded, never a scan."""

    def apply(
        self,
        event: CorpusRegistryEvent,
        *,
        required_replay_lsn: PostgresLsn | None,
        observed_at: datetime,
    ) -> bool:
        """Apply one event; return False when its key was already applied."""

    def reconcile(
        self,
        entries: tuple[CorpusRegistryEntry, ...],
        *,
        expected_generated_at: datetime,
        reconciled_at: datetime,
    ) -> CorpusRegistrySnapshot | None:
        """Replace reconciled rows iff the snapshot is unchanged.

        Returns the new snapshot on success. Returns ``None`` when the
        materialized state moved under the reconciler so the run aborts
        without writing a stale correction.
        """


class InMemoryCorpusRegistryStore:
    """Synthetic test and isolated-development store, never production."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[tuple[UUID, str], CorpusRegistryEntry] = {}
        self._applied_keys: set[str] = set()
        self._generated_at = datetime(1970, 1, 1, tzinfo=UTC)

    def seed(self, entries: tuple[CorpusRegistryEntry, ...]) -> None:
        """Install the initial materialized state for tests and rebuilds."""

        with self._lock:
            self._entries = {
                (entry.tenant_id, entry.release_id): entry for entry in entries
            }

    def snapshot(self) -> CorpusRegistrySnapshot:
        with self._lock:
            return CorpusRegistrySnapshot(
                generated_at=self._generated_at,
                entries=tuple(self._entries[key] for key in sorted(self._entries)),
            )

    def apply(
        self,
        event: CorpusRegistryEvent,
        *,
        required_replay_lsn: PostgresLsn | None,
        observed_at: datetime,
    ) -> bool:
        if not isinstance(event, CorpusRegistryEvent):
            raise RetrievalIntegrityError("corpus registry event failed validation")
        if observed_at.tzinfo is None:
            raise RetrievalIntegrityError("registry observation time must be aware")
        with self._lock:
            if event.idempotency_key in self._applied_keys:
                return False
            self._applied_keys.add(event.idempotency_key)
            key = (event.tenant_id, event.release_id)
            existing = self._entries.get(key)
            if existing is None:
                counts = zero_corpus_counts().with_count(event.kind, event.count)
                entry = CorpusRegistryEntry(
                    tenant_id=event.tenant_id,
                    release_id=event.release_id,
                    counts=counts,
                    core_watermark=event.event_sequence,
                    core_event_sha256=event.core_event_sha256,
                    required_replay_lsn=required_replay_lsn,
                    lag=event.lag,
                )
            else:
                if event.event_sequence <= existing.core_watermark:
                    raise RetrievalIntegrityError(
                        "corpus registry events must advance the core watermark"
                    )
                entry = CorpusRegistryEntry.model_validate(
                    {
                        **existing.model_dump(),
                        "counts": existing.counts.with_count(
                            event.kind, event.count
                        ).model_dump(),
                        "core_watermark": event.event_sequence,
                        "core_event_sha256": event.core_event_sha256,
                        "required_replay_lsn": required_replay_lsn,
                        "lag": event.lag.model_dump(),
                    }
                )
            self._entries[key] = entry
            self._generated_at = observed_at
            return True

    def reconcile(
        self,
        entries: tuple[CorpusRegistryEntry, ...],
        *,
        expected_generated_at: datetime,
        reconciled_at: datetime,
    ) -> CorpusRegistrySnapshot | None:
        del reconciled_at
        with self._lock:
            if self._generated_at != expected_generated_at:
                return None
            for entry in entries:
                key = (entry.tenant_id, entry.release_id)
                existing = self._entries.get(key)
                if existing is None:
                    self._entries[key] = entry
                    continue
                self._entries[key] = CorpusRegistryEntry.model_validate(
                    {
                        **entry.model_dump(),
                        "core_watermark": existing.core_watermark,
                        "core_event_sha256": existing.core_event_sha256,
                        "required_replay_lsn": existing.required_replay_lsn,
                        "lag": existing.lag.model_dump(),
                    }
                )
            return CorpusRegistrySnapshot(
                generated_at=self._generated_at,
                entries=tuple(self._entries[key] for key in sorted(self._entries)),
            )


class CorpusRegistryProjector:
    """Replay pinned corpus outbox events into the materialized registry.

    Delivery is idempotent: the event idempotency key makes duplicates a
    no-op, and absolute per-category counts make replays converge. The
    producer fills the watermark-to-LSN mapping from the core index on every
    applied event so replica gating never reads a stale pin.
    """

    def __init__(self, lsn_index: CoreWatermarkLsnIndex) -> None:
        if not callable(getattr(lsn_index, "required_lsn", None)):
            raise ValueError("corpus registry requires a watermark LSN index")
        self._lsn_index = lsn_index

    def replay(
        self,
        events: tuple[CorpusRegistryEvent, ...],
        store: CorpusRegistryStore,
        *,
        observed_at: datetime,
    ) -> CorpusRegistrySnapshot:
        """Apply ordered events exactly once; return the visible snapshot."""

        previous = 0
        for event in events:
            if not isinstance(event, CorpusRegistryEvent):
                raise RetrievalIntegrityError("corpus registry event failed validation")
            if event.event_sequence <= previous:
                raise RetrievalIntegrityError(
                    "corpus registry events must arrive in strict sequence"
                )
            previous = event.event_sequence
            try:
                required_lsn = self._lsn_index.required_lsn(
                    event_sequence=event.event_sequence
                )
                store.apply(
                    event,
                    required_replay_lsn=required_lsn,
                    observed_at=observed_at,
                )
            except RetrievalIntegrityError:
                raise
            except Exception:
                raise RetrievalUnavailableError(
                    "corpus registry store is unavailable"
                ) from None
        return store.snapshot()


__all__ = [
    "CORPUS_REGISTRY_SCHEMA",
    "CoreWatermarkLsnIndex",
    "CorpusCountKind",
    "CorpusCounts",
    "CorpusRegistryEntry",
    "CorpusRegistryEvent",
    "CorpusRegistryProjector",
    "CorpusRegistrySnapshot",
    "CorpusRegistryStore",
    "InMemoryCorpusRegistryStore",
    "zero_corpus_counts",
]
