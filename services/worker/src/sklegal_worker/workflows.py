"""Deterministic Temporal workflow definitions.

Workflow code here never performs model or I/O calls: every effect runs
through the typed activities in activities.py, and every state decision
folds through the pure WorkflowRunMachine in state.py. All timestamps come
from workflow.now() so history replay is deterministic. The guard test in
tests/test_worker_workflows.py enforces this boundary statically.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .models import (
        ApprovalSignal,
        DispatchReceipt,
        GovernedProposalInput,
        ProposalRunOutcome,
        QueueKind,
        RunPhase,
        StepActivityInput,
        StepOutcome,
        TaskWorkflowInput,
        TaskWorkflowResult,
    )
    from .retry import RetryClass, retry_policy_for
    from .state import RunConfig, WorkflowRunMachine

_STEP_TIMEOUTS: dict[QueueKind, timedelta] = {
    QueueKind.INTERACTIVE: timedelta(minutes=5),
    QueueKind.BATCH: timedelta(minutes=30),
    QueueKind.LONG_CONTEXT: timedelta(minutes=30),
    QueueKind.CONNECTOR: timedelta(minutes=2),
}

# Workers heartbeat from run_task_step at an interval well below these
# timeouts (the activity default is 10 seconds), so a killed worker is
# redelivered at the heartbeat timeout instead of the StartToClose timeout.
_STEP_HEARTBEAT_TIMEOUTS: dict[QueueKind, timedelta] = {
    QueueKind.INTERACTIVE: timedelta(seconds=60),
    QueueKind.BATCH: timedelta(minutes=5),
    QueueKind.LONG_CONTEXT: timedelta(minutes=5),
    QueueKind.CONNECTOR: timedelta(seconds=30),
}

# Activity names mirror the WorkerActivities method names registered on each
# worker; the guard test asserts these constants match the decorated methods.
ACTIVITY_RUN_TASK_STEP = "run_task_step"
ACTIVITY_DISPATCH_CONNECTOR = "dispatch_connector"
ACTIVITY_COMPENSATE_STEP = "compensate_step"
ACTIVITY_RAISE_STALE_RUN_ALERT = "raise_stale_run_alert"
ACTIVITY_RUN_GOVERNED_PROPOSAL = "run_governed_proposal"


def _config_for(input: TaskWorkflowInput) -> RunConfig:
    approval = input.approval
    wait_timeout = (
        timedelta(seconds=approval.wait_timeout_seconds)
        if approval is not None and approval.wait_timeout_seconds is not None
        else None
    )
    return RunConfig(
        queue=input.queue,
        run_key=input.identity.run_key,
        steps=input.steps,
        requires_approval=approval is not None,
        dispatch_key=input.dispatch.idempotency_key if input.dispatch else None,
        stale_after=(
            timedelta(seconds=approval.stale_after_seconds)
            if approval is not None
            else timedelta(seconds=1)
        ),
        wait_timeout=wait_timeout,
    )


class _PlanRunner:
    """Shared deterministic orchestration for all workflow definitions."""

    def __init__(self) -> None:
        self._machine: WorkflowRunMachine | None = None
        self._approval: ApprovalSignal | None = None

    def submit_approval(self, signal: ApprovalSignal) -> None:
        """Record a human signal; duplicate signal ids are dropped."""
        if self._approval is not None:
            return
        machine = self._machine
        if machine is None:
            self._approval = signal
            return
        if machine.record_signal(signal.signal_id, at=workflow.now()):
            self._approval = signal

    def phase(self) -> str:
        machine = self._machine
        return machine.phase.value if machine is not None else RunPhase.PENDING.value

    async def execute(self, input: TaskWorkflowInput) -> TaskWorkflowResult:
        config = _config_for(input)
        machine = WorkflowRunMachine(config)
        self._machine = machine
        machine.start(at=workflow.now())
        try:
            for step in config.steps:
                outcome: StepOutcome = await workflow.execute_activity(
                    ACTIVITY_RUN_TASK_STEP,
                    args=[StepActivityInput(run_key=config.run_key, step=step)],
                    start_to_close_timeout=_STEP_TIMEOUTS[input.queue],
                    heartbeat_timeout=_STEP_HEARTBEAT_TIMEOUTS[input.queue],
                    retry_policy=retry_policy_for(step.retry_class),
                    result_type=StepOutcome,
                )
                machine.complete_step(
                    step, at=workflow.now(), digest=outcome.result_digest
                )
            if config.requires_approval:
                await self._await_human(input, machine)
            receipt_digest: str | None = None
            if input.dispatch is not None:
                receipt: DispatchReceipt = await workflow.execute_activity(
                    ACTIVITY_DISPATCH_CONNECTOR,
                    args=[input.dispatch],
                    start_to_close_timeout=_STEP_TIMEOUTS[QueueKind.CONNECTOR],
                    retry_policy=retry_policy_for(RetryClass.CONNECTOR),
                    result_type=DispatchReceipt,
                )
                receipt_digest = machine.record_dispatch(
                    input.dispatch.idempotency_key,
                    at=workflow.now(),
                    receipt_digest=receipt.receipt_digest,
                )
            machine.finish_completed(at=workflow.now())
            return TaskWorkflowResult(
                run_key=config.run_key,
                phase=machine.phase,
                completed_steps=tuple(step.name for step in config.steps),
                dispatch_receipt_digest=receipt_digest,
                finished_at=workflow.now(),
            )
        except asyncio.CancelledError:
            await self._compensate(machine, reason="cancelled")
            raise
        except Exception as err:
            await self._compensate(machine, reason=f"failed:{type(err).__name__}")
            raise

    async def _await_human(
        self, input: TaskWorkflowInput, machine: WorkflowRunMachine
    ) -> None:
        approval = input.approval
        if approval is None:
            raise ApplicationError("approval requirement missing", non_retryable=True)
        machine.request_approval(at=workflow.now())
        interval = timedelta(seconds=approval.stale_after_seconds)
        while self._approval is None:
            try:
                await workflow.wait_condition(
                    lambda: self._approval is not None, timeout=interval
                )
            except TimeoutError:
                for alert in machine.tick(workflow.now()):
                    await workflow.execute_activity(
                        ACTIVITY_RAISE_STALE_RUN_ALERT,
                        args=[alert],
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=retry_policy_for(RetryClass.INTERACTIVE),
                        result_type=bool,
                    )
                if machine.phase is not RunPhase.AWAITING_APPROVAL:
                    raise ApplicationError(
                        "approval wait timed out", non_retryable=True
                    )
        signal = self._approval
        machine.decide_approval(signal, at=workflow.now())
        if signal.decision != "approved":
            raise ApplicationError("approval rejected", non_retryable=True)

    async def _compensate(self, machine: WorkflowRunMachine, *, reason: str) -> None:
        if machine.phase in (RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED):
            return
        machine.begin_compensation(at=workflow.now(), reason=reason)
        for step in machine.pending_compensations:
            outcome: StepOutcome = await asyncio.shield(
                workflow.execute_activity(
                    ACTIVITY_COMPENSATE_STEP,
                    args=[StepActivityInput(run_key=machine.config.run_key, step=step)],
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_policy_for(RetryClass.INTERACTIVE),
                    result_type=StepOutcome,
                )
            )
            del outcome
            machine.complete_compensation(step.name, at=workflow.now())


@workflow.defn
class MatterTaskWorkflow:
    """Interactive or long-context matter task with a human approval gate."""

    def __init__(self) -> None:
        self._plan = _PlanRunner()

    @workflow.run
    async def run(self, input: TaskWorkflowInput) -> TaskWorkflowResult:
        if input.queue not in (QueueKind.INTERACTIVE, QueueKind.LONG_CONTEXT):
            raise ApplicationError(
                "matter task runs only on interactive or long-context queues",
                non_retryable=True,
            )
        return await self._plan.execute(input)

    @workflow.signal
    def submit_approval(self, signal: ApprovalSignal) -> None:
        self._plan.submit_approval(signal)

    @workflow.query
    def phase(self) -> str:
        return self._plan.phase()


@workflow.defn
class MatterBatchWorkflow:
    """Batch imports, indexing, and reconciliation without human signals."""

    def __init__(self) -> None:
        self._plan = _PlanRunner()

    @workflow.run
    async def run(self, input: TaskWorkflowInput) -> TaskWorkflowResult:
        if input.queue is not QueueKind.BATCH:
            raise ApplicationError(
                "batch workflow requires batch queue input", non_retryable=True
            )
        return await self._plan.execute(input)

    @workflow.query
    def phase(self) -> str:
        return self._plan.phase()


@workflow.defn
class ConnectorDispatchWorkflow:
    """Connector queue dispatch against a pinned upstream approval."""

    def __init__(self) -> None:
        self._plan = _PlanRunner()

    @workflow.run
    async def run(self, input: TaskWorkflowInput) -> TaskWorkflowResult:
        if input.queue is not QueueKind.CONNECTOR or input.dispatch is None:
            raise ApplicationError(
                "connector workflow requires connector queue input with a dispatch",
                non_retryable=True,
            )
        return await self._plan.execute(input)

    @workflow.query
    def phase(self) -> str:
        return self._plan.phase()


@workflow.defn
class GovernedProposalWorkflow:
    """One governed Qwen proposal run over pinned matter context.

    The workflow body stays deterministic: it only checks the queue
    contract and delegates every retrieval, gateway, and ledger effect to
    the run_governed_proposal activity. The activity result carries
    identifiers and digests only, and the proposal itself stays a typed
    proposal in the ledger until a separate human decision.
    """

    @workflow.run
    async def run(self, input: GovernedProposalInput) -> ProposalRunOutcome:
        if input.queue not in (QueueKind.INTERACTIVE, QueueKind.LONG_CONTEXT):
            raise ApplicationError(
                "governed proposal runs use interactive or long-context queues",
                non_retryable=True,
            )
        outcome: ProposalRunOutcome = await workflow.execute_activity(
            ACTIVITY_RUN_GOVERNED_PROPOSAL,
            args=[input.proposal],
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry_policy_for(RetryClass.MODEL),
            result_type=ProposalRunOutcome,
        )
        return outcome
