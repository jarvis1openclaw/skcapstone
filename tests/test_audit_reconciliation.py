from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sklegal_audit import (
    AuditAttributes,
    AuditBoundary,
    AuditEventDraft,
    AuditOutcome,
    DerivedRecordPin,
    DerivedRecordState,
    DerivedStateStale,
    DerivedStoreKind,
    DurableAuditEvent,
    DurableAuditSink,
    InMemoryAuditLedger,
    InMemoryDerivedStore,
    OutboxMessage,
    PolicyRevisionReconciler,
    PolicyRevisionView,
    ProjectionWatermark,
    ReconciliationUnavailable,
    RunCorrelation,
)
from sklegal_policies import (
    DataFlowBoundary,
    PolicyDecision,
    PolicyReason,
)

AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("a7000000-0000-4000-8000-000000000001")
TENANT_B_ID = UUID("a7000000-0000-4000-8000-000000000002")
MATTER_ID = UUID("a7000000-0000-4000-8000-000000000003")
MATTER_B_ID = UUID("a7000000-0000-4000-8000-000000000004")
PRINCIPAL_ID = UUID("a7000000-0000-4000-8000-000000000005")
MATERIAL_ID = UUID("a7000000-0000-4000-8000-000000000006")
RUN_ID = UUID("a7000000-0000-4000-8000-000000000007")
CORRELATION_ID = UUID("a7000000-0000-4000-8000-000000000008")
CAPAUTH_DECISION_ID = UUID("a7000000-0000-4000-8000-000000000009")
TRACE_ID = "1" * 32
SPAN_ID = "2" * 16
REV_A = "a" * 64
REV_B = "b" * 64
REV_C = "c" * 64
MATERIAL_SHA = "d" * 64
PK_1 = "1" * 64
PK_2 = "2" * 64
PK_3 = "3" * 64
PK_4 = "4" * 64


class MutableClock:
    def __init__(self, current: datetime = AT) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class ReconciliationHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.ledger = InMemoryAuditLedger(clock=self.clock)
        self.correlation = RunCorrelation(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
            trace_flags="01",
        )

    def append_policy_event(
        self,
        *,
        event_number: int,
        revision: str,
        tenant_id: UUID = TENANT_ID,
        matter_id: UUID | None = MATTER_ID,
    ) -> DurableAuditEvent:
        draft = AuditEventDraft(
            event_id=UUID(f"a7000000-0000-4000-8000-{100 + event_number:012d}"),
            tenant_id=tenant_id,
            matter_id=matter_id,
            principal_id=PRINCIPAL_ID,
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            action="policy.access",
            resource_kind="material",
            resource_id=MATERIAL_ID,
            outcome=AuditOutcome.ALLOW,
            reason_code="allow",
            occurred_at=self.clock(),
            attributes=AuditAttributes(
                event_schema="sklegal-policy-decision/v1",
                policy_revision=revision,
            ),
        )
        event = self.ledger.append(draft)
        self.clock.advance(seconds=1)
        return event

    def pin(
        self,
        *,
        store: DerivedStoreKind = DerivedStoreKind.REQUEST_CACHE,
        partition_key: str = PK_1,
        tenant_id: UUID = TENANT_ID,
        matter_id: UUID | None = MATTER_ID,
        revision: str = REV_A,
        sequence: int = 1,
        event_sha256: str,
    ) -> DerivedRecordPin:
        return DerivedRecordPin(
            tenant_id=tenant_id,
            matter_id=matter_id,
            store=store,
            partition_key=partition_key,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=MATERIAL_SHA,
            policy_revision=revision,
            core_event_sequence=sequence,
            core_event_sha256=event_sha256,
        )

    def view(
        self,
        *,
        revision: str = REV_A,
        sequence: int = 1,
        event_sha256: str,
        tenant_id: UUID = TENANT_ID,
        matter_id: UUID | None = MATTER_ID,
    ) -> PolicyRevisionView:
        return PolicyRevisionView(
            tenant_id=tenant_id,
            matter_id=matter_id,
            policy_revision=revision,
            core_event_sequence=sequence,
            core_event_sha256=event_sha256,
        )


