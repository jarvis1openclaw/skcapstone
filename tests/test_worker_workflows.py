from __future__ import annotations

import ast
import hashlib
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sklegal_worker import (
    INTERACTIVE_CONTEXT_TOKEN_LIMIT,
    AdmissionRejectedError,
    ApprovalRequirement,
    ApprovalSignal,
    DispatchRequest,
    EventKind,
    InMemoryStaleAlertSink,
    LongContextAdmission,
    ModelUnavailableError,
    PolicyDeniedError,
    QueueKind,
    RetryClass,
    RunConfig,
    RunIdentity,
    RunPhase,
    SimulatedDispatchLedger,
    SimulatedStepDriver,
    StaleRunAlert,
    StaticApprovalGate,
    StepActivityInput,
    StepRecord,
    TaskWorkflowInput,
    WorkflowInvariantError,
    WorkflowRunMachine,
    attempts_exhausted,
    is_retryable,
    retry_policy_for,
    select_queue,
    task_queue_name,
    worker_specs,
)
from sklegal_worker.activities import WorkerActivities
from sklegal_worker.workflows import (
    ACTIVITY_COMPENSATE_STEP,
    ACTIVITY_DISPATCH_CONNECTOR,
    ACTIVITY_RAISE_STALE_RUN_ALERT,
    ACTIVITY_RUN_TASK_STEP,
    ConnectorDispatchWorkflow,
    MatterBatchWorkflow,
    MatterTaskWorkflow,
)

AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("b6000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("b6000000-0000-4000-8000-000000000002")
CORRELATION_ID = UUID("b6000000-0000-4000-8000-000000000003")
APPROVAL_ID = UUID("b6000000-0000-4000-8000-000000000004")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
RECEIPT_DIGEST = "d" * 64


def at(seconds: int) -> datetime:
    return AT + timedelta(seconds=seconds)


def make_step(
    name: str,
    retry_class: RetryClass = RetryClass.INTERACTIVE,
    *,
    compensate: bool = True,
) -> StepRecord:
    return StepRecord(
        name=name,
        idempotency_key=f"key-{name}",
        retry_class=retry_class,
        compensate=compensate,
    )


def make_signal(
    decision: str = "approved",
    signal_id: str = "sig-1",
    decided_at: datetime | None = None,
) -> ApprovalSignal:
    return ApprovalSignal(
        signal_id=signal_id,
        approval_id=APPROVAL_ID,
        decision=decision,  # type: ignore[arg-type]
        decided_by="attorney-1",
        decided_at=decided_at or at(600),
    )


def make_dispatch(key: str = "dispatch-1") -> DispatchRequest:
    return DispatchRequest(
        idempotency_key=key,
        connector="email",
        artifact_digest=DIGEST_A,
        approval_id=APPROVAL_ID,
        destination_digest=DIGEST_B,
    )


def make_config(
    *,
    steps: tuple[StepRecord, ...] = (),
    requires_approval: bool = False,
    dispatch_key: str | None = None,
    stale_after: timedelta = timedelta(seconds=900),
    wait_timeout: timedelta | None = None,
    queue: QueueKind = QueueKind.INTERACTIVE,
) -> RunConfig:
    return RunConfig(
        queue=queue,
        run_key="run-1",
        steps=steps,
        requires_approval=requires_approval,
        dispatch_key=dispatch_key,
        stale_after=stale_after,
        wait_timeout=wait_timeout,
    )


def event_kinds(machine: WorkflowRunMachine) -> list[EventKind]:
    return [event.kind for event in machine.events]


class QueueSelectionTests(unittest.TestCase):
    def test_interactive_under_limit_stays_interactive(self) -> None:
        result = select_queue(
            QueueKind.INTERACTIVE, context_tokens=INTERACTIVE_CONTEXT_TOKEN_LIMIT
        )
        self.assertIs(result, QueueKind.INTERACTIVE)

    def test_interactive_over_limit_moves_to_long_context(self) -> None:
        result = select_queue(
            QueueKind.INTERACTIVE, context_tokens=INTERACTIVE_CONTEXT_TOKEN_LIMIT + 1
        )
        self.assertIs(result, QueueKind.LONG_CONTEXT)

    def test_other_queues_are_not_rerouted(self) -> None:
        for kind in (QueueKind.BATCH, QueueKind.LONG_CONTEXT, QueueKind.CONNECTOR):
            self.assertIs(select_queue(kind, context_tokens=10**9), kind)

    def test_negative_context_tokens_rejected(self) -> None:
        with self.assertRaises(ValueError):
            select_queue(QueueKind.INTERACTIVE, context_tokens=-1)

    def test_task_queue_names_are_distinct(self) -> None:
        names = {task_queue_name(kind) for kind in QueueKind}
        self.assertEqual(len(names), len(QueueKind))
        for name in names:
            self.assertTrue(name.startswith("sklegal-"))


class LongContextAdmissionTests(unittest.TestCase):
    def test_acquire_up_to_capacity(self) -> None:
        gate = LongContextAdmission(max_concurrent=2)
        first = gate.acquire("run-1", context_tokens=20_000)
        second = gate.acquire("run-2", context_tokens=20_000)
        self.assertEqual({first.slot, second.slot}, {0, 1})
        self.assertEqual(gate.active_count, 2)

    def test_reacquire_same_run_is_idempotent(self) -> None:
        gate = LongContextAdmission(max_concurrent=1)
        first = gate.acquire("run-1", context_tokens=20_000)
        again = gate.acquire("run-1", context_tokens=20_000)
        self.assertEqual(first, again)
        self.assertEqual(gate.active_count, 1)

    def test_saturated_gate_rejects_new_runs(self) -> None:
        gate = LongContextAdmission(max_concurrent=1)
        gate.acquire("run-1", context_tokens=20_000)
        with self.assertRaises(AdmissionRejectedError):
            gate.acquire("run-2", context_tokens=20_000)

    def test_release_frees_slot_and_stale_release_is_ignored(self) -> None:
        gate = LongContextAdmission(max_concurrent=1)
        first = gate.acquire("run-1", context_tokens=20_000)
        self.assertTrue(gate.release(first))
        self.assertFalse(gate.release(first))
        second = gate.acquire("run-2", context_tokens=20_000)
        self.assertEqual(second.slot, 0)
        self.assertFalse(gate.release(first))
        self.assertTrue(gate.release(second))


class RetrySpecTests(unittest.TestCase):
    def test_policy_denial_is_never_retried(self) -> None:
        for retry_class in RetryClass:
            self.assertFalse(is_retryable(retry_class, "PolicyDeniedError"))
            self.assertFalse(is_retryable(retry_class, "ApprovalRejectedError"))
            self.assertFalse(is_retryable(retry_class, "WorkflowInvariantError"))

    def test_model_outage_is_retryable_with_bounded_attempts(self) -> None:
        error_type = type(ModelUnavailableError("down")).__name__
        self.assertTrue(is_retryable(RetryClass.MODEL, error_type))
        self.assertFalse(attempts_exhausted(RetryClass.MODEL, 3))
        self.assertTrue(attempts_exhausted(RetryClass.MODEL, 4))

    def test_human_class_never_retries_automatically(self) -> None:
        self.assertTrue(attempts_exhausted(RetryClass.HUMAN, 1))

    def test_retry_policy_for_maps_temporal_fields(self) -> None:
        policy = retry_policy_for(RetryClass.CONNECTOR)
        self.assertEqual(policy.maximum_attempts, 6)
        self.assertIn("PolicyDeniedError", policy.non_retryable_error_types or ())


class HappyPathTests(unittest.TestCase):
    def test_full_run_with_approval_and_dispatch(self) -> None:
        steps = (make_step("prepare"), make_step("review-draft"))
        machine = WorkflowRunMachine(
            make_config(steps=steps, requires_approval=True, dispatch_key="dispatch-1")
        )
        machine.start(at=at(0))
        for index, step in enumerate(steps, start=1):
            machine.complete_step(step, at=at(index), digest=DIGEST_A)
        machine.request_approval(at=at(3))
        self.assertIs(machine.phase, RunPhase.AWAITING_APPROVAL)
        self.assertTrue(machine.decide_approval(make_signal(), at=at(600)))
        machine.record_dispatch("dispatch-1", at=at(601), receipt_digest=RECEIPT_DIGEST)
        machine.finish_completed(at=at(602))
        self.assertIs(machine.phase, RunPhase.COMPLETED)
        self.assertEqual(machine.terminal_detail, "completed")
        self.assertEqual(machine.pending_compensations, ())
        self.assertEqual(
            event_kinds(machine),
            [
                EventKind.RUN_STARTED,
                EventKind.STEP_COMPLETED,
                EventKind.STEP_COMPLETED,
                EventKind.APPROVAL_REQUESTED,
                EventKind.SIGNAL_APPLIED,
                EventKind.APPROVAL_DECIDED,
                EventKind.DISPATCH_RECORDED,
                EventKind.RUN_FINISHED,
            ],
        )


class DuplicateSignalTests(unittest.TestCase):
    def test_duplicate_signal_id_creates_no_new_state(self) -> None:
        machine = WorkflowRunMachine(
            make_config(steps=(make_step("prepare"),), requires_approval=True)
        )
        machine.start(at=at(0))
        machine.complete_step(make_step("prepare"), at=at(1), digest=DIGEST_A)
        machine.request_approval(at=at(2))
        self.assertTrue(machine.decide_approval(make_signal(), at=at(3)))
        baseline = machine.events
        self.assertFalse(machine.decide_approval(make_signal(), at=at(4)))
        self.assertFalse(
            machine.decide_approval(make_signal(signal_id="sig-2"), at=at(5))
        )
        self.assertFalse(machine.record_signal("sig-1", at=at(6)))
        self.assertEqual(machine.events, baseline)
        self.assertEqual(machine.approval_decision, "approved")
        self.assertEqual(event_kinds(machine).count(EventKind.APPROVAL_DECIDED), 1)

    def test_signal_before_wait_is_applied_once_waiting(self) -> None:
        machine = WorkflowRunMachine(
            make_config(steps=(make_step("prepare"),), requires_approval=True)
        )
        machine.start(at=at(0))
        self.assertTrue(machine.record_signal("sig-1", at=at(1)))
        machine.complete_step(make_step("prepare"), at=at(2), digest=DIGEST_A)
        machine.request_approval(at=at(3))
        self.assertTrue(machine.decide_approval(make_signal(), at=at(4)))
        self.assertEqual(machine.approval_decision, "approved")
        self.assertEqual(event_kinds(machine).count(EventKind.SIGNAL_APPLIED), 1)


class DuplicateDispatchTests(unittest.TestCase):
    def _ready_machine(self) -> WorkflowRunMachine:
        machine = WorkflowRunMachine(
            make_config(
                steps=(make_step("prepare"),),
                requires_approval=True,
                dispatch_key="dispatch-1",
            )
        )
        machine.start(at=at(0))
        machine.complete_step(make_step("prepare"), at=at(1), digest=DIGEST_A)
        machine.request_approval(at=at(2))
        machine.decide_approval(make_signal(), at=at(3))
        return machine

    def test_duplicate_dispatch_records_once(self) -> None:
        machine = self._ready_machine()
        machine.record_dispatch("dispatch-1", at=at(4), receipt_digest=RECEIPT_DIGEST)
        again = machine.record_dispatch(
            "dispatch-1", at=at(5), receipt_digest=RECEIPT_DIGEST
        )
        self.assertEqual(again, RECEIPT_DIGEST)
        self.assertEqual(event_kinds(machine).count(EventKind.DISPATCH_RECORDED), 1)

    def test_conflicting_receipt_for_same_key_is_rejected(self) -> None:
        machine = self._ready_machine()
        machine.record_dispatch("dispatch-1", at=at(4), receipt_digest=RECEIPT_DIGEST)
        with self.assertRaises(WorkflowInvariantError):
            machine.record_dispatch("dispatch-1", at=at(5), receipt_digest=DIGEST_C)

    def test_dispatch_before_approval_fails_closed(self) -> None:
        machine = WorkflowRunMachine(
            make_config(
                steps=(make_step("prepare"),),
                requires_approval=True,
                dispatch_key="dispatch-1",
            )
        )
        machine.start(at=at(0))
        machine.complete_step(make_step("prepare"), at=at(1), digest=DIGEST_A)
        with self.assertRaises(WorkflowInvariantError):
            machine.record_dispatch(
                "dispatch-1", at=at(2), receipt_digest=RECEIPT_DIGEST
            )

    def test_dispatch_key_must_match_plan(self) -> None:
        machine = self._ready_machine()
        with self.assertRaises(WorkflowInvariantError):
            machine.record_dispatch(
                "dispatch-other", at=at(4), receipt_digest=RECEIPT_DIGEST
            )


class WorkerKillReplayTests(unittest.TestCase):
    """Killing the worker at every event boundary must not duplicate state.

    A baseline run produces the authoritative event log. For every prefix of
    that log, a fresh machine replays the prefix (the worker-kill resume) and
    then re-drives every scripted command. Idempotent commands and
    history-aware failure recording must reproduce the identical log with
    exactly one dispatch and one applied signal.
    """

    def _scenario(self) -> list[Callable[[WorkflowRunMachine], object]]:
        step_one = make_step("prepare")
        step_two = make_step("model-analysis", RetryClass.MODEL)
        signal = make_signal()

        def fail_step_two_once(machine: WorkflowRunMachine) -> object:
            already = any(
                event.kind is EventKind.STEP_FAILED
                and event.step == step_two.name
                and event.detail == "ModelUnavailableError"
                for event in machine.events
            )
            if already:
                return None
            return machine.fail_step(
                step_two, at=at(2), error_type="ModelUnavailableError"
            )

        return [
            lambda machine: machine.start(at=at(0)),
            lambda machine: machine.complete_step(step_one, at=at(1), digest=DIGEST_A),
            fail_step_two_once,
            lambda machine: machine.complete_step(step_two, at=at(3), digest=DIGEST_B),
            lambda machine: machine.request_approval(at=at(4)),
            lambda machine: machine.decide_approval(signal, at=at(600)),
            lambda machine: machine.record_dispatch(
                "dispatch-1", at=at(601), receipt_digest=RECEIPT_DIGEST
            ),
            lambda machine: machine.finish_completed(at=at(602)),
        ]

    def _config(self) -> RunConfig:
        return make_config(
            steps=(make_step("prepare"), make_step("model-analysis", RetryClass.MODEL)),
            requires_approval=True,
            dispatch_key="dispatch-1",
        )

    def test_resume_from_every_boundary_reproduces_identical_log(self) -> None:
        config = self._config()
        commands = self._scenario()
        baseline = WorkflowRunMachine(config)
        for command in commands:
            command(baseline)
        self.assertIs(baseline.phase, RunPhase.COMPLETED)

        for cut in range(len(baseline.events) + 1):
            with self.subTest(boundary=cut):
                resumed = WorkflowRunMachine.replay(config, baseline.events[:cut])
                for command in commands:
                    command(resumed)
                self.assertEqual(resumed.events, baseline.events)
                self.assertIs(resumed.phase, RunPhase.COMPLETED)
                self.assertEqual(
                    event_kinds(resumed).count(EventKind.DISPATCH_RECORDED), 1
                )
                self.assertEqual(
                    event_kinds(resumed).count(EventKind.SIGNAL_APPLIED), 1
                )
                self.assertEqual(resumed.attempts, baseline.attempts)

    def test_replay_is_pure_and_repeatable(self) -> None:
        config = self._config()
        baseline = WorkflowRunMachine(config)
        for command in self._scenario():
            command(baseline)
        first = WorkflowRunMachine.replay(config, baseline.events)
        second = WorkflowRunMachine.replay(config, baseline.events)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.phase, second.phase)
        self.assertEqual(first.completed_steps, second.completed_steps)


