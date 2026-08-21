"""Typed workflow payloads that cross the Temporal history boundary.

These models serialize to Temporal payloads as JSON and back, so they
deliberately do not use pydantic strict mode: strict validation rejects the
plain strings and ISO timestamps that JSON deserialization produces. All
other safety flags (frozen, extra forbid, whitespace stripping) stay on.

Payloads carry references and digests only. Raw capability tokens, secrets,
and protected matter content never enter workflow history.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:@/-]*$",
    ),
]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("workflow timestamps must use UTC offset zero")
    return value


UtcDateTime = Annotated[datetime, AfterValidator(require_utc)]


class QueueKind(StrEnum):
    INTERACTIVE = "interactive"
    BATCH = "batch"
    LONG_CONTEXT = "long_context"
    CONNECTOR = "connector"


class RetryClass(StrEnum):
    INTERACTIVE = "interactive"
    BATCH = "batch"
    LONG_CONTEXT = "long_context"
    CONNECTOR = "connector"
    MODEL = "model"
    HUMAN = "human"


class RunPhase(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPENSATING = "compensating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_PHASES = frozenset({RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED})


class WorkflowPayload(BaseModel):
    """Immutable typed payload for workflow history."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class RunIdentity(WorkflowPayload):
    """Tenant-scoped identity of one workflow run."""

    tenant_id: UUID
    matter_id: UUID | None = None
    run_key: Slug
    correlation_id: UUID
    requested_by: Slug


class StepRecord(WorkflowPayload):
    """One typed activity invocation planned for a run."""

    name: Slug
    idempotency_key: Slug
    retry_class: RetryClass
    compensate: bool = True


class ApprovalRequirement(WorkflowPayload):
    """Human approval gate for a run, with staleness and timeout bounds."""

    approval_id: UUID
    stale_after_seconds: int = Field(default=900, ge=1)
    wait_timeout_seconds: int | None = Field(default=None, ge=1)


class ApprovalSignal(WorkflowPayload):
    """Human decision delivered to a waiting workflow run."""

    signal_id: Slug
    approval_id: UUID
    decision: Literal["approved", "rejected"]
    decided_by: Slug
    decided_at: UtcDateTime
    note: str = Field(default="", max_length=2000)


class DispatchRequest(WorkflowPayload):
    """Content-free connector dispatch instruction.

    The destination and artifact are referenced by digest so no protected
    content or credential material enters workflow history.
    """

    idempotency_key: Slug
    connector: Slug
    artifact_digest: Sha256Digest
    approval_id: UUID
    destination_digest: Sha256Digest


class DispatchReceipt(WorkflowPayload):
    """Simulation-mode receipt for one recorded dispatch."""

    idempotency_key: Slug
    receipt_digest: Sha256Digest
    simulated: Literal[True] = True
    recorded_at: UtcDateTime


class StepActivityInput(WorkflowPayload):
    """Typed input for one step activity invocation."""

    run_key: Slug
    step: StepRecord


class StepOutcome(WorkflowPayload):
    """Typed result of one step activity invocation."""

    run_key: Slug
    step_name: Slug
    idempotency_key: Slug
    result_digest: Sha256Digest
    completed_at: UtcDateTime


class StaleRunAlert(WorkflowPayload):
    """Alert raised when a run waits longer than its staleness bound."""

    run_key: Slug
    queue: QueueKind
    phase: RunPhase
    stale_after_seconds: int
    last_progress_at: UtcDateTime
    raised_at: UtcDateTime


class TaskWorkflowInput(WorkflowPayload):
    """Typed input for the matter task, batch, and connector workflows."""

    identity: RunIdentity
    queue: QueueKind
    task_ref: Slug
    context_tokens: int = Field(ge=0)
    steps: tuple[StepRecord, ...] = ()
    approval: ApprovalRequirement | None = None
    dispatch: DispatchRequest | None = None

    @model_validator(mode="after")
    def validate_dispatch_contract(self) -> Self:
        if self.queue is QueueKind.CONNECTOR and self.dispatch is None:
            raise ValueError("connector queue runs require a dispatch request")
        if self.dispatch is not None and self.approval is None:
            raise ValueError("dispatch requires an upstream approval reference")
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("step names must be unique within a run")
        keys = [step.idempotency_key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("step idempotency keys must be unique within a run")
        return self


class TaskWorkflowResult(WorkflowPayload):
    """Terminal summary returned when a run completes."""

    run_key: Slug
    phase: RunPhase
    completed_steps: tuple[Slug, ...]
    dispatch_receipt_digest: Sha256Digest | None = None
    finished_at: UtcDateTime