class DerivedRecordPinTests(ReconciliationHarness):
    def test_retrieval_pin_requires_projection_pins(self) -> None:
        with self.assertRaises(ValidationError):
            DerivedRecordPin(
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                store=DerivedStoreKind.RETRIEVAL_VECTOR,
                partition_key=PK_1,
                material_id=MATERIAL_ID,
                material_version=1,
                material_sha256=MATERIAL_SHA,
                policy_revision=REV_A,
                core_event_sequence=1,
                core_event_sha256=REV_B,
            )

    def test_retrieval_pin_requires_complete_projection_pins(self) -> None:
        with self.assertRaises(ValidationError):
            DerivedRecordPin(
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                store=DerivedStoreKind.RETRIEVAL_LEXICAL,
                partition_key=PK_1,
                material_id=MATERIAL_ID,
                material_version=1,
                material_sha256=MATERIAL_SHA,
                policy_revision=REV_A,
                projection_set="projection-set-a",
                core_event_sequence=1,
                core_event_sha256=REV_B,
            )

    def test_retrieval_pin_with_projection_pins_is_valid(self) -> None:
        pin = DerivedRecordPin(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            store=DerivedStoreKind.RETRIEVAL_GRAPH,
            partition_key=PK_1,
            material_id=MATERIAL_ID,
            material_version=1,
            material_sha256=MATERIAL_SHA,
            policy_revision=REV_A,
            rights_revision=REV_C,
            projection_set="projection-set-a",
            projection_generation=1,
            core_event_sequence=1,
            core_event_sha256=REV_B,
        )
        self.assertEqual(pin.projection_generation, 1)

    def test_cache_pin_rejects_projection_pins(self) -> None:
        with self.assertRaises(ValidationError):
            DerivedRecordPin(
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                store=DerivedStoreKind.REQUEST_CACHE,
                partition_key=PK_1,
                material_id=MATERIAL_ID,
                material_version=1,
                material_sha256=MATERIAL_SHA,
                policy_revision=REV_A,
                projection_set="projection-set-a",
                projection_generation=1,
                core_event_sequence=1,
                core_event_sha256=REV_B,
            )

    def test_nil_identifiers_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DerivedRecordPin(
                tenant_id=UUID(int=0),
                store=DerivedStoreKind.EXPORT,
                partition_key=PK_1,
                material_id=MATERIAL_ID,
                material_version=1,
                material_sha256=MATERIAL_SHA,
                policy_revision=REV_A,
                core_event_sequence=1,
                core_event_sha256=REV_B,
            )


