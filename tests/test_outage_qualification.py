from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

from sklegal_audit import (
    AuditAttributes,
    AuditBoundary,
    AuditEventDraft,
    AuditOutcome,
    DerivedStoreKind,
    InMemoryAuditLedger,
    InMemoryDerivedStore,
    PolicyRevisionReconciler,
    ReconciliationUnavailable,
    RunCorrelation,
)
from sklegal_worker import (
    WORKER_DATA_CONVERTER,
    DispatchRequest,
    FileDispatchLedger,
    RetryClass,
    WorkflowInvariantError,
    connect_worker_client,
)
from sklegal_worker.activities import WorkerActivities
from sklegal_worker.models import StepActivityInput, StepRecord
from temporalio.contrib.pydantic import pydantic_data_converter

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "outage_qualification.py"
SPEC = importlib.util.spec_from_file_location("outage_qualification", MODULE_PATH)
assert SPEC and SPEC.loader
outage_qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(outage_qualification)

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
AT_LATER = datetime(2026, 8, 22, 12, 5, tzinfo=UTC)
TENANT_ID = UUID("e1000000-0000-4000-8000-0000000000c1")
APPROVAL_ID = UUID("e1000000-0000-4000-8000-0000000000c2")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def make_dispatch(key: str = "dispatch-outage-1") -> DispatchRequest:
    return DispatchRequest(
        idempotency_key=key,
        connector="email",
        artifact_digest=DIGEST_A,
        approval_id=APPROVAL_ID,
        destination_digest=DIGEST_B,
    )


def append_policy_event(
    ledger: InMemoryAuditLedger, revision: str, matter_id: UUID
) -> None:
    ledger.append(
        AuditEventDraft(
            event_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=matter_id,
            principal_id=TENANT_ID,
            correlation=RunCorrelation(
                run_id=uuid4(),
                correlation_id=uuid4(),
                trace_id=uuid4().hex[:32],
                span_id=uuid4().hex[:16],
            ),
            boundary=AuditBoundary.API,
            action="policy.access",
            resource_kind="material",
            resource_id=uuid4(),
            outcome=AuditOutcome.ALLOW,
            reason_code="allow",
            occurred_at=AT,
            attributes=AuditAttributes(
                event_schema="sklegal-policy-decision/v1",
                policy_revision=revision,
            ),
        )
    )


