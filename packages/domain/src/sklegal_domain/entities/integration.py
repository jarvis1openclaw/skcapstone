"""Validation, approval, and execution integration entities."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    ClassVar,
    Self,
)

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from ..base import (
    MatterEntity,
    MatterStatefulEntity,
)
from ..exceptions import DomainTransitionError
from ..states import (
    ApprovalStatus,
    ExecutionStatus,
    ExecutionStep,
    ValidationOutcome,
)
from ..value_objects import (
    ArtifactBinding,
    DomainId,
    NonEmptyText,
    Sha256,
    ShortText,
    UtcDateTime,
    ValidationSubject,
    require_utc,
    unique_ids,
)


class ValidationResult(MatterEntity):
    subject: ArtifactBinding | ValidationSubject
    outcome: ValidationOutcome
    check_ids: Annotated[tuple[ShortText, ...], Field(min_length=1)]
    validator_principal_id: DomainId
    validated_at: UtcDateTime
    rationale: NonEmptyText

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "subject",
        "outcome",
        "check_ids",
        "validator_principal_id",
        "validated_at",
        "rationale",
    }

    @field_validator("check_ids")
    @classmethod
    def validate_check_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("check_ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_result_time(self) -> ValidationResult:
        if not self.created_at <= self.validated_at <= self.updated_at:
            raise ValueError("validated_at must fit the result audit interval")
        return self


class Approval(MatterStatefulEntity):
    subject: ArtifactBinding
    reviewer_principal_id: DomainId | None = None
    decided_at: UtcDateTime | None = None
    rationale: NonEmptyText | None = None
    revoker_principal_id: DomainId | None = None
    revocation_rationale: NonEmptyText | None = None
    revoked_at: UtcDateTime | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "subject",
            "reviewer_principal_id",
            "decided_at",
            "rationale",
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        }
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "reviewer_principal_id",
            "decided_at",
            "rationale",
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        }
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "reviewer_principal_id",
            "decided_at",
            "rationale",
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        }
    )

    TRANSITIONS: ClassVar = {
        ApprovalStatus.PENDING: frozenset(
            {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
        ),
        ApprovalStatus.APPROVED: frozenset({ApprovalStatus.REVOKED}),
        ApprovalStatus.REJECTED: frozenset(),
        ApprovalStatus.REVOKED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_decision(self) -> Approval:
        decision_values = (
            self.reviewer_principal_id,
            self.decided_at,
            self.rationale,
        )
        revocation_values = (
            self.revoker_principal_id,
            self.revocation_rationale,
            self.revoked_at,
        )
        if self.status == ApprovalStatus.PENDING:
            if any(
                value is not None for value in (*decision_values, *revocation_values)
            ):
                raise ValueError("pending approval cannot carry decision evidence")
        elif any(value is None for value in decision_values):
            raise ValueError("approval decision must be attributable and reasoned")
        if self.status == ApprovalStatus.REVOKED:
            if any(value is None for value in revocation_values):
                raise ValueError("revocation must be attributable and reasoned")
            if self.revoked_at != self.updated_at:
                raise ValueError("revoked_at must equal the revocation transition time")
        elif any(value is not None for value in revocation_values):
            raise ValueError("only a revoked approval can carry revocation evidence")
        if self.decided_at is not None:
            if not self.created_at <= self.decided_at <= self.updated_at:
                raise ValueError("decided_at must fit the approval audit interval")
        if self.revoked_at is not None and self.decided_at is not None:
            if self.revoked_at < self.decided_at:
                raise ValueError("revoked_at cannot precede decided_at")
        return self

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        if target in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            if changes.get("decided_at") != at:
                raise ValueError("decided_at must equal the decision transition time")
        if target == ApprovalStatus.REVOKED and changes.get("revoked_at") != at:
            raise ValueError("revoked_at must equal the revocation transition time")
        return super().transition_to(target, at=at, **changes)


class ExecutionEvent(MatterEntity):
    execution_id: DomainId
    step: ExecutionStep
    occurred_at: UtcDateTime
    correlation_id: ShortText
    actor_principal_id: DomainId
    receipt_id: DomainId | None = None

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "execution_id",
        "step",
        "occurred_at",
        "correlation_id",
        "actor_principal_id",
        "receipt_id",
    }

    @model_validator(mode="after")
    def validate_receipt_step(self) -> ExecutionEvent:
        if not self.created_at <= self.occurred_at <= self.updated_at:
            raise ValueError("execution event time must fit its audit interval")
        if self.step == ExecutionStep.RECEIPT_VERIFIED and self.receipt_id is None:
            raise ValueError("receipt-verified event requires receipt_id")
        if self.step != ExecutionStep.RECEIPT_VERIFIED and self.receipt_id is not None:
            raise ValueError("only a receipt-verified event can carry receipt_id")
        return self


class ExecutionReceipt(MatterEntity):
    execution_id: DomainId
    connector: ShortText
    external_receipt_id: ShortText
    artifact_content_sha256: Sha256
    destination_sha256: Sha256
    received_at: UtcDateTime
    verified_at: UtcDateTime

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "execution_id",
        "connector",
        "external_receipt_id",
        "artifact_content_sha256",
        "destination_sha256",
        "received_at",
        "verified_at",
    }

    @model_validator(mode="after")
    def validate_receipt_time(self) -> ExecutionReceipt:
        if self.received_at < self.created_at or self.verified_at > self.updated_at:
            raise ValueError("receipt times must fit its audit interval")
        if self.verified_at < self.received_at:
            raise ValueError("receipt verification cannot precede receipt")
        return self


class Execution(MatterStatefulEntity):
    subject: ArtifactBinding
    destination_sha256: Sha256
    idempotency_key: ShortText
    validation_result: ValidationResult | None = None
    approval: Approval | None = None
    events: tuple[ExecutionEvent, ...] = ()
    receipt: ExecutionReceipt | None = None
    status: ExecutionStatus = ExecutionStatus.DRAFT

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "subject",
            "destination_sha256",
            "idempotency_key",
            "validation_result",
            "approval",
            "receipt",
        }
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"validation_result", "approval", "receipt"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {"validation_result", "approval", "events", "receipt"}
    )

    TRANSITIONS: ClassVar = {
        ExecutionStatus.DRAFT: frozenset(
            {ExecutionStatus.VALIDATED, ExecutionStatus.CANCELLED}
        ),
        ExecutionStatus.VALIDATED: frozenset(
            {ExecutionStatus.APPROVED, ExecutionStatus.CANCELLED}
        ),
        ExecutionStatus.APPROVED: frozenset(
            {ExecutionStatus.QUEUED, ExecutionStatus.CANCELLED}
        ),
        ExecutionStatus.QUEUED: frozenset(
            {
                ExecutionStatus.DISPATCHED,
                ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED,
            }
        ),
        ExecutionStatus.DISPATCHED: frozenset(
            {ExecutionStatus.RECEIPT_VERIFIED, ExecutionStatus.FAILED}
        ),
        ExecutionStatus.RECEIPT_VERIFIED: frozenset(),
        ExecutionStatus.FAILED: frozenset({ExecutionStatus.QUEUED}),
        ExecutionStatus.CANCELLED: frozenset(),
    }

    @field_validator("events")
    @classmethod
    def validate_event_ids(
        cls, events: tuple[ExecutionEvent, ...]
    ) -> tuple[ExecutionEvent, ...]:
        event_ids = tuple(event.id for event in events)
        unique_ids(event_ids, "execution event ids")
        return events

    @model_validator(mode="after")
    def validate_execution_gates(self) -> Execution:
        for event in self.events:
            if event.tenant_id != self.tenant_id or event.matter_id != self.matter_id:
                raise ValueError("execution event crosses tenant or matter boundary")
            if event.execution_id != self.id:
                raise ValueError("execution event belongs to another execution")
            if event.updated_at > self.updated_at:
                raise ValueError("execution event audit time exceeds its execution")
        if self.receipt is not None:
            if self.receipt.tenant_id != self.tenant_id:
                raise ValueError("execution receipt crosses tenant boundary")
            if self.receipt.matter_id != self.matter_id:
                raise ValueError("execution receipt crosses matter boundary")
            if self.receipt.execution_id != self.id:
                raise ValueError("execution receipt belongs to another execution")
            if self.receipt.updated_at > self.updated_at:
                raise ValueError("execution receipt audit time exceeds its execution")
            if self.receipt.artifact_content_sha256 != self.subject.content_sha256:
                raise ValueError(
                    "execution receipt artifact hash does not match approval"
                )
            if self.receipt.destination_sha256 != self.destination_sha256:
                raise ValueError(
                    "execution receipt destination does not match execution"
                )

        progressed = {
            ExecutionStatus.VALIDATED,
            ExecutionStatus.APPROVED,
            ExecutionStatus.QUEUED,
            ExecutionStatus.DISPATCHED,
            ExecutionStatus.RECEIPT_VERIFIED,
            ExecutionStatus.FAILED,
        }
        if self.status == ExecutionStatus.DRAFT:
            if self.validation_result is not None or self.approval is not None:
                raise ValueError("draft execution cannot carry future gate evidence")
        if self.status in progressed and self.validation_result is None:
            raise ValueError("validated execution state requires validation")
        if self.status == ExecutionStatus.VALIDATED and self.approval is not None:
            raise ValueError("validated execution cannot carry approval early")
        if self.status in progressed - {ExecutionStatus.VALIDATED}:
            if self.approval is None:
                raise ValueError("approved execution state requires approval")

        if self.validation_result is not None:
            if (
                self.validation_result.tenant_id != self.tenant_id
                or self.validation_result.matter_id != self.matter_id
            ):
                raise ValueError(
                    "execution validation crosses tenant or matter boundary"
                )
            if self.validation_result.subject != self.subject:
                raise ValueError(
                    "execution validation does not bind the exact artifact"
                )
            if self.validation_result.outcome != ValidationOutcome.PASSED:
                raise ValueError("execution requires a passed validation result")
            if self.validation_result.updated_at > self.updated_at:
                raise ValueError(
                    "execution validation audit time exceeds its execution"
                )
        if self.approval is not None:
            if (
                self.approval.tenant_id != self.tenant_id
                or self.approval.matter_id != self.matter_id
            ):
                raise ValueError("execution approval crosses tenant or matter boundary")
            if self.approval.subject != self.subject:
                raise ValueError("execution approval does not bind the exact artifact")
            if self.approval.status != ApprovalStatus.APPROVED:
                raise ValueError("execution requires a currently approved decision")
            if self.approval.decided_at is None:
                raise ValueError("execution approval lacks decision evidence")
            if self.approval.updated_at > self.updated_at:
                raise ValueError("execution approval cannot be future-dated")

        step_status = {
            ExecutionStep.VALIDATED: ExecutionStatus.VALIDATED,
            ExecutionStep.APPROVED: ExecutionStatus.APPROVED,
            ExecutionStep.QUEUED: ExecutionStatus.QUEUED,
            ExecutionStep.DISPATCHED: ExecutionStatus.DISPATCHED,
            ExecutionStep.RECEIPT_VERIFIED: ExecutionStatus.RECEIPT_VERIFIED,
            ExecutionStep.FAILED: ExecutionStatus.FAILED,
            ExecutionStep.CANCELLED: ExecutionStatus.CANCELLED,
        }
        simulated_status = ExecutionStatus.DRAFT
        last_event_at = self.created_at
        for event in self.events:
            if event.occurred_at < last_event_at or event.occurred_at > self.updated_at:
                raise ValueError(
                    "execution events must be ordered within aggregate time"
                )
            event_status = step_status[event.step]
            if event_status not in self.TRANSITIONS[simulated_status]:
                raise ValueError("execution events do not follow the state graph")
            simulated_status = event_status
            last_event_at = event.occurred_at
        if simulated_status != self.status:
            raise ValueError("execution events do not prove the current state")
        if self.status != ExecutionStatus.RECEIPT_VERIFIED and self.receipt is not None:
            raise ValueError("execution receipt cannot precede receipt verification")
        if self.status == ExecutionStatus.RECEIPT_VERIFIED:
            if self.receipt is None:
                raise ValueError("receipt-verified execution requires a receipt")
            receipt_events = [
                event
                for event in self.events
                if event.step == ExecutionStep.RECEIPT_VERIFIED
                and event.receipt_id == self.receipt.id
            ]
            if not receipt_events:
                raise ValueError("receipt-verified execution requires matching event")
            dispatch_events = [
                event for event in self.events if event.step == ExecutionStep.DISPATCHED
            ]
            if not dispatch_events:
                raise ValueError("receipt-verified execution requires dispatch event")
            if self.receipt.received_at < dispatch_events[-1].occurred_at:
                raise ValueError("execution receipt cannot precede dispatch")
            if receipt_events[-1].occurred_at < self.receipt.verified_at:
                raise ValueError("receipt event cannot precede receipt verification")
        return self

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        checked_at = require_utc(at)
        supplied_events = changes.get("events")
        if not isinstance(supplied_events, tuple):
            raise DomainTransitionError(
                "execution transition requires an immutable event tuple"
            )
        if len(supplied_events) != len(self.events) + 1:
            raise DomainTransitionError(
                "execution transition must append exactly one event"
            )
        if supplied_events[:-1] != self.events:
            raise DomainTransitionError(
                "execution event history must preserve the exact prior prefix"
            )
        step_by_status: dict[StrEnum, ExecutionStep] = {
            ExecutionStatus.VALIDATED: ExecutionStep.VALIDATED,
            ExecutionStatus.APPROVED: ExecutionStep.APPROVED,
            ExecutionStatus.QUEUED: ExecutionStep.QUEUED,
            ExecutionStatus.DISPATCHED: ExecutionStep.DISPATCHED,
            ExecutionStatus.RECEIPT_VERIFIED: ExecutionStep.RECEIPT_VERIFIED,
            ExecutionStatus.FAILED: ExecutionStep.FAILED,
            ExecutionStatus.CANCELLED: ExecutionStep.CANCELLED,
        }
        expected_step = step_by_status.get(target)
        if expected_step is None or supplied_events[-1].step != expected_step:
            raise DomainTransitionError(
                "execution transition event does not match the target state"
            )
        if supplied_events[-1].occurred_at != checked_at:
            raise DomainTransitionError(
                "execution transition event must occur at the transition boundary"
            )
        return super().transition_to(target, at=checked_at, **changes)
