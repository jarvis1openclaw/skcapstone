"""Typed Temporal activities for the worker queues.

All I/O and model-adjacent effects live in activities, never in deterministic
workflow code. Every activity here runs in simulation mode: dispatches are
recorded in an idempotent ledger and no connector performs live delivery.
Step work is delegated to an injected driver so tests can script timeouts,
policy denials, and model outages without touching workflow code.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from temporalio import activity

from .errors import PolicyDeniedError, WorkflowInvariantError
from .models import (
    DispatchReceipt,
    DispatchRequest,
    StaleRunAlert,
    StepActivityInput,
    StepOutcome,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _digest(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


class StepDriver(Protocol):
    """Executes one step and returns a content-free result digest."""

    def run(self, request: StepActivityInput, *, at: datetime) -> str: ...


class SimulatedStepDriver:
    """Deterministic simulation driver: the digest is a pure function."""

    def run(self, request: StepActivityInput, *, at: datetime) -> str:
        del at
        return _digest(
            "step",
            request.run_key,
            request.step.name,
            request.step.idempotency_key,
        )


class ApprovalGate(Protocol):
    """Verifies that an approval reference is currently approved."""

    def ensure_approved(self, approval_id: UUID) -> None: ...


class StaticApprovalGate:
    """Approval gate over pinned decisions; unknown ids fail closed."""

    def __init__(self, decisions: Mapping[UUID, str]) -> None:
        self._decisions = dict(decisions)

    def ensure_approved(self, approval_id: UUID) -> None:
        if self._decisions.get(approval_id) != "approved":
            raise PolicyDeniedError(
                f"approval {approval_id} is not in an approved state"
            )


class DispatchLedger(Protocol):
    """Idempotent record of connector dispatches."""

    def record(self, request: DispatchRequest, *, at: datetime) -> DispatchReceipt: ...


class SimulatedDispatchLedger:
    """In-memory simulation ledger keyed on the dispatch idempotency key.

    Recording the same request twice returns the original receipt without a
    second entry. A different payload under a taken key is a conflict.
    """

    def __init__(self) -> None:
        self._receipts: dict[str, DispatchReceipt] = {}
        self._fingerprints: dict[str, str] = {}

    @property
    def recorded_count(self) -> int:
        return len(self._receipts)

    def record(self, request: DispatchRequest, *, at: datetime) -> DispatchReceipt:
        fingerprint = _digest(
            "dispatch",
            request.connector,
            request.artifact_digest,
            str(request.approval_id),
            request.destination_digest,
        )
        existing = self._receipts.get(request.idempotency_key)
        if existing is not None:
            if self._fingerprints[request.idempotency_key] != fingerprint:
                raise WorkflowInvariantError(
                    "dispatch idempotency key reused with a different payload"
                )
            return existing
        receipt = DispatchReceipt(
            idempotency_key=request.idempotency_key,
            receipt_digest=_digest("receipt", request.idempotency_key, fingerprint),
            recorded_at=at,
        )
        self._receipts[request.idempotency_key] = receipt
        self._fingerprints[request.idempotency_key] = fingerprint
        return receipt


class StaleAlertSink(Protocol):
    """Records stale-run alerts; returns False for duplicates."""

    def record(self, alert: StaleRunAlert) -> bool: ...


class InMemoryStaleAlertSink:
    """Deduplicating in-memory alert sink for simulation mode."""

    def __init__(self) -> None:
        self._alerts: list[StaleRunAlert] = []
        self._seen: set[tuple[str, str]] = set()

    @property
    def alerts(self) -> tuple[StaleRunAlert, ...]:
        return tuple(self._alerts)

    def record(self, alert: StaleRunAlert) -> bool:
        marker = (alert.run_key, alert.phase.value)
        if marker in self._seen:
            return False
        self._seen.add(marker)
        self._alerts.append(alert)
        return True


class WorkerActivities:
    """Activity set shared by all four task queues (simulation mode)."""

    def __init__(
        self,
        *,
        step_driver: StepDriver | None = None,
        approval_gate: ApprovalGate | None = None,
        dispatch_ledger: DispatchLedger | None = None,
        stale_sink: StaleAlertSink | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._step_driver = step_driver or SimulatedStepDriver()
        self._approval_gate = approval_gate or StaticApprovalGate({})
        self._dispatch_ledger = dispatch_ledger or SimulatedDispatchLedger()
        self._stale_sink = stale_sink or InMemoryStaleAlertSink()
        self._clock = clock

    @activity.defn
    async def run_task_step(self, request: StepActivityInput) -> StepOutcome:
        """Execute one typed step through the injected driver."""
        at = self._clock()
        digest = self._step_driver.run(request, at=at)
        return StepOutcome(
            run_key=request.run_key,
            step_name=request.step.name,
            idempotency_key=request.step.idempotency_key,
            result_digest=digest,
            completed_at=at,
        )

    @activity.defn
    async def dispatch_connector(self, request: DispatchRequest) -> DispatchReceipt:
        """Record one connector dispatch after verifying its approval.

        The approval gate fails closed, and the ledger records each
        idempotency key exactly once, so a replayed or duplicated dispatch
        never produces a second record.
        """
        self._approval_gate.ensure_approved(request.approval_id)
        return self._dispatch_ledger.record(request, at=self._clock())

    @activity.defn
    async def compensate_step(self, request: StepActivityInput) -> StepOutcome:
        """Record compensation for one previously completed step."""
        at = self._clock()
        return StepOutcome(
            run_key=request.run_key,
            step_name=request.step.name,
            idempotency_key=request.step.idempotency_key,
            result_digest=_digest(
                "compensate",
                request.run_key,
                request.step.name,
                request.step.idempotency_key,
            ),
            completed_at=at,
        )

    @activity.defn
    async def raise_stale_run_alert(self, alert: StaleRunAlert) -> bool:
        """Publish a stale-run alert exactly once per run and phase."""
        return self._stale_sink.record(alert)
