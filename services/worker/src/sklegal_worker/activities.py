"""Typed Temporal activities for the worker queues.

All I/O and model-adjacent effects live in activities, never in deterministic
workflow code. Every activity here runs in simulation mode: dispatches are
recorded in an idempotent ledger and no connector performs live delivery.
Step work is delegated to an injected driver so tests can script timeouts,
policy denials, and model outages without touching workflow code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from temporalio import activity

from .document_export import WorkProductExporter
from .errors import PolicyDeniedError, WorkflowInvariantError
from .models import (
    DispatchReceipt,
    DispatchRequest,
    StaleRunAlert,
    StepActivityInput,
    StepOutcome,
    WorkProductExportRequest,
    WorkProductExportResult,
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


class FileDispatchLedger:
    """Crash-durable JSON-file ledger keyed on the dispatch idempotency key.

    The in-memory ledger cannot survive a worker process death between the
    external dispatch and the receipt write-back, so replay after a kill
    depends on receipts persisting outside the worker. This ledger keeps the
    same idempotency contract as ``SimulatedDispatchLedger`` but stores state
    in one JSON file written atomically (temporary file, flush, fsync,
    rename). Recording the same request after a restart returns the original
    receipt without a second entry; a different payload under a taken key is
    a conflict; an unreadable state file fails closed.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {"receipts": {}, "fingerprints": {}}
        try:
            state = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkflowInvariantError("dispatch ledger state is unreadable") from exc
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("receipts"), dict)
            or not isinstance(state.get("fingerprints"), dict)
        ):
            raise WorkflowInvariantError("dispatch ledger state is malformed")
        return state

    def _store(self, state: dict[str, dict[str, str]]) -> None:
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)

    @property
    def recorded_count(self) -> int:
        return len(self._load()["receipts"])

    def receipts(self) -> dict[str, DispatchReceipt]:
        """Return every recorded receipt keyed by idempotency key."""

        return {
            key: DispatchReceipt(
                idempotency_key=key,
                receipt_digest=value["receipt_digest"],
                recorded_at=datetime.fromisoformat(value["recorded_at"]),
            )
            for key, value in sorted(self._load()["receipts"].items())
        }

    def record(self, request: DispatchRequest, *, at: datetime) -> DispatchReceipt:
        fingerprint = _digest(
            "dispatch",
            request.connector,
            request.artifact_digest,
            str(request.approval_id),
            request.destination_digest,
        )
        state = self._load()
        existing = state["receipts"].get(request.idempotency_key)
        if existing is not None:
            if state["fingerprints"][request.idempotency_key] != fingerprint:
                raise WorkflowInvariantError(
                    "dispatch idempotency key reused with a different payload"
                )
            return DispatchReceipt(
                idempotency_key=request.idempotency_key,
                receipt_digest=existing["receipt_digest"],
                recorded_at=datetime.fromisoformat(existing["recorded_at"]),
            )
        receipt = DispatchReceipt(
            idempotency_key=request.idempotency_key,
            receipt_digest=_digest("receipt", request.idempotency_key, fingerprint),
            recorded_at=at,
        )
        state["receipts"][request.idempotency_key] = {
            "receipt_digest": receipt.receipt_digest,
            "recorded_at": at.isoformat(),
        }
        state["fingerprints"][request.idempotency_key] = fingerprint
        self._store(state)
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
    """Activity set shared by all four task queues (simulation mode).

    ``run_task_step`` heartbeats to Temporal every ``heartbeat_seconds`` while
    its driver runs so a killed worker is detected at the activity heartbeat
    timeout instead of the longer ``StartToClose`` timeout. The driver runs
    in a thread executor because a blocking driver would stall the worker
    event loop and no heartbeat could ever be sent. Heartbeat payloads carry
    only content-free markers (run key, step name, tick count).
    """

    def __init__(
        self,
        *,
        step_driver: StepDriver | None = None,
        approval_gate: ApprovalGate | None = None,
        dispatch_ledger: DispatchLedger | None = None,
        stale_sink: StaleAlertSink | None = None,
        work_product_exporter: WorkProductExporter | None = None,
        clock: Callable[[], datetime] = _utcnow,
        heartbeat_seconds: float | None = 10.0,
    ) -> None:
        if heartbeat_seconds is not None and heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive when set")
        self._step_driver = step_driver or SimulatedStepDriver()
        self._approval_gate = approval_gate or StaticApprovalGate({})
        self._dispatch_ledger = dispatch_ledger or SimulatedDispatchLedger()
        self._stale_sink = stale_sink or InMemoryStaleAlertSink()
        self._work_product_exporter = work_product_exporter
        self._clock = clock
        self._heartbeat_seconds = heartbeat_seconds

    @property
    def heartbeat_seconds(self) -> float | None:
        return self._heartbeat_seconds

    async def _run_step_with_heartbeats(
        self, request: StepActivityInput, at: datetime
    ) -> str:
        loop = asyncio.get_running_loop()
        running = loop.run_in_executor(
            None, lambda: self._step_driver.run(request, at=at)
        )
        while True:
            done, _pending = await asyncio.wait(
                {running}, timeout=self._heartbeat_seconds
            )
            if done:
                return running.result()
            if activity.in_activity():
                activity.heartbeat(
                    {
                        "run_key": request.run_key,
                        "step": request.step.name,
                        "tick": True,
                    }
                )

    @activity.defn
    async def run_task_step(self, request: StepActivityInput) -> StepOutcome:
        """Execute one typed step through the injected driver."""
        at = self._clock()
        if self._heartbeat_seconds is None:
            digest = self._step_driver.run(request, at=at)
        else:
            digest = await self._run_step_with_heartbeats(request, at)
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

    @activity.defn
    async def export_tracked_work_product(
        self, request: WorkProductExportRequest
    ) -> WorkProductExportResult:
        """Export one exact approved Work Product version and validate preview."""

        if self._work_product_exporter is None:
            raise WorkflowInvariantError("Work Product exporter is not configured")
        return self._work_product_exporter.export(request)