class InMemoryDerivedStoreTests(ReconciliationHarness):
    def setUp(self) -> None:
        super().setUp()
        self.store = InMemoryDerivedStore(store=DerivedStoreKind.REQUEST_CACHE)
        self.event = self.append_policy_event(event_number=1, revision=REV_A)
        self.current_view = self.view(
            revision=REV_A,
            sequence=1,
            event_sha256=self.event.event_sha256,
        )
        self.current_pin = self.pin(
            partition_key=PK_1,
            revision=REV_A,
            sequence=1,
            event_sha256=self.event.event_sha256,
        )

    def test_put_is_idempotent(self) -> None:
        self.store.put(self.current_pin)
        self.store.put(self.current_pin)
        self.assertEqual(len(self.store.entries(tenant_id=TENANT_ID)), 1)

    def test_require_current_returns_matching_pin(self) -> None:
        self.store.put(self.current_pin)
        result = self.store.require_current(self.current_pin, view=self.current_view)
        self.assertEqual(result, self.current_pin)

    def test_require_current_denies_unknown_entry(self) -> None:
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(self.current_pin, view=self.current_view)

    def test_require_current_denies_wrong_revision(self) -> None:
        self.store.put(self.current_pin)
        newer = self.view(
            revision=REV_B,
            sequence=2,
            event_sha256=REV_C,
        )
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(self.current_pin, view=newer)

    def test_require_current_denies_rollback_evidence(self) -> None:
        self.store.put(self.current_pin)
        behind = self.view(
            revision=REV_A,
            sequence=1,
            event_sha256=REV_C,
        )
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(self.current_pin, view=behind)

    def test_require_current_denies_entry_ahead_of_view(self) -> None:
        ahead = self.pin(
            partition_key=PK_2,
            revision=REV_A,
            sequence=9,
            event_sha256=REV_C,
        )
        self.store.put(ahead)
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(ahead, view=self.current_view)

    def test_require_current_denies_cross_tenant(self) -> None:
        self.store.put(self.current_pin)
        foreign = self.view(
            revision=REV_A,
            sequence=1,
            event_sha256=self.event.event_sha256,
            tenant_id=TENANT_B_ID,
        )
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(self.current_pin, view=foreign)

    def test_require_current_denies_cross_matter(self) -> None:
        self.store.put(self.current_pin)
        foreign = self.view(
            revision=REV_A,
            sequence=1,
            event_sha256=self.event.event_sha256,
            matter_id=MATTER_B_ID,
        )
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(self.current_pin, view=foreign)

    def test_require_current_denies_superseded_pin(self) -> None:
        self.store.put(self.current_pin)
        stale_copy = self.pin(
            partition_key=PK_1,
            revision=REV_B,
            sequence=1,
            event_sha256=self.event.event_sha256,
        )
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(stale_copy, view=self.current_view)

    def test_denied_pin_never_resurrects(self) -> None:
        self.store.put(self.current_pin)
        self.store.deny_stale(tenant_id=TENANT_ID, partition_keys=[PK_1])
        self.store.put(self.current_pin)
        with self.assertRaises(DerivedStateStale):
            self.store.require_current(self.current_pin, view=self.current_view)

    def test_new_derivation_replaces_denied_pin(self) -> None:
        self.store.put(self.current_pin)
        self.store.deny_stale(tenant_id=TENANT_ID, partition_keys=[PK_1])
        replacement = self.pin(
            partition_key=PK_1,
            revision=REV_B,
            sequence=2,
            event_sha256=REV_C,
        )
        self.store.put(replacement)
        newer = self.view(revision=REV_B, sequence=2, event_sha256=REV_C)
        self.assertEqual(
            self.store.require_current(replacement, view=newer),
            replacement,
        )

    def test_deny_is_idempotent_and_purge_requires_denial(self) -> None:
        self.store.put(self.current_pin)
        other = self.pin(
            partition_key=PK_2,
            revision=REV_A,
            sequence=1,
            event_sha256=self.event.event_sha256,
        )
        self.store.put(other)
        purged_early = self.store.purge_denied(
            tenant_id=TENANT_ID,
            partition_keys=[PK_1, PK_2],
        )
        self.assertEqual(purged_early, 0)
        self.assertEqual(len(self.store.entries(tenant_id=TENANT_ID)), 2)
        denied = self.store.deny_stale(tenant_id=TENANT_ID, partition_keys=[PK_1])
        self.assertEqual(denied, 1)
        self.assertEqual(
            self.store.deny_stale(tenant_id=TENANT_ID, partition_keys=[PK_1]),
            0,
        )
        self.assertEqual(
            self.store.purge_denied(tenant_id=TENANT_ID, partition_keys=[PK_1, PK_2]),
            1,
        )
        self.assertEqual(
            self.store.purge_denied(tenant_id=TENANT_ID, partition_keys=[PK_1]),
            0,
        )
        remaining = self.store.entries(tenant_id=TENANT_ID)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].pin.partition_key, PK_2)
        self.assertEqual(remaining[0].state, DerivedRecordState.CURRENT)

    def test_deny_is_tenant_scoped(self) -> None:
        foreign = self.pin(
            partition_key=PK_2,
            tenant_id=TENANT_B_ID,
            revision=REV_A,
            sequence=1,
            event_sha256=REV_B,
        )
        self.store.put(self.current_pin)
        self.store.put(foreign)
        denied = self.store.deny_stale(tenant_id=TENANT_ID, partition_keys=[PK_2])
        self.assertEqual(denied, 0)
        states = {
            entry.pin.partition_key: entry.state
            for entry in self.store.entries(tenant_id=TENANT_B_ID)
        }
        self.assertEqual(states[PK_2], DerivedRecordState.CURRENT)

    def test_pin_crossing_store_boundary_is_rejected(self) -> None:
        export_pin = self.pin(
            store=DerivedStoreKind.EXPORT,
            partition_key=PK_3,
            revision=REV_A,
            sequence=1,
            event_sha256=self.event.event_sha256,
        )
        with self.assertRaises(ValueError):
            self.store.put(export_pin)


