"""Frozen wire contracts for Task, Deadline, and simulation operations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from sklegal_persistence.features.task_deadlines.models import (
    BoundedText,
    DeadlineRecord,
    HolidayCalendarEvidence,
    OpaqueName,
    RuleAuthorityEvidence,
    Sha256,
    ShortText,
    SimulationReceipt,
    TaskRecord,
    TriggerEvidence,
)


class TaskDeadlineContract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class UpsertTaskCommand(TaskDeadlineContract):
    schema_version: Literal["sklegal.task-command/v1"] = "sklegal.task-command/v1"
    expected_version: Literal[0] = 0
    title: ShortText
    description: BoundedText
    assigned_principal_id: UUID | None = None
    deadline_id: UUID | None = None
    due_at: datetime | None = None
    proposal_id: UUID | None = None
    decision_id: UUID | None = None

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        if (self.proposal_id is None) != (self.decision_id is None):
            raise ValueError("proposal and decision must be supplied together")
        if (self.deadline_id is None) != (self.due_at is None):
            raise ValueError(
                "Task Deadline identity and due time must be supplied together"
            )
        return self


class TaskTransitionCommand(TaskDeadlineContract):
    schema_version: Literal["sklegal.task-transition-command/v1"] = (
        "sklegal.task-transition-command/v1"
    )
    expected_version: int = Field(ge=1)
    transition: Literal[
        "ready",
        "start",
        "block",
        "complete",
        "cancel",
        "fail",
        "retry",
        "require_reconciliation",
        "reconcile",
    ]
    assigned_principal_id: UUID | None = None
    reason: BoundedText | None = None
    failure_code: OpaqueName | None = None

    @model_validator(mode="after")
    def validate_transition_evidence(self) -> Self:
        if self.transition == "ready" and self.assigned_principal_id is None:
            raise ValueError("ready transition requires an assignee")
        if self.transition in {"block", "cancel", "require_reconciliation"}:
            if self.reason is None:
                raise ValueError("transition requires a reason")
        if self.transition in {"fail", "retry"} and self.failure_code is None:
            raise ValueError("failure transition requires a failure code")
        return self


class ComputeDeadlineCommand(TaskDeadlineContract):
    schema_version: Literal["sklegal.deadline-command/v1"] = (
        "sklegal.deadline-command/v1"
    )
    expected_version: Literal[0] = 0
    title: ShortText
    trigger: TriggerEvidence
    rule: RuleAuthorityEvidence
    calendar: HolidayCalendarEvidence
    reminder_offsets_days: tuple[int, ...] = Field(default=(), max_length=32)
    supersedes_deadline_id: UUID | None = None

    @model_validator(mode="after")
    def validate_reminders(self) -> Self:
        if len(self.reminder_offsets_days) != len(set(self.reminder_offsets_days)):
            raise ValueError("reminder offsets must be unique")
        if any(value < 0 or value > 3650 for value in self.reminder_offsets_days):
            raise ValueError("reminder offset is outside the supported range")
        if tuple(sorted(self.reminder_offsets_days, reverse=True)) != (
            self.reminder_offsets_days
        ):
            raise ValueError("reminder offsets must be descending")
        return self


class DeadlineReviewCommand(TaskDeadlineContract):
    schema_version: Literal["sklegal.deadline-review-command/v1"] = (
        "sklegal.deadline-review-command/v1"
    )
    expected_version: int = Field(ge=1)
    decision: Literal["accepted", "rejected"]
    rationale: BoundedText


class DeadlineTransitionCommand(TaskDeadlineContract):
    schema_version: Literal["sklegal.deadline-transition-command/v1"] = (
        "sklegal.deadline-transition-command/v1"
    )
    expected_version: int = Field(ge=1)
    transition: Literal[
        "cancel",
        "fail",
        "retry",
        "require_reconciliation",
        "reconcile",
    ]
    reason: BoundedText | None = None
    failure_code: OpaqueName | None = None

    @model_validator(mode="after")
    def validate_transition_evidence(self) -> Self:
        if self.transition in {"cancel", "require_reconciliation"}:
            if self.reason is None:
                raise ValueError("transition requires a reason")
        if self.transition in {"fail", "retry"} and self.failure_code is None:
            raise ValueError("failure transition requires a failure code")
        return self


class SimulationHandoffCommand(TaskDeadlineContract):
    schema_version: Literal["sklegal.action-simulation-command/v1"] = (
        "sklegal.action-simulation-command/v1"
    )
    task_id: UUID
    deadline_id: UUID | None = None
    work_product_id: UUID
    work_product_version_id: UUID
    work_product_version_number: int = Field(ge=1)
    work_product_content_sha256: Sha256
    approval_id: UUID
    approval_snapshot_sha256: Sha256
    approval_current: Literal[True]
    destination_sha256: Sha256
    action_kind: Literal["email"] = "email"
    simulation_only: Literal[True] = True


class MutationEvidence(TaskDeadlineContract):
    idempotency_key_sha256: Sha256
    request_sha256: Sha256
    resource_version: int = Field(ge=1)
    audit_id: UUID
    outbox_id: UUID
    correlation_id: UUID


class TaskMutationReceipt(MutationEvidence):
    task: TaskRecord


class DeadlineMutationReceipt(MutationEvidence):
    deadline: DeadlineRecord


class SimulationMutationReceipt(MutationEvidence):
    simulation: SimulationReceipt


class TaskListRead(TaskDeadlineContract):
    schema_version: Literal["sklegal.task-list/v1"] = "sklegal.task-list/v1"
    tenant_id: UUID
    matter_id: UUID
    projection_revision: Sha256
    tasks: tuple[TaskRecord, ...]


class DeadlineListRead(TaskDeadlineContract):
    schema_version: Literal["sklegal.deadline-list/v1"] = "sklegal.deadline-list/v1"
    tenant_id: UUID
    matter_id: UUID
    projection_revision: Sha256
    deadlines: tuple[DeadlineRecord, ...]
