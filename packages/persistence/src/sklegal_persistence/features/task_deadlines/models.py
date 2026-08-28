"""Durable value objects for the Task and Deadline feature lane."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=4096)]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
OpaqueName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:/@-]{0,254}$")]

TaskState = Literal[
    "draft",
    "ready",
    "in_progress",
    "blocked",
    "completed",
    "cancelled",
    "failed",
    "retry_pending",
    "reconciliation_required",
    "reconciled",
]
DeadlineState = Literal[
    "blocked",
    "uncertain",
    "calculated",
    "reviewed",
    "operative",
    "cancelled",
    "failed",
    "retry_pending",
    "reconciliation_required",
    "reconciled",
    "superseded",
]
ReviewState = Literal["pending", "accepted", "rejected"]
WorkflowOutcome = Literal[
    "succeeded",
    "cancelled",
    "failed",
    "retry_pending",
    "reconciliation_required",
    "reconciled",
]


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def canonical_sha256(value: object) -> str:
    """Hash one stable JSON representation without leaking object reprs."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


class TaskDeadlineValue(BaseModel):
    """Strict immutable camel-case value shared only inside this lane."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class ProvenanceReference(TaskDeadlineValue):
    provenance_id: UUID
    source_kind: Literal[
        "human_command",
        "accepted_proposal",
        "deterministic_calculation",
        "human_review",
        "lifecycle_transition",
        "simulation",
    ]
    source_id: UUID
    source_version: int = Field(ge=1)
    source_sha256: Sha256
    recorded_at: datetime


class TriggerEvidence(TaskDeadlineValue):
    state: Literal["confirmed", "missing", "disputed"]
    trigger_event_id: UUID | None = None
    fact_assertion_id: UUID | None = None
    occurred_at: datetime | None = None
    evidence_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_confirmation(self) -> Self:
        evidence = (
            self.trigger_event_id,
            self.fact_assertion_id,
            self.occurred_at,
            self.evidence_sha256,
        )
        if self.state == "confirmed" and any(item is None for item in evidence):
            raise ValueError("confirmed trigger requires complete evidence")
        if self.state == "missing" and any(item is not None for item in evidence):
            raise ValueError("missing trigger cannot claim evidence")
        if self.occurred_at is not None and not _is_timezone_aware(self.occurred_at):
            raise ValueError("trigger occurrence must be timezone aware")
        return self


class RuleAuthorityEvidence(TaskDeadlineValue):
    state: Literal["current", "missing", "stale", "uncertain"]
    rule_id: OpaqueName
    rule_version: OpaqueName
    rule_sha256: Sha256
    authority_id: UUID | None = None
    authority_version: OpaqueName | None = None
    authority_content_sha256: Sha256 | None = None
    authority_span_sha256: Sha256 | None = None
    verified_at: datetime | None = None
    valid_until: datetime | None = None
    interval_days: int = Field(ge=0, le=3660)
    convention: Literal["calendar_days", "business_days"]
    include_trigger_day: bool = False

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        for value in (self.verified_at, self.valid_until):
            if value is not None and not _is_timezone_aware(value):
                raise ValueError("Authority timestamps must be timezone aware")
        authority = (
            self.authority_id,
            self.authority_version,
            self.authority_content_sha256,
            self.authority_span_sha256,
            self.verified_at,
            self.valid_until,
        )
        if self.state == "current":
            if any(item is None for item in authority):
                raise ValueError("current rule requires complete Authority evidence")
            if self.valid_until is not None and self.verified_at is not None:
                if self.valid_until <= self.verified_at:
                    raise ValueError("Authority validity window is invalid")
        return self


class HolidayCalendarEvidence(TaskDeadlineValue):
    state: Literal["current", "missing", "stale", "uncertain"]
    calendar_id: OpaqueName | None = None
    revision: Sha256 | None = None
    time_zone: str = Field(min_length=1, max_length=128)
    holidays: tuple[date, ...] = ()

    @model_validator(mode="after")
    def validate_calendar(self) -> Self:
        if self.state == "current" and (
            self.calendar_id is None or self.revision is None
        ):
            raise ValueError("current holiday calendar requires identity and revision")
        if len(self.holidays) != len(set(self.holidays)):
            raise ValueError("holiday dates must be unique")
        if tuple(sorted(self.holidays)) != self.holidays:
            raise ValueError("holiday dates must be ordered")
        return self


class ReminderRecord(TaskDeadlineValue):
    reminder_id: UUID
    scheduled_for: datetime
    offset_days: int = Field(ge=0, le=3650)
    channel: Literal["internal"] = "internal"
    state: Literal[
        "proposed",
        "acknowledged",
        "cancelled",
        "failed",
        "retry_pending",
        "reconciled",
    ] = "proposed"
    external_effect: Literal[False] = False


class TaskRecord(TaskDeadlineValue):
    schema_version: Literal["sklegal.task/v1"] = "sklegal.task/v1"
    tenant_id: UUID
    matter_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    title: ShortText
    description: BoundedText
    status: TaskState
    assigned_principal_id: UUID | None = None
    deadline_id: UUID | None = None
    due_at: datetime | None = None
    blocked_reason: BoundedText | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: BoundedText | None = None
    failure_code: OpaqueName | None = None
    retry_of_version: int | None = Field(default=None, ge=1)
    reconciliation_of_version: int | None = Field(default=None, ge=1)
    proposal_id: UUID | None = None
    decision_id: UUID | None = None
    policy_decision_id: UUID
    policy_revision: Sha256
    created_by_principal_id: UUID
    updated_by_principal_id: UUID
    created_at: datetime
    updated_at: datetime
    audit_id: UUID
    outbox_id: UUID
    provenance: tuple[ProvenanceReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_task_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Task update precedes creation")
        if (self.proposal_id is None) != (self.decision_id is None):
            raise ValueError("accepted proposal requires proposal and decision")
        if self.status == "ready" and self.assigned_principal_id is None:
            raise ValueError("ready Task requires an assignee")
        if (self.status == "blocked") != (self.blocked_reason is not None):
            raise ValueError("blocked Task requires only a blocked reason")
        if (self.status == "completed") != (self.completed_at is not None):
            raise ValueError("completed Task requires only a completion time")
        cancelled = (
            self.cancelled_at is not None and self.cancellation_reason is not None
        )
        if (self.status == "cancelled") != cancelled:
            raise ValueError(
                "cancelled Task requires only complete cancellation evidence"
            )
        if (self.status in {"failed", "retry_pending"}) != (
            self.failure_code is not None
        ):
            raise ValueError("failed or retrying Task requires only a failure code")
        if (self.status == "retry_pending") != (self.retry_of_version is not None):
            raise ValueError("retrying Task requires only a retry source version")
        if self.status in {"reconciliation_required", "reconciled"}:
            if self.reconciliation_of_version is None:
                raise ValueError("Task reconciliation requires a source version")
        elif self.reconciliation_of_version is not None:
            raise ValueError(
                "non-reconciling Task cannot carry reconciliation evidence"
            )
        return self


class DeadlineRecord(TaskDeadlineValue):
    schema_version: Literal["sklegal.deadline/v1"] = "sklegal.deadline/v1"
    tenant_id: UUID
    matter_id: UUID
    deadline_id: UUID
    version: int = Field(ge=1)
    title: ShortText
    state: DeadlineState
    review_state: ReviewState = "pending"
    trigger: TriggerEvidence
    rule: RuleAuthorityEvidence
    calendar: HolidayCalendarEvidence
    candidate_due_at: datetime | None = None
    operative_due_at: datetime | None = None
    calculation_sha256: Sha256
    uncertainty_codes: tuple[OpaqueName, ...] = ()
    reminders: tuple[ReminderRecord, ...] = ()
    reviewed_by_principal_id: UUID | None = None
    reviewed_at: datetime | None = None
    review_rationale: BoundedText | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: BoundedText | None = None
    failure_code: OpaqueName | None = None
    retry_of_version: int | None = Field(default=None, ge=1)
    reconciliation_of_version: int | None = Field(default=None, ge=1)
    supersedes_deadline_id: UUID | None = None
    policy_decision_id: UUID
    policy_revision: Sha256
    created_by_principal_id: UUID
    updated_by_principal_id: UUID
    created_at: datetime
    updated_at: datetime
    audit_id: UUID
    outbox_id: UUID
    provenance: tuple[ProvenanceReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_deadline_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Deadline update precedes creation")
        uncertain = self.state in {"blocked", "uncertain"}
        if uncertain != bool(self.uncertainty_codes):
            raise ValueError("blocked or uncertain Deadline must name uncertainty")
        calculated = self.state in {
            "calculated",
            "reviewed",
            "operative",
            "cancelled",
            "failed",
            "retry_pending",
            "reconciliation_required",
            "reconciled",
            "superseded",
        }
        if calculated != (self.candidate_due_at is not None):
            raise ValueError("calculated Deadline requires only a candidate due time")
        reviewed = self.review_state != "pending"
        complete_review = all(
            item is not None
            for item in (
                self.reviewed_by_principal_id,
                self.reviewed_at,
                self.review_rationale,
            )
        )
        if reviewed != complete_review:
            raise ValueError("Deadline review requires complete attribution")
        if self.state == "operative":
            if self.review_state != "accepted" or self.operative_due_at is None:
                raise ValueError(
                    "operative Deadline requires accepted review and due time"
                )
        elif self.operative_due_at is not None:
            raise ValueError(
                "non-operative Deadline cannot carry an operative due time"
            )
        cancelled = (
            self.cancelled_at is not None and self.cancellation_reason is not None
        )
        if (self.state == "cancelled") != cancelled:
            raise ValueError(
                "cancelled Deadline requires complete cancellation evidence"
            )
        if (self.state in {"failed", "retry_pending"}) != (
            self.failure_code is not None
        ):
            raise ValueError("failed or retrying Deadline requires only a failure code")
        if (self.state == "retry_pending") != (self.retry_of_version is not None):
            raise ValueError("retrying Deadline requires only a retry source version")
        if self.state in {"reconciliation_required", "reconciled"}:
            if self.reconciliation_of_version is None:
                raise ValueError("Deadline reconciliation requires a source version")
        elif self.reconciliation_of_version is not None:
            raise ValueError(
                "non-reconciling Deadline cannot carry reconciliation evidence"
            )
        if self.state == "superseded" and self.supersedes_deadline_id is None:
            raise ValueError("superseded Deadline must identify its prior record")
        return self


class SimulationReceipt(TaskDeadlineValue):
    schema_version: Literal["sklegal.action-simulation/v1"] = (
        "sklegal.action-simulation/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    receipt_id: UUID
    task_id: UUID
    deadline_id: UUID | None = None
    work_product_id: UUID
    work_product_version_id: UUID
    work_product_version_number: int = Field(ge=1)
    work_product_content_sha256: Sha256
    approval_id: UUID
    approval_snapshot_sha256: Sha256
    destination_sha256: Sha256
    action_kind: Literal["email"] = "email"
    state: Literal["simulated"] = "simulated"
    outcome: WorkflowOutcome = "succeeded"
    external_effect: Literal[False] = False
    connector_invoked: Literal[False] = False
    dispatch_attempted: Literal[False] = False
    policy_decision_id: UUID
    policy_revision: Sha256
    actor_principal_id: UUID
    created_at: datetime
    audit_id: UUID
    outbox_id: UUID
    provenance: tuple[ProvenanceReference, ...] = Field(min_length=1)


ResourceKind = Literal["task", "deadline", "action_simulation"]


class TaskDeadlineAuditEvent(TaskDeadlineValue):
    audit_id: UUID
    tenant_id: UUID
    matter_id: UUID
    resource_kind: ResourceKind
    resource_id: UUID
    resource_version: int = Field(ge=1)
    action: OpaqueName
    outcome: WorkflowOutcome
    actor_principal_id: UUID
    policy_decision_id: UUID
    policy_revision: Sha256
    correlation_id: UUID
    request_sha256: Sha256
    resource_sha256: Sha256
    occurred_at: datetime


class TaskDeadlineOutboxRecord(TaskDeadlineValue):
    outbox_id: UUID
    tenant_id: UUID
    matter_id: UUID
    audit_id: UUID
    resource_kind: ResourceKind
    resource_id: UUID
    topic: Literal[
        "task.projection",
        "deadline.projection",
        "external_action.simulation",
    ]
    payload_sha256: Sha256
    dispatch_allowed: Literal[False] = False
    created_at: datetime