class ActivityTimeoutTests(unittest.TestCase):
    def test_timeout_retries_then_compensates(self) -> None:
        step = make_step("fetch-bundle", RetryClass.INTERACTIVE)
        machine = WorkflowRunMachine(make_config(steps=(step,)))
        machine.start(at=at(0))
        self.assertTrue(machine.fail_step(step, at=at(1), error_type="ActivityTimeout"))
        self.assertIs(machine.phase, RunPhase.RUNNING)
        self.assertTrue(machine.fail_step(step, at=at(2), error_type="ActivityTimeout"))
        self.assertFalse(
            machine.fail_step(step, at=at(3), error_type="ActivityTimeout")
        )
        self.assertIs(machine.phase, RunPhase.FAILED)
        self.assertEqual(machine.attempts[step.name], 3)
        self.assertEqual(machine.terminal_detail, "failed:ActivityTimeout")
        self.assertNotIn(EventKind.DISPATCH_RECORDED, event_kinds(machine))


class PolicyDenialTests(unittest.TestCase):
    def test_policy_denial_fails_closed_without_retry(self) -> None:
        step = make_step("redact-export")
        machine = WorkflowRunMachine(make_config(steps=(step,)))
        machine.start(at=at(0))
        self.assertFalse(
            machine.fail_step(step, at=at(1), error_type="PolicyDeniedError")
        )
        self.assertIs(machine.phase, RunPhase.FAILED)
        self.assertEqual(machine.attempts[step.name], 1)
        self.assertEqual(machine.terminal_detail, "failed:PolicyDeniedError")
        self.assertNotIn(EventKind.DISPATCH_RECORDED, event_kinds(machine))


