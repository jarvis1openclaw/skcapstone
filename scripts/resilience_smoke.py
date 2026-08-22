#!/usr/bin/env python3
"""SKL-S3-07 resilience smoke driver for the pinned development stack.

Runs the live worker-kill and Temporal-restart smokes from
docs/development/RESILIENCE-SMOKE.md against the disposable compose stack
(deploy/chiap01/compose.dev.yml). All workflows use synthetic identities and
simulation-mode activities; the dispatch ledger is a local JSON file so the
receipt count survives a worker kill. No protected matter content, credential
material, or HammerTime path is touched.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sklegal_worker.activities import WorkerActivities
from sklegal_worker.errors import PolicyDeniedError
from sklegal_worker.models import (
    ApprovalRequirement,
    ApprovalSignal,
    DispatchReceipt,
    DispatchRequest,
    QueueKind,
    RetryClass,
    RunIdentity,
    StepActivityInput,
    StepRecord,
    TaskWorkflowInput,
)
from sklegal_worker.queues import task_queue_name
from sklegal_worker.workflows import MatterTaskWorkflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

SYNTHETIC_TENANT = UUID("10000000-0000-4000-8000-0000000000e1")
SYNTHETIC_MATTER = UUID("10000000-0000-4000-8000-0000000000e2")
OPERATOR = "skl-s3-07-smoke"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _digest(*parts: str) -> str:
    """Mirror the simulation digest construction in activities.py."""
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


class FileDispatchLedger:
    """JSON-file dispatch ledger with SimulatedDispatchLedger semantics.

    Persisting receipts outside the worker process is what lets the smoke
    prove that a replayed dispatch after a worker kill records no second
    receipt. Writes are atomic (temp file plus rename).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"receipts": {}, "fingerprints": {}}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _store(self, state: dict[str, Any]) -> None:
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, self._path)

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
                raise ValueError("dispatch idempotency key reused with new payload")
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


class MarkingStepDriver:
    """Simulation step driver that marks activity start and sleeps.

    The marker file lets the operator kill the worker while the activity is
    provably in flight. The result digest matches SimulatedStepDriver.
    """

    def __init__(self, *, delay_seconds: float, marker_path: Path) -> None:
        self._delay_seconds = delay_seconds
        self._marker_path = marker_path

    def run(self, request: StepActivityInput, *, at: datetime) -> str:
        with self._marker_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "activity_started",
                        "run_key": request.run_key,
                        "step": request.step.name,
                        "pid": os.getpid(),
                        "at": at.isoformat(),
                    }
                )
                + "\n"
            )
        if self._delay_seconds > 0:
            time.sleep(self._delay_seconds)
        return _digest(
            "step",
            request.run_key,
            request.step.name,
            request.step.idempotency_key,
        )


def _build_input(run_key: str, approval_id: UUID) -> TaskWorkflowInput:
    return TaskWorkflowInput(
        identity=RunIdentity(
            tenant_id=SYNTHETIC_TENANT,
            matter_id=SYNTHETIC_MATTER,
            run_key=run_key,
            correlation_id=uuid4(),
            requested_by=OPERATOR,
        ),
        queue=QueueKind.INTERACTIVE,
        task_ref=OPERATOR,
        context_tokens=0,
        steps=(
            StepRecord(
                name="smoke-step",
                idempotency_key=f"{run_key}-step",
                retry_class=RetryClass.INTERACTIVE,
            ),
        ),
        approval=ApprovalRequirement(
            approval_id=approval_id,
            stale_after_seconds=3600,
        ),
        dispatch=DispatchRequest(
            idempotency_key=f"{run_key}-dispatch",
            connector="smoke-connector",
            artifact_digest=_digest("smoke", run_key, "artifact"),
            approval_id=approval_id,
            destination_digest=_digest("smoke", run_key, "destination"),
        ),
    )


async def _connect(address: str) -> Client:
    # The workflow models are pydantic v2; Temporal documents the pydantic
    # data converter as required so typed fields (UUID, datetime) survive the
    # payload round trip instead of degrading to plain strings.
    return await Client.connect(
        address, namespace="default", data_converter=pydantic_data_converter
    )


async def _run_worker(args: argparse.Namespace) -> None:
    client = await _connect(args.address)
    activities = WorkerActivities(
        step_driver=MarkingStepDriver(
            delay_seconds=args.step_delay, marker_path=Path(args.marker)
        ),
        approval_gate=_StaticApproval(UUID(args.approval_id)),
        dispatch_ledger=FileDispatchLedger(Path(args.ledger)),
    )
    worker = Worker(
        client,
        task_queue=task_queue_name(QueueKind.INTERACTIVE),
        workflows=[MatterTaskWorkflow],
        activities=[
            activities.run_task_step,
            activities.dispatch_connector,
            activities.compensate_step,
            activities.raise_stale_run_alert,
        ],
    )
    print(
        json.dumps(
            {
                "event": "worker_started",
                "pid": os.getpid(),
                "task_queue": task_queue_name(QueueKind.INTERACTIVE),
                "step_delay": args.step_delay,
                "at": _utcnow().isoformat(),
            }
        ),
        flush=True,
    )
    await worker.run()


class _StaticApproval:
    """Approves exactly the synthetic approval id; anything else fails closed."""

    def __init__(self, approval_id: UUID) -> None:
        self._approval_id = approval_id

    def ensure_approved(self, approval_id: UUID) -> None:
        if approval_id != self._approval_id:
            raise PolicyDeniedError(f"approval {approval_id} is not approved")