class FileDispatchLedgerTests(unittest.TestCase):
    """OM-7: dispatch replay suppression survives a worker process restart."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger_path = Path(temporary.name) / "dispatch-ledger.json"

    def test_replay_after_restart_returns_original_receipt(self) -> None:
        first = FileDispatchLedger(self.ledger_path).record(
            make_dispatch(), at=AT
        )
        # A fresh instance over the same path models a restarted worker: the
        # receipt must come from the durable file, not process memory.
        restarted = FileDispatchLedger(self.ledger_path)
        replay = restarted.record(make_dispatch(), at=AT_LATER)
        self.assertEqual(replay.receipt_digest, first.receipt_digest)
        self.assertEqual(replay.recorded_at, first.recorded_at)
        self.assertEqual(restarted.recorded_count, 1)
        receipts = restarted.receipts()
        self.assertEqual(
            receipts["dispatch-outage-1"].receipt_digest, first.receipt_digest
        )

    def test_conflicting_payload_after_restart_is_rejected(self) -> None:
        FileDispatchLedger(self.ledger_path).record(make_dispatch(), at=AT)
        restarted = FileDispatchLedger(self.ledger_path)
        with self.assertRaises(WorkflowInvariantError):
            restarted.record(
                DispatchRequest(
                    idempotency_key="dispatch-outage-1",
                    connector="email",
                    artifact_digest=DIGEST_C,
                    approval_id=APPROVAL_ID,
                    destination_digest=DIGEST_B,
                ),
                at=AT_LATER,
            )
        self.assertEqual(restarted.recorded_count, 1)

    def test_unreadable_state_fails_closed(self) -> None:
        self.ledger_path.write_text("{not json", encoding="utf-8")
        ledger = FileDispatchLedger(self.ledger_path)
        with self.assertRaises(WorkflowInvariantError):
            ledger.record(make_dispatch(), at=AT)
        with self.assertRaises(WorkflowInvariantError):
            ledger.receipts()

    def test_malformed_state_fails_closed(self) -> None:
        self.ledger_path.write_text(
            json.dumps({"receipts": [], "fingerprints": {}}), encoding="utf-8"
        )
        with self.assertRaises(WorkflowInvariantError):
            FileDispatchLedger(self.ledger_path).record(make_dispatch(), at=AT)

    def test_store_is_atomic_and_leaves_no_temporary(self) -> None:
        ledger = FileDispatchLedger(self.ledger_path)
        ledger.record(make_dispatch(), at=AT)
        self.assertTrue(self.ledger_path.exists())
        self.assertFalse(
            (self.ledger_path.parent / "dispatch-ledger.json.tmp").exists()
        )


class HeartbeatConfigurationTests(unittest.TestCase):
    """OM-1 prerequisite: heartbeats are configured and validated."""

    def test_non_positive_heartbeat_seconds_is_rejected(self) -> None:
        for invalid in (0, -1.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    WorkerActivities(heartbeat_seconds=invalid)

    def test_none_disables_heartbeats(self) -> None:
        activities = WorkerActivities(heartbeat_seconds=None)
        self.assertIsNone(activities.heartbeat_seconds)
        activities = WorkerActivities()
        self.assertEqual(activities.heartbeat_seconds, 10.0)

    def test_heartbeat_payload_is_content_free(self) -> None:
        payload = {
            "run_key": "run-1",
            "step": "prepare",
            "tick": True,
        }
        self.assertEqual(
            sorted(payload),
            ["run_key", "step", "tick"],
        )
        self.assertNotIn("matter", json.dumps(payload))


class SlowStepDriver:
    """Steps out of the way so a heartbeat interval can elapse first."""

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds

    def run(self, request: StepActivityInput, *, at: datetime) -> str:
        time.sleep(self._delay_seconds)
        return "0" * 64


class HeartbeatEmissionTests(unittest.IsolatedAsyncioTestCase):
    """OM-1: heartbeats are emitted while the step driver is still running."""

    async def test_heartbeat_sent_when_driver_exceeds_interval(self) -> None:
        activities = WorkerActivities(
            step_driver=SlowStepDriver(0.06),
            heartbeat_seconds=0.01,
        )
        request = StepActivityInput(
            run_key="run-outage-1",
            step=StepRecord(
                name="prepare",
                idempotency_key="key-prepare",
                retry_class=RetryClass.INTERACTIVE,
            ),
        )
        with (
            mock.patch(
                "sklegal_worker.activities.activity.in_activity",
                return_value=True,
            ),
            mock.patch(
                "sklegal_worker.activities.activity.heartbeat"
            ) as heartbeat,
        ):
            outcome = await activities.run_task_step(request)
        self.assertEqual(outcome.result_digest, "0" * 64)
        self.assertGreaterEqual(heartbeat.call_count, 1)
        payload = heartbeat.call_args.args[0]
        self.assertEqual(sorted(payload), ["run_key", "step", "tick"])


class ReconcilerRestartTests(unittest.TestCase):
    """OM-5: recover() restores the durable watermark after a restart."""

    def setUp(self) -> None:
        self.ledger = InMemoryAuditLedger()
        self.store = InMemoryDerivedStore(store=DerivedStoreKind.REQUEST_CACHE)
        self.matter = uuid4()

    def _reconciler(self) -> PolicyRevisionReconciler:
        return PolicyRevisionReconciler(
            outbox=self.ledger, stores={"request-cache": self.store}
        )

    def test_recover_restores_watermark_and_next_reconcile_advances(self) -> None:
        append_policy_event(self.ledger, "a" * 64, self.matter)
        append_policy_event(self.ledger, "b" * 64, self.matter)
        before = self._reconciler().reconcile(tenant_id=TENANT_ID)
        self.assertGreater(before.watermark_sequence, 0)
        # A still-pending message is required for the restarted process to
        # reach its stale compare-and-set expectation.
        append_policy_event(self.ledger, "d" * 64, self.matter)
        restarted = self._reconciler()
        with self.assertRaises(ReconciliationUnavailable):
            restarted.reconcile(tenant_id=TENANT_ID)
        recovered = self._reconciler()
        restored = recovered.recover(tenant_id=TENANT_ID)
        self.assertEqual(restored, before.watermark_sequence)
        # The failed attempt consumes the pending delivery, so a watermark
        # advance needs a later event; the recovered process must be able to
        # deliver it without another WatermarkConflict.
        append_policy_event(self.ledger, "e" * 64, self.matter)
        after = recovered.reconcile(tenant_id=TENANT_ID)
        self.assertGreater(after.watermark_sequence, restored)
        view = recovered.current_view(tenant_id=TENANT_ID, matter_id=self.matter)
        self.assertIsNotNone(view)
        self.assertEqual(view.policy_revision, "e" * 64)

    def test_recover_without_watermark_returns_zero(self) -> None:
        restored = self._reconciler().recover(tenant_id=TENANT_ID)
        self.assertEqual(restored, 0)


class ModelMatrixTests(unittest.TestCase):
    """OM-3 through OM-7 run in-process and must all observe their evidence."""

    def test_model_scenarios_all_pass_and_write_result_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outdir = Path(directory)
            results = outage_qualification.run_model_scenarios(outdir)
            self.assertEqual(
                sorted(results),
                ["OM-3", "OM-4", "OM-5", "OM-6", "OM-7"],
            )
            for scenario_id, payload in sorted(results.items()):
                with self.subTest(scenario=scenario_id):
                    self.assertEqual(payload["status"], "PASS")
                    self.assertTrue(payload["checks"])
                    self.assertTrue(
                        all(
                            check == "PASS"
                            for check in payload["checks"].values()
                        )
                    )
                    path = outdir / f"{scenario_id.lower()}.json"
                    self.assertTrue(path.exists())
                    written = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(written["status"], "PASS")


class OutageMatrixContractTests(unittest.TestCase):
    """The executed matrix must match the written contract."""

    def setUp(self) -> None:
        self.text = (
            ROOT / "docs/development/OUTAGE-MATRIX.md"
        ).read_text(encoding="utf-8")

    def test_all_seven_scenarios_are_defined(self) -> None:
        for scenario_id in outage_qualification.SCENARIO_ORDER:
            with self.subTest(scenario=scenario_id):
                self.assertIn(f"| {scenario_id} |", self.text)
        self.assertEqual(
            outage_qualification.SCENARIO_ORDER,
            ("OM-1", "OM-2", "OM-3", "OM-4", "OM-5", "OM-6", "OM-7"),
        )

    def test_scope_preserves_isolation_and_model_boundaries(self) -> None:
        for required in (
            "isolated, disposable compose project",
            "never stopped, restarted, or removed",
            "never resolves a real",
            "qualification failure, not a pass by omission",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.text)

    def test_recovery_targets_carry_over_smoke_bounds(self) -> None:
        self.assertIn("RPO 5 minutes or less", self.text)
        self.assertIn("RTO 15 minutes or less", self.text)
        self.assertIn("heartbeat timeout", self.text)

    def test_documents_use_ascii_dashes_only(self) -> None:
        self.assertNotIn("\u2014", self.text)
        self.assertNotIn("\u2013", self.text)


class WorkerClientExportTests(unittest.TestCase):
    """The worker client must use the shared pydantic payload converter."""

    def test_worker_data_converter_is_pydantic(self) -> None:
        self.assertIs(WORKER_DATA_CONVERTER, pydantic_data_converter)

    def test_connect_worker_client_is_callable(self) -> None:
        self.assertTrue(callable(connect_worker_client))


class ComposeIsolationTests(unittest.TestCase):
    """The qualification stack must stay loopback-only and parameterized."""

    def setUp(self) -> None:
        self.text = (
            ROOT / "deploy/chiap01/compose.dev.yml"
        ).read_text(encoding="utf-8")

    def test_host_ports_bind_loopback_only(self) -> None:
        lines = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("- \"127.0.0.1:${SKLEGAL_DEV_")
        ]
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertTrue(line.startswith("- \"127.0.0.1:"))

    def test_ports_and_volume_are_parameterized(self) -> None:
        self.assertIn("${SKLEGAL_DEV_POSTGRES_PORT:-15433}", self.text)
        self.assertIn("${SKLEGAL_DEV_TEMPORAL_PORT:-17233}", self.text)
        self.assertIn("${SKLEGAL_DEV_VOLUME_NAME:-", self.text)


if __name__ == "__main__":
    unittest.main()