class ModelOutageTests(unittest.TestCase):
    def test_model_outage_retries_to_budget_then_compensates(self) -> None:
        first = make_step("prepare")
        model_step = make_step("model-analysis", RetryClass.MODEL)
        machine = WorkflowRunMachine(make_config(steps=(first, model_step)))
        machine.start(at=at(0))
        machine.complete_step(first, at=at(1), digest=DIGEST_A)
        for index in (2, 3, 4):
            self.assertTrue(
                machine.fail_step(
                    model_step, at=at(index), error_type="ModelUnavailableError"
                )
            )
            self.assertIs(machine.phase, RunPhase.RUNNING)
        self.assertFalse(
            machine.fail_step(model_step, at=at(5), error_type="ModelUnavailableError")
        )
        self.assertIs(machine.phase, RunPhase.COMPENSATING)
        self.assertEqual(machine.pending_compensations, (first,))
        machine.complete_compensation(first.name, at=at(6))
        self.assertIs(machine.phase, RunPhase.FAILED)
        self.assertEqual(machine.terminal_detail, "failed:ModelUnavailableError")
        self.assertNotIn(EventKind.DISPATCH_RECORDED, event_kinds(machine))


class LongHumanWaitTests(unittest.TestCase):
    def _waiting_machine(
        self, wait_timeout: timedelta | None = None
    ) -> WorkflowRunMachine:
        machine = WorkflowRunMachine(
            make_config(
                steps=(make_step("prepare"),),
                requires_approval=True,
                stale_after=timedelta(seconds=900),
                wait_timeout=wait_timeout,
            )
        )
        machine.start(at=at(0))
        machine.complete_step(make_step("prepare"), at=at(1), digest=DIGEST_A)
        machine.request_approval(at=at(2))
        return machine

    def test_stale_alert_fires_once_and_late_approval_still_applies(self) -> None:
        machine = self._waiting_machine()
        self.assertEqual(machine.tick(at(2 + 899)), ())
        alerts = machine.tick(at(2 + 900))
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.run_key, "run-1")
        self.assertIs(alert.phase, RunPhase.AWAITING_APPROVAL)
        self.assertIs(alert.queue, QueueKind.INTERACTIVE)
        self.assertEqual(alert.stale_after_seconds, 900)
        self.assertEqual(machine.tick(at(2 + 1800)), ())
        self.assertEqual(event_kinds(machine).count(EventKind.STALE_ALERT_RAISED), 1)
        self.assertIs(machine.phase, RunPhase.AWAITING_APPROVAL)
        self.assertTrue(
            machine.decide_approval(
                make_signal(decided_at=at(2 + 3600)), at=at(2 + 3600)
            )
        )
        machine.finish_completed(at=at(2 + 3601))
        self.assertIs(machine.phase, RunPhase.COMPLETED)

    def test_wait_timeout_compensates_and_rejects_late_signals(self) -> None:
        machine = self._waiting_machine(wait_timeout=timedelta(seconds=3600))
        machine.tick(at(2 + 3600))
        self.assertIs(machine.phase, RunPhase.COMPENSATING)
        self.assertEqual(machine.pending_compensations, (make_step("prepare"),))
        machine.complete_compensation("prepare", at=at(2 + 3601))
        self.assertIs(machine.phase, RunPhase.FAILED)
        self.assertEqual(machine.terminal_detail, "failed:approval_wait_timeout")
        self.assertFalse(
            machine.decide_approval(
                make_signal(decided_at=at(2 + 3700)), at=at(2 + 3700)
            )
        )
        self.assertFalse(machine.record_signal("sig-late", at=at(2 + 3701)))


