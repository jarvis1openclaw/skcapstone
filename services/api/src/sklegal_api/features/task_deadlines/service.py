"""Fail-closed Task, Deadline, and simulation application service."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol, Self, cast
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sklegal_persistence.features.task_deadlines.models import (
    DeadlineRecord,
    DeadlineState,
    ProvenanceReference,
    ReminderRecord,
    RuleAuthorityEvidence,
    Sha256,
    SimulationReceipt,
    TaskDeadlineAuditEvent,
    TaskDeadlineOutboxRecord,
    TaskRecord,
    TaskState,
    TriggerEvidence,
    WorkflowOutcome,
    canonical_sha256,
)
from sklegal_persistence.features.task_deadlines.repository import (
    ResourceRecord,
    TaskDeadlineIdempotencyConflict,
    TaskDeadlineRepository,
    TaskDeadlineRepositoryUnavailable,
    TaskDeadlineVersionConflict,
)

from .contracts import (
    ComputeDeadlineCommand,
    DeadlineListRead,
    DeadlineMutationReceipt,
    DeadlineReviewCommand,
    DeadlineTransitionCommand,
    MutationEvidence,
    SimulationHandoffCommand,
    SimulationMutationReceipt,
    TaskListRead,
    TaskMutationReceipt,
    TaskTransitionCommand,
    UpsertTaskCommand,
)

Operation = Literal[
    "task.create",
    "task.read",
    "task.list",
    "task.transition",
    "deadline.compute",
    "deadline.read",
    "deadline.list",
    "deadline.review",
    "deadline.transition",
    "external_action.simulation",
]
Capability = Literal["matter.read", "matter.manage", "action.email.prepare"]
Purpose = Literal["matter_management", "external_action_preparation"]


class TaskDeadlineServiceError(RuntimeError):
    """Sanitized service error from the frozen closed vocabulary."""

    def __init__(
        self,
        code: Literal[
            "authentication_required",
            "access_denied",
            "resource_unavailable",
            "validation_failed",
            "precondition_failed",
            "idempotency_conflict",
            "version_conflict",
            "policy_unavailable",
            "dependency_unavailable",
            "internal_error",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class TaskDeadlineAccessContext(BaseModel):
    """Trusted route-local scope from CapAuth, never from request content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    capability: Capability
    purpose: Purpose
    authorization_decision_id: UUID
    credential_expires_at: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def validate_expiry(self) -> Self:
        if (
            self.credential_expires_at.tzinfo is None
            or self.credential_expires_at.utcoffset() is None
        ):
            raise ValueError("credential expiry must be timezone aware")
        return self


class TaskDeadlinePolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    operation: Operation
    capability: Capability
    purpose: Purpose
    allowed: bool
    revision: Sha256
    evaluated_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        aware = all(
            value.tzinfo is not None and value.utcoffset() is not None
            for value in (self.evaluated_at, self.valid_until)
        )
        if not aware or self.valid_until <= self.evaluated_at:
            raise ValueError("policy validity window is invalid")
        return self


class TaskDeadlinePolicyUnavailable(RuntimeError):
    """No current policy decision can be produced."""


class TaskDeadlinePolicy(Protocol):
    def authorize(
        self,
        *,
        context: TaskDeadlineAccessContext,
        operation: Operation,
        capability: Capability,
        purpose: Purpose,
        now: datetime,
    ) -> TaskDeadlinePolicyDecision: ...


class SimulationGateVerifier(Protocol):
    """Verify the complete operative Approval against the frozen V2 contract.

    A true result requires approved status, exact current Work Product subject,
    passed validation, allowed capability evidence, complete reviewer decision
    evidence, current policy, and no revocation or supersession.
    """

    def verify(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        task_id: UUID,
        deadline_id: UUID | None,
        work_product_id: UUID,
        work_product_version_id: UUID,
        work_product_version_number: int,
        work_product_content_sha256: str,
        approval_id: UUID,
        approval_snapshot_sha256: str,
        action_kind: Literal["email"],
        destination_sha256: str,
    ) -> bool: ...


