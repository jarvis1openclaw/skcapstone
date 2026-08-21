"""Deterministic, replayable workflow run state machine.

This module is pure Python: no I/O, no model calls, no wall clock. Every
timestamp is injected by the caller (Temporal workflow code passes
``workflow.now()``; tests pass fixed values). Commands validate against
current state and append events; replay folds the same events back into a
fresh machine through the identical code path, so a worker killed at any
boundary resumes into exactly one state. Repeated commands are idempotent
no-ops keyed on signal ids and idempotency keys, so duplicate signals and
duplicate dispatch requests never create duplicate state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .errors import WorkflowInvariantError
from .models import (
    TERMINAL_PHASES,
    ApprovalSignal,
    QueueKind,
    RunPhase,
    StaleRunAlert,
    StepRecord,
)
from .retry import attempts_exhausted, is_retryable


class EventKind(StrEnum):
    RUN_STARTED = "run_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    SIGNAL_APPLIED = "signal_applied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    DISPATCH_RECORDED = "dispatch_recorded"
    COMPENSATION_STARTED = "compensation_started"
    COMPENSATION_COMPLETED = "compensation_completed"
    STALE_ALERT_RAISED = "stale_alert_raised"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One append-only entry in the run history."""

    kind: EventKind
    at: datetime
    step: str | None = None
    key: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Immutable plan for one workflow run."""

    queue: QueueKind
    run_key: str
    steps: tuple[StepRecord, ...]
    requires_approval: bool
    dispatch_key: str | None
    stale_after: timedelta
    wait_timeout: timedelta | None


class WorkflowRunMachine:
    """Fold-based state machine over an append-only run event log."""

    def __init__(self, config: RunConfig) -> None:
        names = [step.name for step in config.steps]
        if len(names) != len(set(names)):
            raise WorkflowInvariantError("step names must be unique within a run")
        keys = [step.idempotency_key for step in config.steps]
        if len(keys) != len(set(keys)):
            raise WorkflowInvariantError(
                "step idempotency keys must be unique within a run"
            )
        self._config = config
        self._events: list[RunEvent] = []
        self._phase = RunPhase.PENDING
        self._completed: dict[str, str] = {}
        self._compensated: tuple[str, ...] = ()
        self._attempts: dict[str, int] = {}
        self._signals: set[str] = set()
        self._approval_requested_at: datetime | None = None
        self._approval_decision: str | None = None
        self._dispatch_key: str | None = None
        self._dispatch_receipt_digest: str | None = None
        self._stale_raised = False
        self._pending_reason: str | None = None
        self._terminal_detail: str | None = None
        self._last_progress_at: datetime | None = None

    @classmethod
    def replay(
        cls, config: RunConfig, events: tuple[RunEvent, ...] | list[RunEvent]
    ) -> WorkflowRunMachine:
        """Rebuild a machine by folding a recorded event log."""
        machine = cls(config)
        for event in events:
            machine._fold(event)
        return machine

    @property
    def config(self) -> RunConfig:
        return self._config

    @property
    def events(self) -> tuple[RunEvent, ...]:
        return tuple(self._events)

    @property
    def phase(self) -> RunPhase:
        return self._phase

    @property
    def completed_steps(self) -> tuple[str, ...]:
        return tuple(self._completed)

    @property
    def attempts(self) -> dict[str, int]:
        return dict(self._attempts)

    @property
    def signals_seen(self) -> frozenset[str]:
        return frozenset(self._signals)

    @property
    def approval_decision(self) -> str | None:
        return self._approval_decision

    @property
    def dispatch_receipt_digest(self) -> str | None:
        return self._dispatch_receipt_digest

    @property
    def terminal_detail(self) -> str | None:
        return self._terminal_detail

    @property
    def last_progress_at(self) -> datetime | None:
        return self._last_progress_at

    @property
    def pending_compensations(self) -> tuple[StepRecord, ...]:
        """Completed, compensable steps in reverse completion order.

        Nonempty only while the run is compensating; a completed run is
        never unwound.
        """
        if self._phase is not RunPhase.COMPENSATING:
            return ()
        by_name = {step.name: step for step in self._config.steps}
        return tuple(
            by_name[name]
            for name in reversed(tuple(self._completed))
            if name not in self._compensated and by_name[name].compensate
        )

    def start(self, *, at: datetime) -> RunEvent | None:
        if self._phase is not RunPhase.PENDING:
            return None
        return self._command(RunEvent(EventKind.RUN_STARTED, at))

    def complete_step(
        self, step: StepRecord, *, at: datetime, digest: str
    ) -> RunEvent | None:
        known = {item.name: item for item in self._config.steps}.get(step.name)
        if known is None or known.idempotency_key != step.idempotency_key:
            raise WorkflowInvariantError(f"unknown step for this run: {step.name}")
        recorded_key = self._completed.get(step.name)
        if recorded_key is not None:
            if recorded_key == step.idempotency_key:
                return None
            raise WorkflowInvariantError(
                f"step completed under a different idempotency key: {step.name}"
            )
        self._require_phase(RunPhase.RUNNING)
        return self._command(
            RunEvent(
                EventKind.STEP_COMPLETED,
                at,
                step=step.name,
                key=step.idempotency_key,
                detail=digest,
            )
        )

    def fail_step(self, step: StepRecord, *, at: datetime, error_type: str) -> bool:
        """Record a step failure; returns True when a retry may be scheduled."""
        self._require_phase(RunPhase.RUNNING)
        attempts = self._attempts.get(step.name, 0) + 1
        retryable = is_retryable(step.retry_class, error_type)
        exhausted = attempts_exhausted(step.retry_class, attempts)
        self._command(
            RunEvent(
                EventKind.STEP_FAILED,
                at,
                step=step.name,
                key=step.idempotency_key,
                detail=error_type,
            )
        )
        if retryable and not exhausted:
            return True
        self.begin_compensation(at=at, reason=f"failed:{error_type}")
        return False

    def request_approval(self, *, at: datetime) -> RunEvent | None:
        if self._approval_requested_at is not None:
            return None
        self._require_phase(RunPhase.RUNNING)
        if not self._config.requires_approval:
            raise WorkflowInvariantError("this run has no approval requirement")
        self._require_steps_complete()
        return self._command(RunEvent(EventKind.APPROVAL_REQUESTED, at))

    def record_signal(self, signal_id: str, *, at: datetime) -> bool:
        """Deduplicate an inbound signal; returns False for repeats."""
        if self._phase in TERMINAL_PHASES:
            return False
        if signal_id in self._signals:
            return False
        self._command(RunEvent(EventKind.SIGNAL_APPLIED, at, key=signal_id))
        return True

    def decide_approval(self, signal: ApprovalSignal, *, at: datetime) -> bool:
        """Apply a human decision; only the first decision is applied.

        The signal id may already be recorded if the signal arrived while the
        run was still executing steps; the decision is still applied exactly
        once. Repeat decisions are ignored without new state.
        """
        if self._phase is not RunPhase.AWAITING_APPROVAL:
            return False
        if self._approval_decision is not None:
            return False
        if signal.signal_id not in self._signals:
            self._command(RunEvent(EventKind.SIGNAL_APPLIED, at, key=signal.signal_id))
        self._command(
            RunEvent(
                EventKind.APPROVAL_DECIDED,
                at,
                key=signal.signal_id,
                detail=signal.decision,
            )
        )
        if signal.decision == "rejected":
            self.begin_compensation(at=at, reason="failed:approval_rejected")
        return True

    def record_dispatch(self, key: str, *, at: datetime, receipt_digest: str) -> str:
        """Record a connector dispatch exactly once per idempotency key."""
        if self._dispatch_key is not None:
            if self._dispatch_key != key:
                raise WorkflowInvariantError(
                    "dispatch idempotency key conflict for this run"
                )
            existing = self._dispatch_receipt_digest
            if existing != receipt_digest:
                raise WorkflowInvariantError(
                    "dispatch receipt conflict for idempotency key"
                )
            return receipt_digest
        self._require_phase(RunPhase.RUNNING)
        self._require_steps_complete()
        if self._config.requires_approval and self._approval_decision != "approved":
            raise WorkflowInvariantError("dispatch requires an approved decision")
        if self._config.dispatch_key is not None and self._config.dispatch_key != key:
            raise WorkflowInvariantError("dispatch key does not match the run plan")
        self._command(
            RunEvent(
                EventKind.DISPATCH_RECORDED,
                at,
                key=key,
                detail=receipt_digest,
            )
        )
        return receipt_digest

    def begin_compensation(self, *, at: datetime, reason: str) -> bool:
        """Enter compensation once; repeated calls are no-ops."""
        if self._phase is RunPhase.COMPENSATING or self._phase in TERMINAL_PHASES:
            return False
        self._pending_reason = reason
        self._command(RunEvent(EventKind.COMPENSATION_STARTED, at, detail=reason))
        if not self.pending_compensations:
            self._finish(at, reason)
        return True

    def complete_compensation(self, step_name: str, *, at: datetime) -> RunEvent | None:
        if step_name in self._compensated:
            return None
        self._require_phase(RunPhase.COMPENSATING)
        if step_name not in self._completed:
            raise WorkflowInvariantError(
                f"cannot compensate an incomplete step: {step_name}"
            )
        event = self._command(
            RunEvent(EventKind.COMPENSATION_COMPLETED, at, step=step_name)
        )
        if not self.pending_compensations:
            self._finish(at, self._pending_reason or "cancelled")
        return event

    def tick(self, at: datetime) -> tuple[StaleRunAlert, ...]:
        """Advance time-based checks: stale alerts and approval wait timeout."""
        if self._phase is not RunPhase.AWAITING_APPROVAL:
            return ()
        if self._approval_requested_at is None:
            return ()
        waited = at - self._approval_requested_at
        alerts: list[StaleRunAlert] = []
        if not self._stale_raised and waited >= self._config.stale_after:
            self._command(RunEvent(EventKind.STALE_ALERT_RAISED, at))
            last_progress = self._last_progress_at or self._approval_requested_at
            alerts.append(
                StaleRunAlert(
                    run_key=self._config.run_key,
                    queue=self._config.queue,
                    phase=self._phase,
                    stale_after_seconds=int(self._config.stale_after.total_seconds()),
                    last_progress_at=last_progress,
                    raised_at=at,
                )
            )
        timeout = self._config.wait_timeout
        if timeout is not None and waited >= timeout:
            self.begin_compensation(at=at, reason="failed:approval_wait_timeout")
        return tuple(alerts)

    def finish_completed(self, *, at: datetime) -> RunEvent | None:
        if self._phase is RunPhase.COMPLETED and self._terminal_detail == "completed":
            return None
        self._require_phase(RunPhase.RUNNING)
        self._require_steps_complete()
        if self._config.requires_approval and self._approval_decision != "approved":
            raise WorkflowInvariantError("completion requires an approved decision")
        if self._config.dispatch_key is not None and self._dispatch_key is None:
            raise WorkflowInvariantError("completion requires the planned dispatch")
        self._finish(at, "completed")
        return self._events[-1]

    def _finish(self, at: datetime, detail: str) -> None:
        self._command(RunEvent(EventKind.RUN_FINISHED, at, detail=detail))

    def _require_phase(self, phase: RunPhase) -> None:
        if self._phase is not phase:
            raise WorkflowInvariantError(
                f"command requires phase {phase.value}, current {self._phase.value}"
            )

    def _require_steps_complete(self) -> None:
        missing = [
            step.name for step in self._config.steps if step.name not in self._completed
        ]
        if missing:
            raise WorkflowInvariantError(
                f"steps not complete: {', '.join(sorted(missing))}"
            )

    def _command(self, event: RunEvent) -> RunEvent:
        self._fold(event)
        return event

    def _fold(self, event: RunEvent) -> None:
        """Fold one event into state; the single path for commands and replay."""
        self._events.append(event)
        self._last_progress_at = event.at
        match event.kind:
            case EventKind.RUN_STARTED:
                self._phase = RunPhase.RUNNING
            case EventKind.STEP_COMPLETED:
                if event.step is not None and event.key is not None:
                    self._completed[event.step] = event.key
            case EventKind.STEP_FAILED:
                if event.step is not None:
                    self._attempts[event.step] = self._attempts.get(event.step, 0) + 1
            case EventKind.SIGNAL_APPLIED:
                if event.key is not None:
                    self._signals.add(event.key)
            case EventKind.APPROVAL_REQUESTED:
                self._phase = RunPhase.AWAITING_APPROVAL
                self._approval_requested_at = event.at
            case EventKind.APPROVAL_DECIDED:
                self._approval_decision = event.detail
                if event.detail == "approved":
                    self._phase = RunPhase.RUNNING
            case EventKind.DISPATCH_RECORDED:
                self._dispatch_key = event.key
                self._dispatch_receipt_digest = event.detail
            case EventKind.COMPENSATION_STARTED:
                self._pending_reason = event.detail
                if event.detail and self._phase not in TERMINAL_PHASES:
                    self._phase = RunPhase.COMPENSATING
            case EventKind.COMPENSATION_COMPLETED:
                if event.step is not None:
                    self._compensated = (*self._compensated, event.step)
            case EventKind.STALE_ALERT_RAISED:
                self._stale_raised = True
            case EventKind.RUN_FINISHED:
                self._terminal_detail = event.detail
                if event.detail == "completed":
                    self._phase = RunPhase.COMPLETED
                elif event.detail == "cancelled":
                    self._phase = RunPhase.CANCELLED
                else:
                    self._phase = RunPhase.FAILED