class CancellationTests(unittest.TestCase):
    def test_cancellation_compensates_in_reverse_order(self) -> None:
        steps = (make_step("prepare"), make_step("redact"), make_step("export"))
        machine = WorkflowRunMachine(make_config(steps=steps))
        machine.start(at=at(0))
        machine.complete_step(steps[0], at=at(1), digest=DIGEST_A)
        machine.complete_step(steps[1], at=at(2), digest=DIGEST_B)
        self.assertTrue(machine.begin_compensation(at=at(3), reason="cancelled"))
        self.assertFalse(machine.begin_compensation(at=at(4), reason="cancelled"))
        self.assertIs(machine.phase, RunPhase.COMPENSATING)
        self.assertEqual(machine.pending_compensations, (steps[1], steps[0]))
        with self.assertRaises(WorkflowInvariantError):
            machine.record_dispatch(
                "dispatch-1", at=at(4), receipt_digest=RECEIPT_DIGEST
            )
        machine.complete_compensation("redact", at=at(5))
        machine.complete_compensation("prepare", at=at(6))
        self.assertIs(machine.phase, RunPhase.CANCELLED)
        self.assertEqual(machine.terminal_detail, "cancelled")

    def test_cancel_before_progress_finishes_immediately(self) -> None:
        machine = WorkflowRunMachine(make_config(steps=(make_step("prepare"),)))
        machine.start(at=at(0))
        machine.begin_compensation(at=at(1), reason="cancelled")
        self.assertIs(machine.phase, RunPhase.CANCELLED)

    def test_compensating_an_incomplete_step_is_rejected(self) -> None:
        steps = (make_step("prepare"), make_step("export"))
        machine = WorkflowRunMachine(make_config(steps=steps))
        machine.start(at=at(0))
        machine.complete_step(steps[0], at=at(1), digest=DIGEST_A)
        machine.begin_compensation(at=at(2), reason="cancelled")
        with self.assertRaises(WorkflowInvariantError):
            machine.complete_compensation("export", at=at(3))