class StaticTaskDeadlinePolicy:
    """Deterministic public-synthetic policy for isolated tests only."""

    def __init__(
        self,
        *,
        grants: set[tuple[UUID, UUID, UUID, Capability, Purpose]],
        revision: str,
        valid_until: datetime,
    ) -> None:
        self.grants = grants
        self.revision = revision
        self.valid_until = valid_until
        self.available = True

    def authorize(
        self,
        *,
        context: TaskDeadlineAccessContext,
        operation: Operation,
        capability: Capability,
        purpose: Purpose,
        now: datetime,
    ) -> TaskDeadlinePolicyDecision:
        if not self.available:
            raise TaskDeadlinePolicyUnavailable("policy unavailable")
        allowed = (
            context.tenant_id,
            context.matter_id,
            context.principal_id,
            capability,
            purpose,
        ) in self.grants
        return TaskDeadlinePolicyDecision(
            decision_id=uuid5(
                NAMESPACE_URL,
                f"task-deadline-policy:{self.revision}:{context.tenant_id}:"
                f"{context.matter_id}:{context.principal_id}:{operation}",
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            capability=capability,
            purpose=purpose,
            allowed=allowed,
            revision=self.revision,
            evaluated_at=now,
            valid_until=self.valid_until,
        )


class StaticSimulationGateVerifier:
    """Exact allowlist verifier for public-synthetic Work Product evidence."""

    def __init__(
        self,
        allowed: set[
            tuple[
                UUID,
                UUID,
                UUID,
                UUID | None,
                UUID,
                UUID,
                int,
                str,
                UUID,
                str,
                Literal["email"],
                str,
            ]
        ],
        *,
        available: bool = True,
    ) -> None:
        self.allowed = allowed
        self.available = available

    def verify(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        task_id: UUID,
        deadline_id: UUID | None,
        work_product_id: UUID,
        work_product_version_id: UUID,
        work_product_version_number: int,
        work_product_content_sha256: str,
        approval_id: UUID,
        approval_snapshot_sha256: str,
        action_kind: Literal["email"],
        destination_sha256: str,
    ) -> bool:
        if not self.available:
            raise TaskDeadlineRepositoryUnavailable("Approval verifier unavailable")
        return (
            tenant_id,
            matter_id,
            task_id,
            deadline_id,
            work_product_id,
            work_product_version_id,
            work_product_version_number,
            work_product_content_sha256,
            approval_id,
            approval_snapshot_sha256,
            action_kind,
            destination_sha256,
        ) in self.allowed


def _key_sha256(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _request_sha256(
    operation: Operation, resource_id: UUID | None, command: BaseModel
) -> str:
    return canonical_sha256(
        {
            "operation": operation,
            "resource_id": str(resource_id) if resource_id else None,
            "command": command.model_dump(mode="json", by_alias=True),
        }
    )


def _uuid(label: str, *parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, ":".join((label, *(str(part) for part in parts))))


def _due_time(
    trigger: TriggerEvidence,
    rule: RuleAuthorityEvidence,
    holidays: frozenset[date],
    zone: ZoneInfo,
) -> datetime:
    if trigger.occurred_at is None:
        raise ValueError("confirmed trigger has no occurrence time")
    local = trigger.occurred_at.astimezone(zone)
    remaining = rule.interval_days
    if rule.include_trigger_day:
        trigger_is_countable = rule.convention == "calendar_days" or (
            local.weekday() < 5 and local.date() not in holidays
        )
        if trigger_is_countable:
            remaining = max(0, remaining - 1)
    current = local
    while remaining:
        current += timedelta(days=1)
        if rule.convention == "business_days" and (
            current.weekday() >= 5 or current.date() in holidays
        ):
            continue
        remaining -= 1
    return current.astimezone(UTC)


def _workflow_outcome(state: TaskState | DeadlineState) -> WorkflowOutcome:
    if state in {
        "cancelled",
        "failed",
        "retry_pending",
        "reconciliation_required",
        "reconciled",
    }:
        return state
    return "succeeded"


class TaskDeadlineService:
    """Authorize and atomically append durable feature records."""

    def __init__(
        self,
        *,
        repository: TaskDeadlineRepository,
        policy: TaskDeadlinePolicy,
        simulation_gate: SimulationGateVerifier,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._simulation_gate = simulation_gate
        self._clock = clock

    def _authorize(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        operation: Operation,
        capability: Capability,
        purpose: Purpose,
    ) -> TaskDeadlinePolicyDecision:
        now = self._clock()
        if (
            context.revoked
            or context.credential_expires_at <= now
            or context.matter_id != matter_id
            or context.capability != capability
            or context.purpose != purpose
        ):
            raise TaskDeadlineServiceError("access_denied")
        try:
            member = self._repository.is_matter_member(
                context.tenant_id, matter_id, context.principal_id
            )
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        if not member:
            raise TaskDeadlineServiceError("access_denied")
        try:
            decision = self._policy.authorize(
                context=context,
                operation=operation,
                capability=capability,
                purpose=purpose,
                now=now,
            )
        except (TaskDeadlinePolicyUnavailable, ValidationError):
            raise TaskDeadlineServiceError("policy_unavailable") from None
        if decision.valid_until <= now or decision.evaluated_at > now:
            raise TaskDeadlineServiceError("policy_unavailable")
        if (
            not decision.allowed
            or decision.tenant_id != context.tenant_id
            or decision.matter_id != matter_id
            or decision.principal_id != context.principal_id
            or decision.operation != operation
            or decision.capability != capability
            or decision.purpose != purpose
        ):
            raise TaskDeadlineServiceError("access_denied")
        return decision

    def _existing(
        self,
        *,
        context: TaskDeadlineAccessContext,
        operation: Operation,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_type: type[TaskRecord]
        | type[DeadlineRecord]
        | type[SimulationReceipt],
    ) -> ResourceRecord | None:
        try:
            existing = self._repository.find_idempotent(
                context.tenant_id,
                context.matter_id,
                operation,
                idempotency_key_sha256,
            )
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        if existing is None:
            return None
        if existing[0] != request_sha256:
            raise TaskDeadlineServiceError("idempotency_conflict")
        if not isinstance(existing[1], expected_type):
            raise TaskDeadlineServiceError("idempotency_conflict")
        return existing[1]

    @staticmethod
    def _evidence(
        *,
        key_sha256: str,
        request_sha256: str,
        resource_version: int,
        audit_id: UUID,
        outbox_id: UUID,
        correlation_id: UUID,
    ) -> dict[str, object]:
        return MutationEvidence(
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            resource_version=resource_version,
            audit_id=audit_id,
            outbox_id=outbox_id,
            correlation_id=correlation_id,
        ).model_dump()

    def _write_evidence(
        self,
        *,
        context: TaskDeadlineAccessContext,
        decision: TaskDeadlinePolicyDecision,
        operation: Operation,
        resource_kind: Literal["task", "deadline", "action_simulation"],
        resource_id: UUID,
        resource_version: int,
        request_sha256: str,
        resource_sha256: str,
        now: datetime,
        outcome: WorkflowOutcome = "succeeded",
    ) -> tuple[TaskDeadlineAuditEvent, TaskDeadlineOutboxRecord, UUID]:
        correlation_id = _uuid("task-deadline-correlation", operation, request_sha256)
        audit_id = _uuid("task-deadline-audit", operation, request_sha256)
        outbox_id = _uuid("task-deadline-outbox", operation, request_sha256)
        topic = cast(
            Literal[
                "task.projection",
                "deadline.projection",
                "external_action.simulation",
            ],
            {
                "task": "task.projection",
                "deadline": "deadline.projection",
                "action_simulation": "external_action.simulation",
            }[resource_kind],
        )
        audit = TaskDeadlineAuditEvent(
            audit_id=audit_id,
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            resource_version=resource_version,
            action=operation,
            outcome=outcome,
            actor_principal_id=context.principal_id,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            correlation_id=correlation_id,
            request_sha256=request_sha256,
            resource_sha256=resource_sha256,
            occurred_at=now,
        )
        outbox = TaskDeadlineOutboxRecord(
            outbox_id=outbox_id,
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            audit_id=audit_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            topic=topic,
            payload_sha256=resource_sha256,
            dispatch_allowed=False,
            created_at=now,
        )
        return audit, outbox, correlation_id

    def _commit_task(
        self,
        *,
        context: TaskDeadlineAccessContext,
        decision: TaskDeadlinePolicyDecision,
        operation: Operation,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        draft: dict[str, object],
    ) -> TaskMutationReceipt:
        now = self._clock()
        audit_id = _uuid("task-deadline-audit", operation, request_sha256)
        outbox_id = _uuid("task-deadline-outbox", operation, request_sha256)
        record = TaskRecord.model_validate(
            {**draft, "audit_id": audit_id, "outbox_id": outbox_id}
        )
        audit, outbox, correlation = self._write_evidence(
            context=context,
            decision=decision,
            operation=operation,
            resource_kind="task",
            resource_id=record.task_id,
            resource_version=record.version,
            request_sha256=request_sha256,
            resource_sha256=canonical_sha256(record),
            now=now,
            outcome=_workflow_outcome(record.status),
        )
        try:
            saved = self._repository.commit_task(
                operation=operation,
                idempotency_key_sha256=idempotency_key_sha256,
                request_sha256=request_sha256,
                expected_version=expected_version,
                record=record,
                audit=audit,
                outbox=outbox,
            )
        except TaskDeadlineIdempotencyConflict:
            raise TaskDeadlineServiceError("idempotency_conflict") from None
        except TaskDeadlineVersionConflict:
            raise TaskDeadlineServiceError("version_conflict") from None
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        return TaskMutationReceipt.model_validate(
            {
                "task": saved,
                **self._evidence(
                    key_sha256=idempotency_key_sha256,
                    request_sha256=request_sha256,
                    resource_version=saved.version,
                    audit_id=saved.audit_id,
                    outbox_id=saved.outbox_id,
                    correlation_id=correlation,
                ),
            }
        )

    def create_task(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: UpsertTaskCommand,
    ) -> TaskMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="task.create",
            capability="matter.manage",
            purpose="matter_management",
        )
        request_sha256 = _request_sha256("task.create", None, command)
        key_sha256 = _key_sha256(idempotency_key)
        existing = self._existing(
            context=context,
            operation="task.create",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_type=TaskRecord,
        )
        correlation = _uuid("task-deadline-correlation", "task.create", request_sha256)
        if isinstance(existing, TaskRecord):
            return TaskMutationReceipt.model_validate(
                {
                    "task": existing,
                    **self._evidence(
                        key_sha256=key_sha256,
                        request_sha256=request_sha256,
                        resource_version=existing.version,
                        audit_id=existing.audit_id,
                        outbox_id=existing.outbox_id,
                        correlation_id=correlation,
                    ),
                }
            )
        if command.deadline_id is not None:
            linked = self._get_deadline_record(context, matter_id, command.deadline_id)
            if (
                linked.state != "operative"
                or linked.review_state != "accepted"
                or linked.operative_due_at != command.due_at
            ):
                raise TaskDeadlineServiceError("precondition_failed")
        now = self._clock()
        task_id = _uuid(
            "task", context.tenant_id, matter_id, context.principal_id, key_sha256
        )
        source_kind: Literal["accepted_proposal", "human_command"] = (
            "accepted_proposal" if command.proposal_id else "human_command"
        )
        source_id = command.proposal_id or correlation
        provenance = ProvenanceReference(
            provenance_id=_uuid("task-provenance", request_sha256),
            source_kind=source_kind,
            source_id=source_id,
            source_version=1,
            source_sha256=request_sha256,
            recorded_at=now,
        )
        return self._commit_task(
            context=context,
            decision=decision,
            operation="task.create",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_version=0,
            draft={
                "tenant_id": context.tenant_id,
                "matter_id": matter_id,
                "task_id": task_id,
                "version": 1,
                "title": command.title,
                "description": command.description,
                "status": "ready" if command.assigned_principal_id else "draft",
                "assigned_principal_id": command.assigned_principal_id,
                "deadline_id": command.deadline_id,
                "due_at": command.due_at,
                "proposal_id": command.proposal_id,
                "decision_id": command.decision_id,
                "policy_decision_id": decision.decision_id,
                "policy_revision": decision.revision,
                "created_by_principal_id": context.principal_id,
                "updated_by_principal_id": context.principal_id,
                "created_at": now,
                "updated_at": now,
                "provenance": (provenance,),
            },
        )

    def _task_transition_state(
        self, current: TaskRecord, command: TaskTransitionCommand
    ) -> dict[str, object]:
        allowed: dict[str, set[str]] = {
            "draft": {"ready", "cancel", "fail"},
            "ready": {"start", "block", "cancel", "fail"},
            "in_progress": {"block", "complete", "cancel", "fail"},
            "blocked": {"ready", "start", "cancel", "fail", "require_reconciliation"},
            "failed": {"retry", "require_reconciliation", "cancel"},
            "retry_pending": {"ready", "start", "fail", "require_reconciliation"},
            "reconciliation_required": {"reconcile", "cancel"},
            "reconciled": {"ready", "start", "cancel"},
            "completed": set(),
            "cancelled": set(),
        }
        if command.transition not in allowed[current.status]:
            raise TaskDeadlineServiceError("precondition_failed")
        state_by_transition: dict[str, TaskState] = {
            "ready": "ready",
            "start": "in_progress",
            "block": "blocked",
            "complete": "completed",
            "cancel": "cancelled",
            "fail": "failed",
            "retry": "retry_pending",
            "require_reconciliation": "reconciliation_required",
            "reconcile": "reconciled",
        }
        now = self._clock()
        state = state_by_transition[command.transition]
        return {
            "status": state,
            "assigned_principal_id": command.assigned_principal_id
            or current.assigned_principal_id,
            "blocked_reason": command.reason if state == "blocked" else None,
            "completed_at": now if state == "completed" else None,
            "cancelled_at": now if state == "cancelled" else None,
            "cancellation_reason": command.reason if state == "cancelled" else None,
            "failure_code": command.failure_code
            if state in {"failed", "retry_pending"}
            else None,
            "retry_of_version": current.version if state == "retry_pending" else None,
            "reconciliation_of_version": (
                current.reconciliation_of_version or current.version
                if state in {"reconciliation_required", "reconciled"}
                else None
            ),
        }

    def transition_task(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        task_id: UUID,
        idempotency_key: str,
        command: TaskTransitionCommand,
    ) -> TaskMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="task.transition",
            capability="matter.manage",
            purpose="matter_management",
        )
        request_sha256 = _request_sha256("task.transition", task_id, command)
        key_sha256 = _key_sha256(idempotency_key)
        existing = self._existing(
            context=context,
            operation="task.transition",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_type=TaskRecord,
        )
        correlation = _uuid(
            "task-deadline-correlation", "task.transition", request_sha256
        )
        if isinstance(existing, TaskRecord):
            return TaskMutationReceipt.model_validate(
                {
                    "task": existing,
                    **self._evidence(
                        key_sha256=key_sha256,
                        request_sha256=request_sha256,
                        resource_version=existing.version,
                        audit_id=existing.audit_id,
                        outbox_id=existing.outbox_id,
                        correlation_id=correlation,
                    ),
                }
            )
        current = self._get_task_record(context, matter_id, task_id)
        if current.version != command.expected_version:
            raise TaskDeadlineServiceError("version_conflict")
        changes = self._task_transition_state(current, command)
        now = self._clock()
        provenance = ProvenanceReference(
            provenance_id=_uuid("task-transition-provenance", request_sha256),
            source_kind="lifecycle_transition",
            source_id=correlation,
            source_version=current.version + 1,
            source_sha256=request_sha256,
            recorded_at=now,
        )
        draft = current.model_dump()
        draft.update(changes)
        draft.update(
            version=current.version + 1,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            updated_by_principal_id=context.principal_id,
            updated_at=now,
            provenance=(*current.provenance, provenance),
        )
        draft.pop("audit_id")
        draft.pop("outbox_id")
        return self._commit_task(
            context=context,
            decision=decision,
            operation="task.transition",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_version=current.version,
            draft=draft,
        )

    def _deadline_uncertainty(
        self, command: ComputeDeadlineCommand, now: datetime
    ) -> tuple[str, ...]:
        codes: list[str] = []
        if command.trigger.state != "confirmed":
            codes.append(f"trigger_{command.trigger.state}")
        if command.rule.state != "current":
            codes.append(f"rule_{command.rule.state}")
        elif command.rule.verified_at is not None and command.rule.verified_at > now:
            raise TaskDeadlineServiceError("validation_failed")
        elif command.rule.valid_until is None or command.rule.valid_until <= now:
            codes.append("authority_stale")
        if (
            command.rule.convention == "business_days"
            and command.calendar.state != "current"
        ):
            codes.append(f"holiday_calendar_{command.calendar.state}")
        try:
            ZoneInfo(command.calendar.time_zone)
        except ZoneInfoNotFoundError:
            codes.append("time_zone_unknown")
        return tuple(sorted(set(codes)))

    def _commit_deadline(
        self,
        *,
        context: TaskDeadlineAccessContext,
        decision: TaskDeadlinePolicyDecision,
        operation: Operation,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        draft: dict[str, object],
    ) -> DeadlineMutationReceipt:
        now = self._clock()
        audit_id = _uuid("task-deadline-audit", operation, request_sha256)
        outbox_id = _uuid("task-deadline-outbox", operation, request_sha256)
        record = DeadlineRecord.model_validate(
            {**draft, "audit_id": audit_id, "outbox_id": outbox_id}
        )
        audit, outbox, correlation = self._write_evidence(
            context=context,
            decision=decision,
            operation=operation,
            resource_kind="deadline",
            resource_id=record.deadline_id,
            resource_version=record.version,
            request_sha256=request_sha256,
            resource_sha256=canonical_sha256(record),
            now=now,
            outcome=_workflow_outcome(record.state),
        )
        try:
            saved = self._repository.commit_deadline(
                operation=operation,
                idempotency_key_sha256=idempotency_key_sha256,
                request_sha256=request_sha256,
                expected_version=expected_version,
                record=record,
                audit=audit,
                outbox=outbox,
            )
        except TaskDeadlineIdempotencyConflict:
            raise TaskDeadlineServiceError("idempotency_conflict") from None
        except TaskDeadlineVersionConflict:
            raise TaskDeadlineServiceError("version_conflict") from None
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        return DeadlineMutationReceipt.model_validate(
            {
                "deadline": saved,
                **self._evidence(
                    key_sha256=idempotency_key_sha256,
                    request_sha256=request_sha256,
                    resource_version=saved.version,
                    audit_id=saved.audit_id,
                    outbox_id=saved.outbox_id,
                    correlation_id=correlation,
                ),
            }
        )

    def compute_deadline(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: ComputeDeadlineCommand,
    ) -> DeadlineMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="deadline.compute",
            capability="matter.manage",
            purpose="matter_management",
        )
        request_sha256 = _request_sha256("deadline.compute", None, command)
        key_sha256 = _key_sha256(idempotency_key)
        existing = self._existing(
            context=context,
            operation="deadline.compute",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_type=DeadlineRecord,
        )
        correlation = _uuid(
            "task-deadline-correlation", "deadline.compute", request_sha256
        )
        if isinstance(existing, DeadlineRecord):
            return DeadlineMutationReceipt.model_validate(
                {
                    "deadline": existing,
                    **self._evidence(
                        key_sha256=key_sha256,
                        request_sha256=request_sha256,
                        resource_version=existing.version,
                        audit_id=existing.audit_id,
                        outbox_id=existing.outbox_id,
                        correlation_id=correlation,
                    ),
                }
            )
        superseded: DeadlineRecord | None = None
        if command.supersedes_deadline_id is not None:
            superseded = self._get_deadline_record(
                context, matter_id, command.supersedes_deadline_id
            )
            if superseded.state != "operative" or superseded.review_state != "accepted":
                raise TaskDeadlineServiceError("precondition_failed")
        now = self._clock()
        uncertainty = self._deadline_uncertainty(command, now)
        due_at: datetime | None = None
        reminders: tuple[ReminderRecord, ...] = ()
        state: DeadlineState
        if uncertainty:
            state = (
                "blocked"
                if any(
                    "missing" in code or code == "time_zone_unknown"
                    for code in uncertainty
                )
                else "uncertain"
            )
        else:
            zone = ZoneInfo(command.calendar.time_zone)
            due_at = _due_time(
                command.trigger,
                command.rule,
                frozenset(command.calendar.holidays),
                zone,
            )
            state = "calculated"
            reminders = tuple(
                ReminderRecord(
                    reminder_id=_uuid("deadline-reminder", request_sha256, offset),
                    scheduled_for=due_at - timedelta(days=offset),
                    offset_days=offset,
                )
                for offset in command.reminder_offsets_days
            )
        deadline_id = _uuid(
            "deadline", context.tenant_id, matter_id, context.principal_id, key_sha256
        )
        calculation_sha256 = canonical_sha256(
            {
                "trigger": command.trigger,
                "rule": command.rule,
                "calendar": command.calendar,
                "candidate_due_at": due_at,
                "uncertainty_codes": uncertainty,
            }
        )
        provenance = ProvenanceReference(
            provenance_id=_uuid("deadline-provenance", request_sha256),
            source_kind="deterministic_calculation",
            source_id=correlation,
            source_version=1,
            source_sha256=calculation_sha256,
            recorded_at=now,
        )
        return self._commit_deadline(
            context=context,
            decision=decision,
            operation="deadline.compute",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_version=0,
            draft={
                "tenant_id": context.tenant_id,
                "matter_id": matter_id,
                "deadline_id": deadline_id,
                "version": 1,
                "title": command.title,
                "state": state,
                "trigger": command.trigger,
                "rule": command.rule,
                "calendar": command.calendar,
                "candidate_due_at": due_at,
                "calculation_sha256": calculation_sha256,
                "uncertainty_codes": uncertainty,
                "reminders": reminders,
                "supersedes_deadline_id": command.supersedes_deadline_id,
                "policy_decision_id": decision.decision_id,
                "policy_revision": decision.revision,
                "created_by_principal_id": context.principal_id,
                "updated_by_principal_id": context.principal_id,
                "created_at": now,
                "updated_at": now,
                "provenance": (provenance,),
            },
        )

    def review_deadline(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        deadline_id: UUID,
        idempotency_key: str,
        command: DeadlineReviewCommand,
    ) -> DeadlineMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="deadline.review",
            capability="matter.manage",
            purpose="matter_management",
        )
        request_sha256 = _request_sha256("deadline.review", deadline_id, command)
        key_sha256 = _key_sha256(idempotency_key)
        existing = self._existing(
            context=context,
            operation="deadline.review",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_type=DeadlineRecord,
        )
        correlation = _uuid(
            "task-deadline-correlation", "deadline.review", request_sha256
        )
        if isinstance(existing, DeadlineRecord):
            return DeadlineMutationReceipt.model_validate(
                {
                    "deadline": existing,
                    **self._evidence(
                        key_sha256=key_sha256,
                        request_sha256=request_sha256,
                        resource_version=existing.version,
                        audit_id=existing.audit_id,
                        outbox_id=existing.outbox_id,
                        correlation_id=correlation,
                    ),
                }
            )
        current = self._get_deadline_record(context, matter_id, deadline_id)
        if current.version != command.expected_version:
            raise TaskDeadlineServiceError("version_conflict")
        if current.state != "calculated" or current.candidate_due_at is None:
            raise TaskDeadlineServiceError("precondition_failed")
        if (
            command.decision == "accepted"
            and current.supersedes_deadline_id is not None
        ):
            superseded = self._get_deadline_record(
                context, matter_id, current.supersedes_deadline_id
            )
            if superseded.state not in {"cancelled", "superseded"}:
                raise TaskDeadlineServiceError("precondition_failed")
        now = self._clock()
        provenance = ProvenanceReference(
            provenance_id=_uuid("deadline-review-provenance", request_sha256),
            source_kind="human_review",
            source_id=correlation,
            source_version=current.version + 1,
            source_sha256=request_sha256,
            recorded_at=now,
        )
        draft = current.model_dump()
        draft.update(
            version=current.version + 1,
            state="operative" if command.decision == "accepted" else "reviewed",
            review_state=command.decision,
            operative_due_at=(
                current.candidate_due_at if command.decision == "accepted" else None
            ),
            reviewed_by_principal_id=context.principal_id,
            reviewed_at=now,
            review_rationale=command.rationale,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            updated_by_principal_id=context.principal_id,
            updated_at=now,
            provenance=(*current.provenance, provenance),
        )
        draft.pop("audit_id")
        draft.pop("outbox_id")
        return self._commit_deadline(
            context=context,
            decision=decision,
            operation="deadline.review",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_version=current.version,
            draft=draft,
        )

    def transition_deadline(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        deadline_id: UUID,
        idempotency_key: str,
        command: DeadlineTransitionCommand,
    ) -> DeadlineMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="deadline.transition",
            capability="matter.manage",
            purpose="matter_management",
        )
        request_sha256 = _request_sha256("deadline.transition", deadline_id, command)
        key_sha256 = _key_sha256(idempotency_key)
        existing = self._existing(
            context=context,
            operation="deadline.transition",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_type=DeadlineRecord,
        )
        correlation = _uuid(
            "task-deadline-correlation", "deadline.transition", request_sha256
        )
        if isinstance(existing, DeadlineRecord):
            return DeadlineMutationReceipt.model_validate(
                {
                    "deadline": existing,
                    **self._evidence(
                        key_sha256=key_sha256,
                        request_sha256=request_sha256,
                        resource_version=existing.version,
                        audit_id=existing.audit_id,
                        outbox_id=existing.outbox_id,
                        correlation_id=correlation,
                    ),
                }
            )
        current = self._get_deadline_record(context, matter_id, deadline_id)
        if current.version != command.expected_version:
            raise TaskDeadlineServiceError("version_conflict")
        allowed = {
            "calculated": {"cancel", "fail", "require_reconciliation"},
            "reviewed": {"cancel", "fail", "require_reconciliation"},
            "operative": {"cancel", "fail", "require_reconciliation"},
            "failed": {"retry", "require_reconciliation", "cancel"},
            "retry_pending": {"fail", "require_reconciliation", "cancel"},
            "reconciliation_required": {"reconcile", "cancel"},
            "reconciled": {"cancel", "fail"},
            "blocked": set(),
            "uncertain": set(),
            "cancelled": set(),
            "superseded": set(),
        }
        if command.transition not in allowed[current.state]:
            raise TaskDeadlineServiceError("precondition_failed")
        state_by_transition: dict[str, DeadlineState] = {
            "cancel": "cancelled",
            "fail": "failed",
            "retry": "retry_pending",
            "require_reconciliation": "reconciliation_required",
            "reconcile": "reconciled",
        }
        new_state = state_by_transition[command.transition]
        now = self._clock()
        provenance = ProvenanceReference(
            provenance_id=_uuid("deadline-transition-provenance", request_sha256),
            source_kind="lifecycle_transition",
            source_id=correlation,
            source_version=current.version + 1,
            source_sha256=request_sha256,
            recorded_at=now,
        )
        draft = current.model_dump()
        draft.update(
            version=current.version + 1,
            state=new_state,
            operative_due_at=None,
            cancelled_at=now if new_state == "cancelled" else None,
            cancellation_reason=(command.reason if new_state == "cancelled" else None),
            failure_code=(
                command.failure_code
                if new_state in {"failed", "retry_pending"}
                else None
            ),
            retry_of_version=(
                current.version if new_state == "retry_pending" else None
            ),
            reconciliation_of_version=(
                current.reconciliation_of_version or current.version
                if new_state in {"reconciliation_required", "reconciled"}
                else None
            ),
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            updated_by_principal_id=context.principal_id,
            updated_at=now,
            provenance=(*current.provenance, provenance),
        )
        draft.pop("audit_id")
        draft.pop("outbox_id")
        return self._commit_deadline(
            context=context,
            decision=decision,
            operation="deadline.transition",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_version=current.version,
            draft=draft,
        )

    def create_simulation_handoff(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: SimulationHandoffCommand,
    ) -> SimulationMutationReceipt:
        decision = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="external_action.simulation",
            capability="action.email.prepare",
            purpose="external_action_preparation",
        )
        request_sha256 = _request_sha256(
            "external_action.simulation", command.task_id, command
        )
        key_sha256 = _key_sha256(idempotency_key)
        existing = self._existing(
            context=context,
            operation="external_action.simulation",
            idempotency_key_sha256=key_sha256,
            request_sha256=request_sha256,
            expected_type=SimulationReceipt,
        )
        correlation = _uuid(
            "task-deadline-correlation", "external_action.simulation", request_sha256
        )
        if isinstance(existing, SimulationReceipt):
            return SimulationMutationReceipt.model_validate(
                {
                    "simulation": existing,
                    **self._evidence(
                        key_sha256=key_sha256,
                        request_sha256=request_sha256,
                        resource_version=1,
                        audit_id=existing.audit_id,
                        outbox_id=existing.outbox_id,
                        correlation_id=correlation,
                    ),
                }
            )
        task = self._get_task_record(context, matter_id, command.task_id)
        if task.status not in {"ready", "in_progress", "completed", "reconciled"}:
            raise TaskDeadlineServiceError("precondition_failed")
        if task.deadline_id != command.deadline_id:
            raise TaskDeadlineServiceError("precondition_failed")
        if command.deadline_id is not None:
            deadline = self._get_deadline_record(
                context, matter_id, command.deadline_id
            )
            if deadline.state != "operative" or deadline.review_state != "accepted":
                raise TaskDeadlineServiceError("precondition_failed")
            if task.due_at != deadline.operative_due_at:
                raise TaskDeadlineServiceError("precondition_failed")
        try:
            approval_current = self._simulation_gate.verify(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                task_id=command.task_id,
                deadline_id=command.deadline_id,
                work_product_id=command.work_product_id,
                work_product_version_id=command.work_product_version_id,
                work_product_version_number=command.work_product_version_number,
                work_product_content_sha256=command.work_product_content_sha256,
                approval_id=command.approval_id,
                approval_snapshot_sha256=command.approval_snapshot_sha256,
                action_kind=command.action_kind,
                destination_sha256=command.destination_sha256,
            )
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        if not approval_current:
            raise TaskDeadlineServiceError("precondition_failed")
        now = self._clock()
        receipt_id = _uuid(
            "action-simulation",
            context.tenant_id,
            matter_id,
            context.principal_id,
            key_sha256,
        )
        provenance = ProvenanceReference(
            provenance_id=_uuid("simulation-provenance", request_sha256),
            source_kind="simulation",
            source_id=correlation,
            source_version=1,
            source_sha256=request_sha256,
            recorded_at=now,
        )
        audit_id = _uuid(
            "task-deadline-audit", "external_action.simulation", request_sha256
        )
        outbox_id = _uuid(
            "task-deadline-outbox", "external_action.simulation", request_sha256
        )
        receipt = SimulationReceipt(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            receipt_id=receipt_id,
            task_id=command.task_id,
            deadline_id=command.deadline_id,
            work_product_id=command.work_product_id,
            work_product_version_id=command.work_product_version_id,
            work_product_version_number=command.work_product_version_number,
            work_product_content_sha256=command.work_product_content_sha256,
            approval_id=command.approval_id,
            approval_snapshot_sha256=command.approval_snapshot_sha256,
            destination_sha256=command.destination_sha256,
            action_kind="email",
            state="simulated",
            outcome="succeeded",
            external_effect=False,
            connector_invoked=False,
            dispatch_attempted=False,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
            actor_principal_id=context.principal_id,
            created_at=now,
            audit_id=audit_id,
            outbox_id=outbox_id,
            provenance=(provenance,),
        )
        audit, outbox, _ = self._write_evidence(
            context=context,
            decision=decision,
            operation="external_action.simulation",
            resource_kind="action_simulation",
            resource_id=receipt_id,
            resource_version=1,
            request_sha256=request_sha256,
            resource_sha256=canonical_sha256(receipt),
            now=now,
        )
        try:
            saved = self._repository.commit_simulation(
                operation="external_action.simulation",
                idempotency_key_sha256=key_sha256,
                request_sha256=request_sha256,
                receipt=receipt,
                audit=audit,
                outbox=outbox,
            )
        except TaskDeadlineIdempotencyConflict:
            raise TaskDeadlineServiceError("idempotency_conflict") from None
        except TaskDeadlineVersionConflict:
            raise TaskDeadlineServiceError("version_conflict") from None
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        return SimulationMutationReceipt.model_validate(
            {
                "simulation": saved,
                **self._evidence(
                    key_sha256=key_sha256,
                    request_sha256=request_sha256,
                    resource_version=1,
                    audit_id=saved.audit_id,
                    outbox_id=saved.outbox_id,
                    correlation_id=correlation,
                ),
            }
        )

    def _get_task_record(
        self,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        task_id: UUID,
    ) -> TaskRecord:
        try:
            record = self._repository.get_task(context.tenant_id, matter_id, task_id)
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        if record is None:
            raise TaskDeadlineServiceError("resource_unavailable")
        return record

    def _get_deadline_record(
        self,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        deadline_id: UUID,
    ) -> DeadlineRecord:
        try:
            record = self._repository.get_deadline(
                context.tenant_id, matter_id, deadline_id
            )
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        if record is None:
            raise TaskDeadlineServiceError("resource_unavailable")
        return record

    def get_task(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        task_id: UUID,
    ) -> TaskRecord:
        self._authorize(
            context=context,
            matter_id=matter_id,
            operation="task.read",
            capability="matter.read",
            purpose="matter_management",
        )
        return self._get_task_record(context, matter_id, task_id)

    def list_tasks(
        self, *, context: TaskDeadlineAccessContext, matter_id: UUID
    ) -> TaskListRead:
        self._authorize(
            context=context,
            matter_id=matter_id,
            operation="task.list",
            capability="matter.read",
            purpose="matter_management",
        )
        try:
            records = self._repository.list_tasks(context.tenant_id, matter_id)
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        return TaskListRead(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            projection_revision=canonical_sha256(records),
            tasks=records,
        )

    def get_deadline(
        self,
        *,
        context: TaskDeadlineAccessContext,
        matter_id: UUID,
        deadline_id: UUID,
    ) -> DeadlineRecord:
        self._authorize(
            context=context,
            matter_id=matter_id,
            operation="deadline.read",
            capability="matter.read",
            purpose="matter_management",
        )
        return self._get_deadline_record(context, matter_id, deadline_id)

    def list_deadlines(
        self, *, context: TaskDeadlineAccessContext, matter_id: UUID
    ) -> DeadlineListRead:
        self._authorize(
            context=context,
            matter_id=matter_id,
            operation="deadline.list",
            capability="matter.read",
            purpose="matter_management",
        )
        try:
            records = self._repository.list_deadlines(context.tenant_id, matter_id)
        except TaskDeadlineRepositoryUnavailable:
            raise TaskDeadlineServiceError("dependency_unavailable") from None
        return DeadlineListRead(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            projection_revision=canonical_sha256(records),
            deadlines=records,
        )