class UnavailableOutbox:
    def pending_outbox(self, *, tenant_id: UUID) -> tuple[OutboxMessage, ...]:
        del tenant_id
        raise RuntimeError("synthetic outbox outage")

    def deliver(
        self,
        *,
        outbox_id: UUID,
        destination: str,
        handler: Callable[[OutboxMessage], None],
    ) -> object:
        del outbox_id, destination, handler
        raise RuntimeError("synthetic outbox outage")

    def event(self, *, tenant_id: UUID, event_id: UUID) -> DurableAuditEvent:
        del tenant_id, event_id
        raise RuntimeError("synthetic outbox outage")

    def advance_watermark(
        self,
        *,
        tenant_id: UUID,
        projection: str,
        expected_sequence: int,
        expected_event_sha256: str | None,
        new_sequence: int,
        new_event_sha256: str,
    ) -> ProjectionWatermark:
        del (
            tenant_id,
            projection,
            expected_sequence,
            expected_event_sha256,
            new_sequence,
            new_event_sha256,
        )
        raise RuntimeError("synthetic outbox outage")


class FailingDerivedStore(InMemoryDerivedStore):
    def entries(self, *, tenant_id: UUID) -> object:
        del tenant_id
        raise RuntimeError("synthetic store outage")


class ReconcilerTests(ReconciliationHarness):
    def setUp(self) -> None:
        super().setUp()
        self.cache = InMemoryDerivedStore(store=DerivedStoreKind.REQUEST_CACHE)
        self.bundles = InMemoryDerivedStore(store=DerivedStoreKind.MODEL_CONTEXT_BUNDLE)
        self.reconciler = PolicyRevisionReconciler(
            outbox=self.ledger,
            stores={
                "model-context-bundle": self.bundles,
                "request-cache": self.cache,
            },
            clock=self.clock,
        )

    def test_revision_propagation_denies_stale_and_reports_counts(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        shared_first = self.append_policy_event(
            event_number=2,
            revision=REV_A,
            matter_id=None,
        )
        matter_pin = self.pin(
            partition_key=PK_1,
            revision=REV_A,
            sequence=first.event_sequence,
            event_sha256=first.event_sha256,
        )
        bundle_pin = self.pin(
            store=DerivedStoreKind.MODEL_CONTEXT_BUNDLE,
            partition_key=PK_2,
            revision=REV_A,
            sequence=first.event_sequence,
            event_sha256=first.event_sha256,
        )
        shared_pin = self.pin(
            partition_key=PK_3,
            matter_id=None,
            revision=REV_A,
            sequence=shared_first.event_sequence,
            event_sha256=shared_first.event_sha256,
        )
        self.cache.put(matter_pin)
        self.bundles.put(bundle_pin)
        self.cache.put(shared_pin)
        self.reconciler.reconcile(tenant_id=TENANT_ID)
        view_a = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
        )
        assert view_a is not None
        self.assertEqual(
            self.cache.require_current(matter_pin, view=view_a),
            matter_pin,
        )

        self.append_policy_event(event_number=3, revision=REV_B)
        head = self.append_policy_event(
            event_number=4,
            revision=REV_B,
            matter_id=None,
        )
        report = self.reconciler.reconcile(tenant_id=TENANT_ID)
        counts = {item.store: item for item in report.stores}
        self.assertEqual(counts["request-cache"].scanned, 2)
        self.assertEqual(counts["request-cache"].denied, 2)
        self.assertEqual(counts["request-cache"].current, 0)
        self.assertEqual(counts["request-cache"].purged, 0)
        self.assertEqual(counts["model-context-bundle"].denied, 1)
        self.assertEqual(report.watermark_sequence, head.event_sequence)
        self.assertEqual(report.watermark_sha256, head.event_sha256)

        view_b = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
        )
        shared_view_b = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=None,
        )
        assert view_b is not None
        assert shared_view_b is not None
        self.assertEqual(view_b.policy_revision, REV_B)
        self.assertEqual(shared_view_b.policy_revision, REV_B)
        with self.assertRaises(DerivedStateStale):
            self.cache.require_current(matter_pin, view=view_b)
        with self.assertRaises(DerivedStateStale):
            self.bundles.require_current(bundle_pin, view=view_b)
        with self.assertRaises(DerivedStateStale):
            self.cache.require_current(shared_pin, view=shared_view_b)
        # A raced reader holding the superseded view is still denied by the
        # synchronous state marker written before any purge.
        with self.assertRaises(DerivedStateStale):
            self.cache.require_current(matter_pin, view=view_a)

        replacement = self.pin(
            partition_key=PK_4,
            revision=REV_B,
            sequence=head.event_sequence,
            event_sha256=head.event_sha256,
        )
        self.cache.put(replacement)
        self.assertEqual(
            self.cache.require_current(replacement, view=view_b),
            replacement,
        )

    def test_reconcile_is_idempotent(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        self.cache.put(
            self.pin(
                partition_key=PK_1,
                revision=REV_A,
                sequence=first.event_sequence,
                event_sha256=first.event_sha256,
            )
        )
        first_report = self.reconciler.reconcile(tenant_id=TENANT_ID)
        second_report = self.reconciler.reconcile(tenant_id=TENANT_ID)
        self.assertEqual(first_report.watermark_sequence, first.event_sequence)
        self.assertEqual(
            second_report.watermark_sequence,
            first_report.watermark_sequence,
        )
        self.assertEqual(
            second_report.watermark_sha256,
            first_report.watermark_sha256,
        )
        for item in second_report.stores:
            self.assertEqual(item.denied, 0)
            self.assertEqual(item.purged, 0)

    def test_stale_writer_after_reconcile_is_denied(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        self.append_policy_event(event_number=2, revision=REV_B)
        self.reconciler.reconcile(tenant_id=TENANT_ID)
        stale_write = self.pin(
            partition_key=PK_1,
            revision=REV_A,
            sequence=first.event_sequence,
            event_sha256=first.event_sha256,
        )
        self.cache.put(stale_write)
        view_b = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
        )
        assert view_b is not None
        with self.assertRaises(DerivedStateStale):
            self.cache.require_current(stale_write, view=view_b)
        report = self.reconciler.reconcile(tenant_id=TENANT_ID)
        counts = {item.store: item for item in report.stores}
        self.assertEqual(counts["request-cache"].denied, 1)

    def test_purge_removes_only_denied_records(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        head = self.append_policy_event(event_number=2, revision=REV_B)
        self.cache.put(
            self.pin(
                partition_key=PK_1,
                revision=REV_A,
                sequence=first.event_sequence,
                event_sha256=first.event_sha256,
            )
        )
        current = self.pin(
            partition_key=PK_2,
            revision=REV_B,
            sequence=head.event_sequence,
            event_sha256=head.event_sha256,
        )
        self.cache.put(current)
        early = self.reconciler.purge(tenant_id=TENANT_ID)
        self.assertEqual(sum(item.purged for item in early.stores), 0)
        self.reconciler.reconcile(tenant_id=TENANT_ID)
        report = self.reconciler.purge(tenant_id=TENANT_ID)
        counts = {item.store: item for item in report.stores}
        self.assertEqual(counts["request-cache"].purged, 1)
        self.assertEqual(counts["request-cache"].current, 1)
        self.assertEqual(report.watermark_sequence, head.event_sequence)
        again = self.reconciler.purge(tenant_id=TENANT_ID)
        self.assertEqual(sum(item.purged for item in again.stores), 0)
        view_b = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
        )
        assert view_b is not None
        self.assertEqual(self.cache.require_current(current, view=view_b), current)

    def test_outbox_outage_fails_closed(self) -> None:
        reconciler = PolicyRevisionReconciler(
            outbox=UnavailableOutbox(),
            stores={"request-cache": self.cache},
            clock=self.clock,
        )
        with self.assertRaises(ReconciliationUnavailable):
            reconciler.reconcile(tenant_id=TENANT_ID)
        self.assertIsNone(
            reconciler.current_view(tenant_id=TENANT_ID, matter_id=MATTER_ID)
        )

    def test_store_outage_fails_closed_without_watermark_advance(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        second = self.append_policy_event(event_number=2, revision=REV_B)
        stale_pin = self.pin(
            partition_key=PK_1,
            revision=REV_A,
            sequence=first.event_sequence,
            event_sha256=first.event_sha256,
        )
        self.cache.put(stale_pin)
        failing = FailingDerivedStore(store=DerivedStoreKind.MODEL_CONTEXT_BUNDLE)
        reconciler = PolicyRevisionReconciler(
            outbox=self.ledger,
            stores={
                "model-context-bundle": failing,
                "request-cache": self.cache,
            },
            clock=self.clock,
        )
        with self.assertRaises(ReconciliationUnavailable):
            reconciler.reconcile(tenant_id=TENANT_ID)
        # The committed read-gate view still denies stale records after the
        # partial failure.
        view_b = reconciler.current_view(tenant_id=TENANT_ID, matter_id=MATTER_ID)
        assert view_b is not None
        self.assertEqual(view_b.policy_revision, REV_B)
        with self.assertRaises(DerivedStateStale):
            self.cache.require_current(stale_pin, view=view_b)
        # The failed run never advanced the durable projection watermark, so
        # an operator-driven compare-and-set from the initial state succeeds.
        watermark = self.ledger.advance_watermark(
            tenant_id=TENANT_ID,
            projection="policy-reconciliation",
            expected_sequence=0,
            expected_event_sha256=None,
            new_sequence=second.event_sequence,
            new_event_sha256=second.event_sha256,
        )
        self.assertEqual(watermark.event_sequence, second.event_sequence)

    def test_watermark_conflict_fails_closed(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        self.append_policy_event(event_number=2, revision=REV_B)
        stale_pin = self.pin(
            partition_key=PK_1,
            revision=REV_A,
            sequence=first.event_sequence,
            event_sha256=first.event_sha256,
        )
        self.cache.put(stale_pin)
        self.ledger.advance_watermark(
            tenant_id=TENANT_ID,
            projection="policy-reconciliation",
            expected_sequence=0,
            expected_event_sha256=None,
            new_sequence=first.event_sequence,
            new_event_sha256=first.event_sha256,
        )
        with self.assertRaises(ReconciliationUnavailable):
            self.reconciler.reconcile(tenant_id=TENANT_ID)
        # Denials written before the conflict stand and the read gate fails
        # closed because the committed view names the newer revision.
        view_b = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
        )
        assert view_b is not None
        with self.assertRaises(DerivedStateStale):
            self.cache.require_current(stale_pin, view=view_b)

    def test_cross_tenant_isolation(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        foreign = self.pin(
            partition_key=PK_2,
            tenant_id=TENANT_B_ID,
            revision=REV_A,
            sequence=first.event_sequence,
            event_sha256=first.event_sha256,
        )
        self.cache.put(foreign)
        self.append_policy_event(event_number=2, revision=REV_B)
        self.reconciler.reconcile(tenant_id=TENANT_ID)
        foreign_entries = self.cache.entries(tenant_id=TENANT_B_ID)
        self.assertEqual(len(foreign_entries), 1)
        self.assertEqual(foreign_entries[0].state, DerivedRecordState.CURRENT)
        self.assertIsNone(
            self.reconciler.current_view(
                tenant_id=TENANT_B_ID,
                matter_id=MATTER_ID,
            )
        )

    def test_report_contains_no_denied_detail(self) -> None:
        first = self.append_policy_event(event_number=1, revision=REV_A)
        self.cache.put(
            self.pin(
                partition_key=PK_1,
                revision=REV_A,
                sequence=first.event_sequence,
                event_sha256=first.event_sha256,
            )
        )
        self.append_policy_event(event_number=2, revision=REV_B)
        report = self.reconciler.reconcile(tenant_id=TENANT_ID)
        encoded = json.dumps(report.model_dump(mode="json"))
        self.assertNotIn(PK_1, encoded)
        self.assertNotIn(str(MATERIAL_ID), encoded)
        self.assertNotIn(str(MATTER_ID), encoded)
        self.assertNotIn(REV_A, encoded)

    def test_sink_recorded_decisions_flow_through_outbox(self) -> None:
        sink = DurableAuditSink(
            repository=self.ledger,
            correlation=self.correlation,
            boundary=AuditBoundary.API,
            clock=self.clock,
        )
        first = self.append_policy_event(event_number=1, revision=REV_A)
        self.cache.put(
            self.pin(
                partition_key=PK_1,
                revision=REV_A,
                sequence=first.event_sequence,
                event_sha256=first.event_sha256,
            )
        )
        self.reconciler.reconcile(tenant_id=TENANT_ID)
        decision = PolicyDecision(
            decision_id=UUID("a7000000-0000-4000-8000-000000000201"),
            capauth_decision_id=CAPAUTH_DECISION_ID,
            correlation_id=CORRELATION_ID,
            principal_id=PRINCIPAL_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            material_id=MATERIAL_ID,
            material_version=1,
            boundary=DataFlowBoundary.RETRIEVAL,
            allow=False,
            reason=PolicyReason.CONFLICT_HOLD,
            policy_revision=REV_C,
            evaluated_at=self.clock(),
        )
        sink.record(decision)
        report = self.reconciler.reconcile(tenant_id=TENANT_ID)
        counts = {item.store: item for item in report.stores}
        self.assertEqual(counts["request-cache"].denied, 1)
        view_c = self.reconciler.current_view(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
        )
        assert view_c is not None
        self.assertEqual(view_c.policy_revision, REV_C)


if __name__ == "__main__":
    unittest.main()