class ActivitySimulationTests(unittest.IsolatedAsyncioTestCase):
    def _activities(
        self,
        *,
        gate: StaticApprovalGate | None = None,
        ledger: SimulatedDispatchLedger | None = None,
        sink: InMemoryStaleAlertSink | None = None,
    ) -> WorkerActivities:
        return WorkerActivities(
            approval_gate=gate or StaticApprovalGate({APPROVAL_ID: "approved"}),
            dispatch_ledger=ledger or SimulatedDispatchLedger(),
            stale_sink=sink or InMemoryStaleAlertSink(),
            clock=lambda: AT,
        )

    async def test_run_task_step_is_deterministic(self) -> None:
        activities = self._activities()
        step = make_step("prepare")
        request = StepActivityInput(run_key="run-1", step=step)
        outcome = await activities.run_task_step(request)
        expected = hashlib.sha256(b"step:run-1:prepare:key-prepare").hexdigest()
        self.assertEqual(outcome.result_digest, expected)
        self.assertEqual(
            outcome.result_digest, SimulatedStepDriver().run(request, at=AT)
        )

    async def test_dispatch_requires_approved_reference(self) -> None:
        activities = self._activities(gate=StaticApprovalGate({}))
        with self.assertRaises(PolicyDeniedError):
            await activities.dispatch_connector(make_dispatch())

    async def test_dispatch_records_once_per_idempotency_key(self) -> None:
        ledger = SimulatedDispatchLedger()
        activities = self._activities(ledger=ledger)
        first = await activities.dispatch_connector(make_dispatch())
        second = await activities.dispatch_connector(make_dispatch())
        self.assertEqual(first, second)
        self.assertEqual(ledger.recorded_count, 1)

    async def test_dispatch_key_conflict_is_rejected(self) -> None:
        ledger = SimulatedDispatchLedger()
        activities = self._activities(ledger=ledger)
        await activities.dispatch_connector(make_dispatch())
        conflicting = DispatchRequest(
            idempotency_key="dispatch-1",
            connector="filing",
            artifact_digest=DIGEST_C,
            approval_id=APPROVAL_ID,
            destination_digest=DIGEST_B,
        )
        with self.assertRaises(WorkflowInvariantError):
            await activities.dispatch_connector(conflicting)
        self.assertEqual(ledger.recorded_count, 1)

    async def test_compensation_digest_differs_from_run_digest(self) -> None:
        activities = self._activities()
        request = StepActivityInput(run_key="run-1", step=make_step("prepare"))
        run_outcome = await activities.run_task_step(request)
        compensation = await activities.compensate_step(request)
        self.assertNotEqual(run_outcome.result_digest, compensation.result_digest)

    async def test_stale_alert_is_recorded_once(self) -> None:
        sink = InMemoryStaleAlertSink()
        activities = self._activities(sink=sink)
        alert = StaleRunAlert(
            run_key="run-1",
            queue=QueueKind.INTERACTIVE,
            phase=RunPhase.AWAITING_APPROVAL,
            stale_after_seconds=900,
            last_progress_at=AT,
            raised_at=at(900),
        )
        self.assertTrue(await activities.raise_stale_run_alert(alert))
        self.assertFalse(await activities.raise_stale_run_alert(alert))
        self.assertEqual(len(sink.alerts), 1)


