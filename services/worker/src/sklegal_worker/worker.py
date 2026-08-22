"""Worker factories binding task queues, workflows, and activities."""

from __future__ import annotations

from dataclasses import dataclass

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from .activities import WorkerActivities
from .models import QueueKind
from .queues import task_queue_name
from .workflows import (
    ConnectorDispatchWorkflow,
    MatterBatchWorkflow,
    MatterTaskWorkflow,
)

# Workflow payloads are pydantic v2 models. Temporal documents the pydantic
# data converter as required for them: without it UUID and datetime fields
# degrade to plain strings across payload round trips, which corrupts typed
# activity results after a worker restart or workflow replay. Every runtime
# client entrypoint must connect through this converter.
WORKER_DATA_CONVERTER = pydantic_data_converter


async def connect_worker_client(
    address: str, *, namespace: str = "default"
) -> Client:
    """Connect the runtime Temporal client with the pinned converter."""

    return await Client.connect(
        address,
        namespace=namespace,
        data_converter=WORKER_DATA_CONVERTER,
    )


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    """Which workflows serve one task queue."""

    queue: QueueKind
    workflows: tuple[type, ...]


def worker_specs() -> tuple[WorkerSpec, ...]:
    """One worker spec per task queue."""
    return (
        WorkerSpec(QueueKind.INTERACTIVE, (MatterTaskWorkflow,)),
        WorkerSpec(QueueKind.BATCH, (MatterBatchWorkflow,)),
        WorkerSpec(QueueKind.LONG_CONTEXT, (MatterTaskWorkflow,)),
        WorkerSpec(QueueKind.CONNECTOR, (ConnectorDispatchWorkflow,)),
    )


def build_worker(
    client: Client, spec: WorkerSpec, activities: WorkerActivities
) -> Worker:
    """Bind one task queue to its workflows and typed activities."""
    return Worker(
        client,
        task_queue=task_queue_name(spec.queue),
        workflows=list(spec.workflows),
        activities=[
            activities.run_task_step,
            activities.dispatch_connector,
            activities.compensate_step,
            activities.raise_stale_run_alert,
        ],
    )
