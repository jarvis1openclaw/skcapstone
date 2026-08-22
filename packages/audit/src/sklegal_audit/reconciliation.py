"""Policy-revision propagation and reconciliation across derived stores.

Derived stores (request caches, model-context bundles, exports, backups,
workflow snapshots, and materialized retrieval state) hold rebuildable copies
of protected decisions. Every derived record carries an exact
``DerivedRecordPin`` binding its source material hash and version, policy
revision, optional rights revision, optional projection set and generation,
and the exact core audit watermark it was derived from. A stale record is
denied synchronously at the read gate before any asynchronous purge. The
reconciler consumes policy evidence from the transactional outbox, marks
stale pins denied, and advances one compare-and-set projection watermark.
Outage, lag, rollback, partial failure, and revocation races fail closed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from .ledger import AuditUnavailable, WatermarkConflict
from .models import (
    AuditValue,
    DurableAuditEvent,
    OutboxDelivery,
    OutboxMessage,
    ProjectionWatermark,
    SafeCode,
    Sha256,
    require_utc,
)


class DerivedStateStale(PermissionError):
    """A derived record failed the synchronous current-policy read gate."""


class DerivedStoreUnavailable(RuntimeError):
    """A derived store cannot provide an authoritative current answer."""


class ReconciliationUnavailable(RuntimeError):
    """Policy-revision reconciliation could not complete fail-closed."""


class DerivedStoreKind(StrEnum):
    """Closed vocabulary of derived stores under policy reconciliation."""

    REQUEST_CACHE = "request-cache"
    MODEL_CONTEXT_BUNDLE = "model-context-bundle"
    EXPORT = "export"
    BACKUP = "backup"
    WORKFLOW_SNAPSHOT = "workflow-snapshot"
    RETRIEVAL_LEXICAL = "retrieval-lexical"
    RETRIEVAL_VECTOR = "retrieval-vector"
    RETRIEVAL_GRAPH = "retrieval-graph"


_RETRIEVAL_KINDS = frozenset(
    {
        DerivedStoreKind.RETRIEVAL_LEXICAL,
        DerivedStoreKind.RETRIEVAL_VECTOR,
        DerivedStoreKind.RETRIEVAL_GRAPH,
    }
)


class DerivedRecordState(StrEnum):
    CURRENT = "current"
    DENIED_STALE = "denied_stale"


class DerivedRecordPin(AuditValue):
    """Exact provenance and policy pins carried by every derived record.

    Contains only opaque identifiers, safe codes, digests, and watermark
    evidence. It never carries protected content or credentials.
    """

    tenant_id: UUID
    matter_id: UUID | None = None
    store: DerivedStoreKind
    partition_key: Sha256
    material_id: UUID
    material_version: int = Field(ge=1)
    material_sha256: Sha256
    policy_revision: Sha256
    rights_revision: Sha256 | None = None
    projection_set: SafeCode | None = None
    projection_generation: int | None = Field(default=None, ge=1)
    core_event_sequence: int = Field(ge=1)
    core_event_sha256: Sha256

    @model_validator(mode="after")
    def validate_projection_pins(self) -> DerivedRecordPin:
        retrieval = self.store in _RETRIEVAL_KINDS
        has_projection = (
            self.projection_set is not None or self.projection_generation is not None
        )
        if retrieval and not (
            self.projection_set is not None and self.projection_generation is not None
        ):
            raise ValueError("retrieval derived records require projection pins")
        if not retrieval and has_projection:
            raise ValueError("non-retrieval derived records cannot carry projections")
        return self


class StoredDerivedRecord(AuditValue):
    """One derived record pin plus its reconciliation state."""

    pin: DerivedRecordPin
    state: DerivedRecordState


class PolicyRevisionView(AuditValue):
    """Newest policy revision and core watermark observed for one scope."""

    tenant_id: UUID
    matter_id: UUID | None = None
    policy_revision: Sha256
    core_event_sequence: int = Field(ge=1)
    core_event_sha256: Sha256


class StoreReconciliationCounts(AuditValue):
    """Complete content-free counts for one derived store in one run."""

    store: SafeCode
    scanned: int = Field(ge=0)
    current: int = Field(ge=0)
    denied: int = Field(ge=0)
    purged: int = Field(ge=0)


class ReconciliationReport(AuditValue):
    """Complete counts for one run; never carries denied record detail."""

    tenant_id: UUID
    projection: SafeCode
    stores: tuple[StoreReconciliationCounts, ...]
    watermark_sequence: int = Field(ge=0)
    watermark_sha256: Sha256 | None = None
    reconciled_at: datetime

    @model_validator(mode="after")
    def validate_report(self) -> ReconciliationReport:
        require_utc(self.reconciled_at)
        if (self.watermark_sequence == 0) != (self.watermark_sha256 is None):
            raise ValueError("reconciliation watermark shape disagrees")
        return self


class DerivedStore(Protocol):
    """Fail-closed derived-store port implemented by each derived store."""

    def entries(self, *, tenant_id: UUID) -> tuple[StoredDerivedRecord, ...]: ...

    def deny_stale(self, *, tenant_id: UUID, partition_keys: Iterable[str]) -> int: ...

    def purge_denied(
        self, *, tenant_id: UUID, partition_keys: Iterable[str]
    ) -> int: ...

    def require_current(
        self, pin: DerivedRecordPin, *, view: PolicyRevisionView
    ) -> DerivedRecordPin: ...


class PolicyRevisionOutbox(Protocol):
    """Transactional-outbox port consumed by the policy reconciler."""

    def pending_outbox(self, *, tenant_id: UUID) -> tuple[OutboxMessage, ...]: ...

    def deliver(
        self,
        *,
        outbox_id: UUID,
        destination: str,
        handler: Callable[[OutboxMessage], None],
    ) -> OutboxDelivery: ...

    def event(self, *, tenant_id: UUID, event_id: UUID) -> DurableAuditEvent: ...

    def advance_watermark(
        self,
        *,
        tenant_id: UUID,
        projection: str,
        expected_sequence: int,
        expected_event_sha256: str | None,
        new_sequence: int,
        new_event_sha256: str,
    ) -> ProjectionWatermark: ...


class InMemoryDerivedStore:
    """Synthetic test and isolated-development store, never production."""

    def __init__(self, *, store: DerivedStoreKind) -> None:
        self._store = store
        self._lock = Lock()
        self._entries: dict[str, StoredDerivedRecord] = {}

    def put(self, pin: DerivedRecordPin) -> None:
        """Insert or replace one derivation; a denied pin never resurrects."""

        if not isinstance(pin, DerivedRecordPin):
            raise DerivedStoreUnavailable("derived store rejected an invalid pin")
        if pin.store != self._store:
            raise ValueError("derived pin crosses the store boundary")
        with self._lock:
            existing = self._entries.get(pin.partition_key)
            if existing is not None and existing.pin == pin:
                return
            self._entries[pin.partition_key] = StoredDerivedRecord(
                pin=pin,
                state=DerivedRecordState.CURRENT,
            )

    def entries(self, *, tenant_id: UUID) -> tuple[StoredDerivedRecord, ...]:
        with self._lock:
            return tuple(
                entry
                for key, entry in sorted(self._entries.items())
                if entry.pin.tenant_id == tenant_id
            )

    def deny_stale(self, *, tenant_id: UUID, partition_keys: Iterable[str]) -> int:
        """Synchronously deny stale records; idempotent and purge-free."""

        denied = 0
        with self._lock:
            for key in partition_keys:
                entry = self._entries.get(key)
                if (
                    entry is None
                    or entry.pin.tenant_id != tenant_id
                    or entry.state is not DerivedRecordState.CURRENT
                ):
                    continue
                self._entries[key] = StoredDerivedRecord(
                    pin=entry.pin,
                    state=DerivedRecordState.DENIED_STALE,
                )
                denied += 1
        return denied

    def purge_denied(self, *, tenant_id: UUID, partition_keys: Iterable[str]) -> int:
        """Remove only records already denied; never purges current records."""

        purged = 0
        with self._lock:
            for key in partition_keys:
                entry = self._entries.get(key)
                if (
                    entry is None
                    or entry.pin.tenant_id != tenant_id
                    or entry.state is not DerivedRecordState.DENIED_STALE
                ):
                    continue
                del self._entries[key]
                purged += 1
        return purged

    def require_current(
        self, pin: DerivedRecordPin, *, view: PolicyRevisionView
    ) -> DerivedRecordPin:
        """Recheck exact policy pins before any use of a derived record.

        Denies when the record is unknown, superseded, already denied,
        cross-tenant, cross-store, pinned to another policy revision, or
        pinned ahead of the authoritative core watermark (rollback evidence).
        """

        try:
            if not isinstance(pin, DerivedRecordPin) or not isinstance(
                view, PolicyRevisionView
            ):
                raise DerivedStateStale("derived record is not current")
            with self._lock:
                stored = self._entries.get(pin.partition_key)
            if (
                stored is None
                or stored.pin != pin
                or stored.state is not DerivedRecordState.CURRENT
                or pin.tenant_id != view.tenant_id
                or pin.matter_id != view.matter_id
                or pin.store != self._store
                or pin.policy_revision != view.policy_revision
                or pin.core_event_sequence > view.core_event_sequence
                or (
                    pin.core_event_sequence == view.core_event_sequence
                    and pin.core_event_sha256 != view.core_event_sha256
                )
            ):
                raise DerivedStateStale("derived record is not current")
            return stored.pin
        except DerivedStateStale:
            raise
        except Exception:
            raise DerivedStoreUnavailable("derived store unavailable") from None


class PolicyRevisionReconciler:
    """Propagate policy revisions from the core outbox to derived stores.

    The reconciler polls the tenant outbox in event order, records an
    idempotent delivery receipt per message, computes the newest policy
    revision per tenant and matter scope, and commits the read-gate view
    first so a later partial failure still denies stale records at the gate.
    It then synchronously denies every derived record pinned to a different
    revision or to rolled-back watermark evidence, and only then advances
    its compare-and-set projection watermark. Purge is a separate
    asynchronous step that can remove only records already denied. Any
    outbox, store, or watermark failure aborts the run without advancing
    the watermark, so lag, rollback, revocation races, and partial failure
    fail closed.
    """

    def __init__(
        self,
        *,
        outbox: PolicyRevisionOutbox,
        stores: Mapping[str, DerivedStore],
        projection: str = "policy-reconciliation",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        safe_code = TypeAdapter(SafeCode)
        self._outbox = outbox
        self._stores = dict(sorted(stores.items()))
        for name, store in self._stores.items():
            safe_code.validate_python(name)
            if not all(
                callable(getattr(store, attribute, None))
                for attribute in (
                    "entries",
                    "deny_stale",
                    "purge_denied",
                    "require_current",
                )
            ):
                raise ValueError("derived store does not implement the port")
        self._projection = safe_code.validate_python(projection)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._expected: dict[UUID, tuple[int, str | None]] = {}
        self._views: dict[tuple[UUID, UUID | None], PolicyRevisionView] = {}

    def current_view(
        self, *, tenant_id: UUID, matter_id: UUID | None
    ) -> PolicyRevisionView | None:
        """Return the newest reconciled view for one scope, if any.

        A missing view fails closed: callers must deny derived reads for any
        scope the reconciler has never reconciled.
        """

        with self._lock:
            return self._views.get((tenant_id, matter_id))

    def reconcile(self, *, tenant_id: UUID) -> ReconciliationReport:
        """Deliver pending policy evidence and deny stale derived records."""

        with self._lock:
            observed = self._consume(tenant_id)
            views = dict(self._views)
            if observed:
                head = observed[-1]
                latest_revision: dict[UUID | None, str] = {}
                for event in observed:
                    revision = event.attributes.policy_revision
                    if revision is not None:
                        latest_revision[event.matter_id] = revision
                scopes = {key[1] for key in views if key[0] == tenant_id} | {
                    event.matter_id for event in observed
                }
                for matter_id in scopes:
                    key = (tenant_id, matter_id)
                    existing = views.get(key)
                    revision = latest_revision.get(matter_id)
                    if revision is None:
                        if existing is None:
                            continue
                        revision = existing.policy_revision
                    views[key] = PolicyRevisionView(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        policy_revision=revision,
                        core_event_sequence=head.event_sequence,
                        core_event_sha256=head.event_sha256,
                    )
            self._views = views
            counts = self._deny_stale(tenant_id, views)
            if observed:
                self._advance(tenant_id, observed[-1])
            applied = self._expected.get(tenant_id, (0, None))
            return ReconciliationReport(
                tenant_id=tenant_id,
                projection=self._projection,
                stores=counts,
                watermark_sequence=applied[0],
                watermark_sha256=applied[1],
                reconciled_at=self._clock(),
            )

    def purge(self, *, tenant_id: UUID) -> ReconciliationReport:
        """Asynchronously remove records already denied by reconciliation."""

        with self._lock:
            counts: list[StoreReconciliationCounts] = []
            for name, store in self._stores.items():
                try:
                    entries = store.entries(tenant_id=tenant_id)
                    denied_keys = [
                        entry.pin.partition_key
                        for entry in entries
                        if entry.state is DerivedRecordState.DENIED_STALE
                    ]
                    purged = store.purge_denied(
                        tenant_id=tenant_id,
                        partition_keys=denied_keys,
                    )
                    remaining = store.entries(tenant_id=tenant_id)
                except Exception:
                    raise ReconciliationUnavailable(
                        "derived store unavailable"
                    ) from None
                counts.append(
                    StoreReconciliationCounts(
                        store=name,
                        scanned=len(entries),
                        current=sum(
                            1
                            for entry in remaining
                            if entry.state is DerivedRecordState.CURRENT
                        ),
                        denied=0,
                        purged=purged,
                    )
                )
            applied = self._expected.get(tenant_id, (0, None))
            return ReconciliationReport(
                tenant_id=tenant_id,
                projection=self._projection,
                stores=tuple(counts),
                watermark_sequence=applied[0],
                watermark_sha256=applied[1],
                reconciled_at=self._clock(),
            )

    def _consume(self, tenant_id: UUID) -> list[DurableAuditEvent]:
        try:
            messages = self._outbox.pending_outbox(tenant_id=tenant_id)
        except Exception:
            raise ReconciliationUnavailable("policy outbox unavailable") from None
        observed: list[DurableAuditEvent] = []
        for message in messages:
            try:
                event = self._outbox.event(
                    tenant_id=tenant_id,
                    event_id=message.event_id,
                )
            except Exception:
                raise ReconciliationUnavailable(
                    "policy outbox event unavailable"
                ) from None

            def _ack(
                _message: OutboxMessage, _event: DurableAuditEvent = event
            ) -> None:
                observed.append(_event)

            try:
                self._outbox.deliver(
                    outbox_id=message.outbox_id,
                    destination=message.destination,
                    handler=_ack,
                )
            except Exception:
                raise ReconciliationUnavailable(
                    "policy outbox delivery unavailable"
                ) from None
        return observed

    def _deny_stale(
        self,
        tenant_id: UUID,
        views: Mapping[tuple[UUID, UUID | None], PolicyRevisionView],
    ) -> tuple[StoreReconciliationCounts, ...]:
        counts: list[StoreReconciliationCounts] = []
        for name, store in self._stores.items():
            try:
                entries = store.entries(tenant_id=tenant_id)
                stale_keys = [
                    entry.pin.partition_key
                    for entry in entries
                    if entry.state is DerivedRecordState.CURRENT
                    and self._is_stale(entry.pin, views)
                ]
                denied = store.deny_stale(
                    tenant_id=tenant_id,
                    partition_keys=stale_keys,
                )
                remaining = store.entries(tenant_id=tenant_id)
            except Exception:
                raise ReconciliationUnavailable("derived store unavailable") from None
            counts.append(
                StoreReconciliationCounts(
                    store=name,
                    scanned=len(entries),
                    current=sum(
                        1
                        for entry in remaining
                        if entry.state is DerivedRecordState.CURRENT
                    ),
                    denied=denied,
                    purged=0,
                )
            )
        return tuple(counts)

    @staticmethod
    def _is_stale(
        pin: DerivedRecordPin,
        views: Mapping[tuple[UUID, UUID | None], PolicyRevisionView],
    ) -> bool:
        view = views.get((pin.tenant_id, pin.matter_id))
        if view is None:
            return False
        return (
            pin.policy_revision != view.policy_revision
            or pin.core_event_sequence > view.core_event_sequence
            or (
                pin.core_event_sequence == view.core_event_sequence
                and pin.core_event_sha256 != view.core_event_sha256
            )
        )

    def _advance(self, tenant_id: UUID, head: DurableAuditEvent) -> None:
        expected = self._expected.get(tenant_id, (0, None))
        try:
            watermark = self._outbox.advance_watermark(
                tenant_id=tenant_id,
                projection=self._projection,
                expected_sequence=expected[0],
                expected_event_sha256=expected[1],
                new_sequence=head.event_sequence,
                new_event_sha256=head.event_sha256,
            )
        except WatermarkConflict:
            raise ReconciliationUnavailable(
                "policy reconciliation watermark conflict"
            ) from None
        except AuditUnavailable:
            raise ReconciliationUnavailable(
                "policy reconciliation watermark unavailable"
            ) from None
        except Exception:
            raise ReconciliationUnavailable(
                "policy reconciliation watermark unavailable"
            ) from None
        self._expected[tenant_id] = (
            watermark.event_sequence,
            watermark.event_sha256,
        )