class WorkflowBoundaryGuardTests(unittest.TestCase):
    """Workflow code must stay deterministic: no model or I/O calls."""

    BANNED_MODULES = {
        "os",
        "sys",
        "socket",
        "subprocess",
        "pathlib",
        "requests",
        "httpx",
        "urllib",
        "random",
        "time",
        "uuid",
        "sklegal_model_gateway",
        "sklegal_connectors",
        "sklegal_retrieval",
    }
    BANNED_CALLS = {
        "open",
        "eval",
        "exec",
        "compile",
        "input",
        "print",
        "globals",
        "locals",
        "__import__",
    }
    BANNED_ATTR_CALLS = {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("time", "time"),
        ("time", "sleep"),
        ("time", "monotonic"),
        ("uuid", "uuid4"),
        ("random", "random"),
    }

    def _workflow_tree(self) -> ast.Module:
        path = (
            Path(__file__).resolve().parents[1]
            / "services"
            / "worker"
            / "src"
            / "sklegal_worker"
            / "workflows.py"
        )
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_no_io_or_model_calls_in_workflow_code(self) -> None:
        tree = self._workflow_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    self.assertNotIn(root, self.BANNED_MODULES)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                self.assertNotIn(root, self.BANNED_MODULES)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, self.BANNED_CALLS)
                elif isinstance(node.func, ast.Attribute) and isinstance(
                    node.func.value, ast.Name
                ):
                    pair = (node.func.value.id, node.func.attr)
                    self.assertNotIn(pair, self.BANNED_ATTR_CALLS)

    def test_workflow_definitions_are_registered(self) -> None:
        for cls in (MatterTaskWorkflow, MatterBatchWorkflow, ConnectorDispatchWorkflow):
            self.assertTrue(hasattr(cls, "__temporal_workflow_definition"))
            self.assertTrue(hasattr(cls.run, "__temporal_workflow_run"))
            self.assertTrue(hasattr(cls.phase, "__temporal_query_definition"))
        self.assertTrue(
            hasattr(MatterTaskWorkflow.submit_approval, "__temporal_signal_definition")
        )

    def test_activity_name_constants_match_registered_activities(self) -> None:
        pairs = {
            ACTIVITY_RUN_TASK_STEP: WorkerActivities.run_task_step,
            ACTIVITY_DISPATCH_CONNECTOR: WorkerActivities.dispatch_connector,
            ACTIVITY_COMPENSATE_STEP: WorkerActivities.compensate_step,
            ACTIVITY_RAISE_STALE_RUN_ALERT: WorkerActivities.raise_stale_run_alert,
        }
        for name, method in pairs.items():
            definition = getattr(method, "__temporal_activity_definition", None)
            self.assertIsNotNone(definition)
            self.assertEqual(definition.name, name)

    def test_payloads_round_trip_through_json(self) -> None:
        payload = TaskWorkflowInput(
            identity=RunIdentity(
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                run_key="run-1",
                correlation_id=CORRELATION_ID,
                requested_by="attorney-1",
            ),
            queue=QueueKind.INTERACTIVE,
            task_ref="task-1",
            context_tokens=1200,
            steps=(make_step("prepare"),),
            approval=ApprovalRequirement(approval_id=APPROVAL_ID),
            dispatch=make_dispatch(),
        )
        restored = TaskWorkflowInput.model_validate(payload.model_dump(mode="json"))
        self.assertEqual(restored, payload)

    def test_connector_input_requires_dispatch_and_approval(self) -> None:
        identity = RunIdentity(
            tenant_id=TENANT_ID,
            run_key="run-1",
            correlation_id=CORRELATION_ID,
            requested_by="attorney-1",
        )
        with self.assertRaises(ValidationError):
            TaskWorkflowInput(
                identity=identity,
                queue=QueueKind.CONNECTOR,
                task_ref="task-1",
                context_tokens=0,
            )
        with self.assertRaises(ValidationError):
            TaskWorkflowInput(
                identity=identity,
                queue=QueueKind.INTERACTIVE,
                task_ref="task-1",
                context_tokens=0,
                dispatch=make_dispatch(),
            )


class WorkerSpecTests(unittest.TestCase):
    def test_every_queue_has_one_worker_spec(self) -> None:
        specs = worker_specs()
        self.assertEqual({spec.queue for spec in specs}, set(QueueKind))
        self.assertEqual(len(specs), len(QueueKind))
        for spec in specs:
            self.assertTrue(spec.workflows)
            self.assertTrue(task_queue_name(spec.queue).startswith("sklegal-"))


if __name__ == "__main__":
    unittest.main()