def _handle(client: Client, workflow_id: str) -> WorkflowHandle[Any, Any]:
    return client.get_workflow_handle_for(MatterTaskWorkflow.run, workflow_id)


async def _run_start(args: argparse.Namespace) -> None:
    client = await _connect(args.address)
    handle = await client.start_workflow(
        MatterTaskWorkflow.run,
        _build_input(args.run_key, UUID(args.approval_id)),
        id=args.workflow_id,
        task_queue=task_queue_name(QueueKind.INTERACTIVE),
    )
    print(
        json.dumps(
            {
                "event": "workflow_started",
                "workflow_id": handle.id,
                "run_id": getattr(handle, "result_run_id", None),
                "at": _utcnow().isoformat(),
            }
        )
    )


async def _run_approve(args: argparse.Namespace) -> None:
    client = await _connect(args.address)
    handle = _handle(client, args.workflow_id)
    await handle.signal(
        MatterTaskWorkflow.submit_approval,
        ApprovalSignal(
            signal_id=args.signal_id,
            approval_id=UUID(args.approval_id),
            decision="approved",
            decided_by=OPERATOR,
            decided_at=_utcnow(),
        ),
    )
    print(
        json.dumps(
            {
                "event": "approval_signaled",
                "workflow_id": args.workflow_id,
                "at": _utcnow().isoformat(),
            }
        )
    )


async def _run_query(args: argparse.Namespace) -> None:
    client = await _connect(args.address)
    handle = _handle(client, args.workflow_id)
    description = await handle.describe()
    phase: str | None = None
    if description.status in (None, WorkflowExecutionStatus.RUNNING):
        phase = await handle.query(MatterTaskWorkflow.phase)
    print(
        json.dumps(
            {
                "event": "workflow_query",
                "workflow_id": args.workflow_id,
                "status": (
                    description.status.name if description.status else "RUNNING"
                ),
                "phase": phase,
                "at": _utcnow().isoformat(),
            }
        )
    )


async def _run_verify(args: argparse.Namespace) -> None:
    client = await _connect(args.address)
    handle = _handle(client, args.workflow_id)
    description = await handle.describe()
    scheduled_names: dict[int, str] = {}
    activity_events: dict[str, dict[str, int]] = {}

    def bump(name: str, kind: str) -> None:
        bucket = activity_events.setdefault(name, {})
        bucket[kind] = bucket.get(kind, 0) + 1

    async for event in handle.fetch_history_events():
        if event.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            attributes = event.activity_task_scheduled_event_attributes
            scheduled_names[event.event_id] = attributes.activity_type.name
            bump(attributes.activity_type.name, "scheduled")
        elif event.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            scheduled_id = (
                event.activity_task_started_event_attributes.scheduled_event_id
            )
            bump(scheduled_names.get(scheduled_id, "unknown"), "started")
        elif event.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            scheduled_id = (
                event.activity_task_completed_event_attributes.scheduled_event_id
            )
            bump(scheduled_names.get(scheduled_id, "unknown"), "completed")
        elif event.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
            scheduled_id = (
                event.activity_task_timed_out_event_attributes.scheduled_event_id
            )
            bump(scheduled_names.get(scheduled_id, "unknown"), "timed_out")
    result: dict[str, Any] | None = None
    if description.status is not None and description.status.name == "COMPLETED":
        outcome = await handle.result()
        result = {
            "run_key": outcome.run_key,
            "phase": outcome.phase.value,
            "completed_steps": list(outcome.completed_steps),
            "dispatch_receipt_digest": outcome.dispatch_receipt_digest,
            "finished_at": outcome.finished_at.isoformat(),
        }
    ledger_path = Path(args.ledger)
    ledger = (
        json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger_path.exists()
        else {"receipts": {}}
    )
    print(
        json.dumps(
            {
                "event": "workflow_verify",
                "workflow_id": args.workflow_id,
                "status": (
                    description.status.name if description.status else "RUNNING"
                ),
                "history_length": description.history_length,
                "activity_events": activity_events,
                "ledger_receipt_count": len(ledger["receipts"]),
                "result": result,
                "at": _utcnow().isoformat(),
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="127.0.0.1:17233")
    commands = parser.add_subparsers(dest="command", required=True)

    worker = commands.add_parser("worker", help="run an interactive-queue worker")
    worker.add_argument("--ledger", required=True)
    worker.add_argument("--marker", required=True)
    worker.add_argument("--approval-id", required=True)
    worker.add_argument("--step-delay", type=float, default=0.0)

    start = commands.add_parser("start", help="start a synthetic workflow")
    start.add_argument("--workflow-id", required=True)
    start.add_argument("--run-key", required=True)
    start.add_argument("--approval-id", required=True)

    approve = commands.add_parser("approve", help="signal approval")
    approve.add_argument("--workflow-id", required=True)
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--signal-id", required=True)

    query = commands.add_parser("query", help="query phase and status")
    query.add_argument("--workflow-id", required=True)

    verify = commands.add_parser("verify", help="collect completion evidence")
    verify.add_argument("--workflow-id", required=True)
    verify.add_argument("--ledger", required=True)

    args = parser.parse_args()
    runners = {
        "worker": _run_worker,
        "start": _run_start,
        "approve": _run_approve,
        "query": _run_query,
        "verify": _run_verify,
    }
    asyncio.run(runners[args.command](args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
