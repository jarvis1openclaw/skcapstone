"""Deadline calculation, deadline, task, and communication entities."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    Self,
)

from pydantic import (
    Field,
    model_validator,
)

from ..base import (
    MatterEntity,
    MatterStatefulEntity,
)
from ..exceptions import DomainTransitionError
from ..states import (
    CommunicationStatus,
    DeadlineStatus,
    TaskStatus,
)
from ..value_objects import (
    DomainId,
    NonEmptyText,
    Sha256,
    ShortText,
    SourceReference,
    UtcDateTime,
    unique_ids,
)


class DeadlineCalculation(MatterEntity):
    trigger_fact_id: DomainId
    calculation_rule: NonEmptyText
    candidate_due_at: UtcDateTime
    calculated_at: UtcDateTime
    source_references: Annotated[tuple[SourceReference, ...], Field(min_length=1)]
    calculation_version: ShortText

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "trigger_fact_id",
        "calculation_rule",
        "candidate_due_at",
        "calculated_at",
        "source_references",
        "calculation_version",
    }

    @model_validator(mode="after")
    def validate_calculated_time(self) -> DeadlineCalculation:
        if self.calculated_at > self.updated_at:
            raise ValueError("calculated_at cannot be later than updated_at")
        return self


class Deadline(MatterStatefulEntity):
    title: ShortText
    candidate_due_at: UtcDateTime | None = None
    operative_due_at: UtcDateTime | None = None
    trigger_fact_id: DomainId | None = None
    calculation_id: DomainId | None = None
    review_validation_id: DomainId | None = None
    completed_at: UtcDateTime | None = None
    status: DeadlineStatus = DeadlineStatus.CANDIDATE

    TRANSITIONS: ClassVar = {
        DeadlineStatus.CANDIDATE: frozenset(
            {DeadlineStatus.REVIEWED, DeadlineStatus.WITHDRAWN}
        ),
        DeadlineStatus.REVIEWED: frozenset(
            {DeadlineStatus.OPERATIVE, DeadlineStatus.WITHDRAWN}
        ),
        DeadlineStatus.OPERATIVE: frozenset(
            {
                DeadlineStatus.SATISFIED,
                DeadlineStatus.MISSED,
                DeadlineStatus.WITHDRAWN,
            }
        ),
        DeadlineStatus.SATISFIED: frozenset(),
        DeadlineStatus.MISSED: frozenset({DeadlineStatus.SATISFIED}),
        DeadlineStatus.WITHDRAWN: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "candidate_due_at",
            "operative_due_at",
            "trigger_fact_id",
            "calculation_id",
            "review_validation_id",
            "completed_at",
        }
    )

    @model_validator(mode="after")
    def validate_deadline_provenance(self) -> Deadline:
        provenance = (
            self.trigger_fact_id,
            self.calculation_id,
            self.review_validation_id,
        )
        if self.status in {
            DeadlineStatus.REVIEWED,
            DeadlineStatus.OPERATIVE,
            DeadlineStatus.SATISFIED,
            DeadlineStatus.MISSED,
        }:
            if any(value is None for value in provenance):
                raise ValueError(
                    "reviewed deadline requires trigger, calculation, and review provenance"
                )
        if (
            self.status
            in {
                DeadlineStatus.OPERATIVE,
                DeadlineStatus.SATISFIED,
                DeadlineStatus.MISSED,
            }
            and self.operative_due_at is None
        ):
            raise ValueError("operative deadline state requires operative_due_at")
        if self.status == DeadlineStatus.SATISFIED and self.completed_at is None:
            raise ValueError("satisfied deadline requires completed_at")
        if self.status == DeadlineStatus.SATISFIED:
            if self.completed_at != self.updated_at:
                raise ValueError(
                    "completed_at must equal the satisfaction transition time"
                )
        if self.status != DeadlineStatus.SATISFIED and self.completed_at is not None:
            raise ValueError("only a satisfied deadline can carry completed_at")
        if self.status in {DeadlineStatus.CANDIDATE, DeadlineStatus.REVIEWED}:
            if self.operative_due_at is not None:
                raise ValueError("non-operative deadline cannot carry operative_due_at")
        if self.status == DeadlineStatus.CANDIDATE:
            if self.review_validation_id is not None:
                raise ValueError("deadline candidate cannot carry completed review")
        return self


class Task(MatterStatefulEntity):
    title: ShortText
    description: NonEmptyText
    assigned_principal_id: DomainId | None = None
    due_at: UtcDateTime | None = None
    blocked_reason: NonEmptyText | None = None
    completed_at: UtcDateTime | None = None
    status: TaskStatus = TaskStatus.DRAFT

    TRANSITIONS: ClassVar = {
        TaskStatus.DRAFT: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
        TaskStatus.READY: frozenset(
            {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
        ),
        TaskStatus.IN_PROGRESS: frozenset(
            {TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        ),
        TaskStatus.BLOCKED: frozenset(
            {TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}
        ),
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"blocked_reason", "completed_at"}
    )

    @model_validator(mode="after")
    def validate_task_state(self) -> Task:
        if self.status == TaskStatus.READY and self.assigned_principal_id is None:
            raise ValueError("ready task requires an assignee")
        if self.status == TaskStatus.BLOCKED and self.blocked_reason is None:
            raise ValueError("blocked task requires blocked_reason")
        if self.status != TaskStatus.BLOCKED and self.blocked_reason is not None:
            raise ValueError("only a blocked task can carry blocked_reason")
        if self.status == TaskStatus.COMPLETED and self.completed_at is None:
            raise ValueError("completed task requires completed_at")
        if self.status == TaskStatus.COMPLETED:
            if self.completed_at != self.updated_at:
                raise ValueError(
                    "completed_at must equal the completion transition time"
                )
        if self.status != TaskStatus.COMPLETED and self.completed_at is not None:
            raise ValueError("only a completed task can carry completed_at")
        return self


class Communication(MatterStatefulEntity):
    direction: Literal["inbound", "outbound", "internal"]
    channel: Literal["email", "mail", "service", "filing", "calendar", "other"]
    subject: ShortText
    participant_ids: tuple[DomainId, ...]
    work_product_version_id: DomainId | None = None
    destination_verified: bool = False
    destination_sha256: Sha256 | None = None
    validation_result_id: DomainId | None = None
    approval_id: DomainId | None = None
    execution_id: DomainId | None = None
    status: CommunicationStatus = CommunicationStatus.DRAFT

    TRANSITIONS: ClassVar = {
        CommunicationStatus.DRAFT: frozenset(
            {CommunicationStatus.VALIDATED, CommunicationStatus.CANCELLED}
        ),
        CommunicationStatus.VALIDATED: frozenset(
            {
                CommunicationStatus.DRAFT,
                CommunicationStatus.APPROVED,
                CommunicationStatus.CANCELLED,
            }
        ),
        CommunicationStatus.APPROVED: frozenset(
            {
                CommunicationStatus.DRAFT,
                CommunicationStatus.QUEUED,
                CommunicationStatus.CANCELLED,
            }
        ),
        CommunicationStatus.QUEUED: frozenset(
            {
                CommunicationStatus.DISPATCHED,
                CommunicationStatus.FAILED,
                CommunicationStatus.CANCELLED,
            }
        ),
        CommunicationStatus.DISPATCHED: frozenset(
            {CommunicationStatus.RECEIPT_VERIFIED, CommunicationStatus.FAILED}
        ),
        CommunicationStatus.RECEIPT_VERIFIED: frozenset(),
        CommunicationStatus.FAILED: frozenset({CommunicationStatus.QUEUED}),
        CommunicationStatus.CANCELLED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "destination_verified",
            "destination_sha256",
            "validation_result_id",
            "approval_id",
            "execution_id",
        }
    )
    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"destination_sha256", "execution_id"}
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"destination_sha256", "execution_id"}
    )

    _PAYLOAD_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "direction",
            "channel",
            "subject",
            "participant_ids",
            "work_product_version_id",
        }
    )
    _GATE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "validation_result_id",
            "approval_id",
            "destination_verified",
            "destination_sha256",
            "execution_id",
        }
    )

    @model_validator(mode="after")
    def validate_external_gates(self) -> Communication:
        if not self.participant_ids:
            raise ValueError("communication requires at least one participant")
        unique_ids(self.participant_ids, "participant_ids")
        progressed = {
            CommunicationStatus.VALIDATED,
            CommunicationStatus.APPROVED,
            CommunicationStatus.QUEUED,
            CommunicationStatus.DISPATCHED,
            CommunicationStatus.RECEIPT_VERIFIED,
            CommunicationStatus.FAILED,
        }
        if self.status in progressed and self.validation_result_id is None:
            raise ValueError("validated communication state requires validation")
        if self.status in progressed - {CommunicationStatus.VALIDATED}:
            if self.approval_id is None:
                raise ValueError("approved communication state requires approval")
        if self.status in {
            CommunicationStatus.QUEUED,
            CommunicationStatus.DISPATCHED,
            CommunicationStatus.RECEIPT_VERIFIED,
            CommunicationStatus.FAILED,
        }:
            if not self.destination_verified:
                raise ValueError("queued communication requires verified destination")
            if self.destination_sha256 is None:
                raise ValueError(
                    "queued communication requires exact destination binding"
                )
            if self.execution_id is None:
                raise ValueError("queued communication requires execution linkage")
        if self.status == CommunicationStatus.DRAFT:
            if (
                any(
                    value is not None
                    for value in (
                        self.validation_result_id,
                        self.approval_id,
                        self.destination_sha256,
                        self.execution_id,
                    )
                )
                or self.destination_verified
            ):
                raise ValueError(
                    "draft communication cannot carry future gate evidence"
                )
        if self.status == CommunicationStatus.VALIDATED:
            if (
                self.approval_id is not None
                or self.destination_sha256 is not None
                or self.execution_id is not None
            ):
                raise ValueError("validated communication cannot carry later gates")
            if self.destination_verified:
                raise ValueError("validated communication cannot be queued early")
        if self.status == CommunicationStatus.APPROVED:
            if (
                self.destination_sha256 is not None
                or self.execution_id is not None
                or self.destination_verified
            ):
                raise ValueError("approved communication cannot be queued early")
        return self

    def evolve(self, *, at: datetime, **changes: Any) -> Self:
        if self.status != CommunicationStatus.DRAFT:
            attempted = self._PAYLOAD_FIELDS.intersection(changes)
            if attempted:
                names = ", ".join(sorted(attempted))
                raise DomainTransitionError(
                    "reviewed communication payload requires a reset transition: "
                    f"{names}"
                )
        return super().evolve(at=at, **changes)

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        payload_changes = self._PAYLOAD_FIELDS.intersection(changes)
        if payload_changes and target != CommunicationStatus.DRAFT:
            names = ", ".join(sorted(payload_changes))
            raise DomainTransitionError(
                f"communication payload can change only on reset to draft: {names}"
            )
        if target == CommunicationStatus.DRAFT:
            uncleared = {
                name
                for name in self._GATE_FIELDS
                if changes.get(name, getattr(self, name)) not in {None, False}
            }
            if uncleared:
                names = ", ".join(sorted(uncleared))
                raise DomainTransitionError(
                    f"communication reset must clear gate evidence: {names}"
                )
        else:
            replaced = {
                name
                for name in self._GATE_FIELDS
                if name in changes
                and getattr(self, name) not in {None, False}
                and changes[name] != getattr(self, name)
            }
            if replaced:
                names = ", ".join(sorted(replaced))
                raise DomainTransitionError(
                    f"communication gate evidence cannot be replaced: {names}"
                )
        return super().transition_to(target, at=at, **changes)
