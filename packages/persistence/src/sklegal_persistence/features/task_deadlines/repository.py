"""Atomic repository contracts for Task, Deadline, and simulation records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from .models import (
    DeadlineRecord,
    SimulationReceipt,
    TaskDeadlineAuditEvent,
    TaskDeadlineOutboxRecord,
    TaskRecord,
    canonical_sha256,
)

type ResourceRecord = TaskRecord | DeadlineRecord | SimulationReceipt


class TaskDeadlineRepositoryError(RuntimeError):
    """Base sanitized persistence error."""


class TaskDeadlineRepositoryUnavailable(TaskDeadlineRepositoryError):
    """Durable state, audit, or outbox has no current answer."""


class TaskDeadlineIdempotencyConflict(TaskDeadlineRepositoryError):
    """One idempotency key is bound to different request bytes."""


class TaskDeadlineVersionConflict(TaskDeadlineRepositoryError):
    """A mutation named a stale record version."""


class TaskDeadlineNotFound(TaskDeadlineRepositoryError):
    """A scoped resource is unavailable."""


class TaskDeadlineRepository(Protocol):
    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...

    def find_idempotent(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        operation: str,
        idempotency_key_sha256: str,
    ) -> tuple[str, ResourceRecord] | None: ...

    def commit_task(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        record: TaskRecord,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> TaskRecord: ...

    def commit_deadline(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        record: DeadlineRecord,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> DeadlineRecord: ...

    def commit_simulation(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        receipt: SimulationReceipt,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> SimulationReceipt: ...

    def get_task(
        self, tenant_id: UUID, matter_id: UUID, task_id: UUID
    ) -> TaskRecord | None: ...

    def list_tasks(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[TaskRecord, ...]: ...

    def get_deadline(
        self, tenant_id: UUID, matter_id: UUID, deadline_id: UUID
    ) -> DeadlineRecord | None: ...

    def list_deadlines(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[DeadlineRecord, ...]: ...

    def get_simulation(
        self, tenant_id: UUID, matter_id: UUID, receipt_id: UUID
    ) -> SimulationReceipt | None: ...


def validate_write_evidence(
    record: ResourceRecord,
    audit: TaskDeadlineAuditEvent,
    outbox: TaskDeadlineOutboxRecord,
) -> ResourceRecord:
    """Bind state, audit, and outbox to the exact same scoped bytes."""

    resource_kind: str
    resource_id: UUID
    resource_version: int
    if isinstance(record, TaskRecord):
        resource_kind = "task"
        resource_id = record.task_id
        resource_version = record.version
    elif isinstance(record, DeadlineRecord):
        resource_kind = "deadline"
        resource_id = record.deadline_id
        resource_version = record.version
    else:
        resource_kind = "action_simulation"
        resource_id = record.receipt_id
        resource_version = 1
    resource_sha256 = canonical_sha256(record)
    if (
        audit.audit_id != record.audit_id
        or outbox.outbox_id != record.outbox_id
        or outbox.audit_id != audit.audit_id
        or audit.tenant_id != record.tenant_id
        or audit.matter_id != record.matter_id
        or outbox.tenant_id != record.tenant_id
        or outbox.matter_id != record.matter_id
        or audit.resource_kind != resource_kind
        or outbox.resource_kind != resource_kind
        or audit.resource_id != resource_id
        or outbox.resource_id != resource_id
        or audit.resource_version != resource_version
        or audit.resource_sha256 != resource_sha256
        or outbox.payload_sha256 != resource_sha256
        or outbox.dispatch_allowed
    ):
        raise TaskDeadlineVersionConflict("audit or outbox does not bind record")
    return record


class InMemoryTaskDeadlineRepository:
    """Hermetic atomic repository for tests and public-synthetic composition."""

    def __init__(
        self,
        *,
        available: bool = True,
        audit_available: bool = True,
        outbox_available: bool = True,
    ) -> None:
        self.available = available
        self.audit_available = audit_available
        self.outbox_available = outbox_available
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}
        self._tasks: dict[tuple[UUID, UUID, UUID], list[TaskRecord]] = defaultdict(list)
        self._deadlines: dict[tuple[UUID, UUID, UUID], list[DeadlineRecord]] = (
            defaultdict(list)
        )
        self._simulations: dict[tuple[UUID, UUID, UUID], SimulationReceipt] = {}
        self._idempotency: dict[
            tuple[UUID, UUID, str, str], tuple[str, ResourceRecord]
        ] = {}
        self._audit: list[TaskDeadlineAuditEvent] = []
        self._outbox: list[TaskDeadlineOutboxRecord] = []

    @property
    def audit_events(self) -> tuple[TaskDeadlineAuditEvent, ...]:
        return tuple(self._audit)

    @property
    def outbox_records(self) -> tuple[TaskDeadlineOutboxRecord, ...]:
        return tuple(self._outbox)

    def set_matter_members(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        principal_ids: Iterable[UUID],
    ) -> None:
        self._members[(tenant_id, matter_id)] = frozenset(principal_ids)

    def _require_available(self) -> None:
        if not self.available:
            raise TaskDeadlineRepositoryUnavailable("repository unavailable")

    def _require_atomic_boundaries(self) -> None:
        self._require_available()
        if not self.audit_available or not self.outbox_available:
            raise TaskDeadlineRepositoryUnavailable("atomic evidence unavailable")

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_available()
        return principal_id in self._members.get((tenant_id, matter_id), frozenset())

    def find_idempotent(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        operation: str,
        idempotency_key_sha256: str,
    ) -> tuple[str, ResourceRecord] | None:
        self._require_available()
        return self._idempotency.get(
            (tenant_id, matter_id, operation, idempotency_key_sha256)
        )

    def _idempotent(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        record: ResourceRecord,
    ) -> ResourceRecord | None:
        key = (
            record.tenant_id,
            record.matter_id,
            operation,
            idempotency_key_sha256,
        )
        existing = self._idempotency.get(key)
        if existing is None:
            return None
        if existing[0] != request_sha256:
            raise TaskDeadlineIdempotencyConflict(
                "idempotency key is bound to another request"
            )
        return existing[1]

    def _record_commit(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        record: ResourceRecord,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> None:
        self._idempotency[
            (record.tenant_id, record.matter_id, operation, idempotency_key_sha256)
        ] = (request_sha256, record)
        self._audit.append(audit)
        self._outbox.append(outbox)

    def commit_task(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        record: TaskRecord,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> TaskRecord:
        self._require_atomic_boundaries()
        validate_write_evidence(record, audit, outbox)
        existing = self._idempotent(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            record=record,
        )
        if existing is not None:
            if not isinstance(existing, TaskRecord):
                raise TaskDeadlineIdempotencyConflict("resource kind changed")
            return existing
        key = (record.tenant_id, record.matter_id, record.task_id)
        history = self._tasks[key]
        current_version = 0 if not history else history[-1].version
        if (
            current_version != expected_version
            or record.version != expected_version + 1
        ):
            raise TaskDeadlineVersionConflict("Task version is stale")
        if history:
            current = history[-1]
            if (current.tenant_id, current.matter_id, current.task_id) != (
                record.tenant_id,
                record.matter_id,
                record.task_id,
            ):
                raise TaskDeadlineVersionConflict("Task identity changed")
            if record.created_at != current.created_at:
                raise TaskDeadlineVersionConflict("Task creation evidence changed")
        history.append(record)
        self._record_commit(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            record=record,
            audit=audit,
            outbox=outbox,
        )
        return record

    def commit_deadline(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        record: DeadlineRecord,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> DeadlineRecord:
        self._require_atomic_boundaries()
        validate_write_evidence(record, audit, outbox)
        existing = self._idempotent(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            record=record,
        )
        if existing is not None:
            if not isinstance(existing, DeadlineRecord):
                raise TaskDeadlineIdempotencyConflict("resource kind changed")
            return existing
        key = (record.tenant_id, record.matter_id, record.deadline_id)
        history = self._deadlines[key]
        current_version = 0 if not history else history[-1].version
        if (
            current_version != expected_version
            or record.version != expected_version + 1
        ):
            raise TaskDeadlineVersionConflict("Deadline version is stale")
        if history and record.created_at != history[-1].created_at:
            raise TaskDeadlineVersionConflict("Deadline creation evidence changed")
        history.append(record)
        self._record_commit(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            record=record,
            audit=audit,
            outbox=outbox,
        )
        return record

    def commit_simulation(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        receipt: SimulationReceipt,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> SimulationReceipt:
        self._require_atomic_boundaries()
        validate_write_evidence(receipt, audit, outbox)
        existing = self._idempotent(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            record=receipt,
        )
        if existing is not None:
            if not isinstance(existing, SimulationReceipt):
                raise TaskDeadlineIdempotencyConflict("resource kind changed")
            return existing
        key = (receipt.tenant_id, receipt.matter_id, receipt.receipt_id)
        if key in self._simulations:
            raise TaskDeadlineVersionConflict("simulation receipt already exists")
        self._simulations[key] = receipt
        self._record_commit(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            record=receipt,
            audit=audit,
            outbox=outbox,
        )
        return receipt

    def get_task(
        self, tenant_id: UUID, matter_id: UUID, task_id: UUID
    ) -> TaskRecord | None:
        self._require_available()
        history = self._tasks.get((tenant_id, matter_id, task_id))
        return None if not history else history[-1]

    def list_tasks(self, tenant_id: UUID, matter_id: UUID) -> tuple[TaskRecord, ...]:
        self._require_available()
        return tuple(
            history[-1]
            for (row_tenant, row_matter, _), history in sorted(
                self._tasks.items(), key=lambda item: str(item[0][2])
            )
            if row_tenant == tenant_id and row_matter == matter_id and history
        )

    def get_deadline(
        self, tenant_id: UUID, matter_id: UUID, deadline_id: UUID
    ) -> DeadlineRecord | None:
        self._require_available()
        history = self._deadlines.get((tenant_id, matter_id, deadline_id))
        return None if not history else history[-1]

    def list_deadlines(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[DeadlineRecord, ...]:
        self._require_available()
        return tuple(
            history[-1]
            for (row_tenant, row_matter, _), history in sorted(
                self._deadlines.items(), key=lambda item: str(item[0][2])
            )
            if row_tenant == tenant_id and row_matter == matter_id and history
        )

    def get_simulation(
        self, tenant_id: UUID, matter_id: UUID, receipt_id: UUID
    ) -> SimulationReceipt | None:
        self._require_available()
        return self._simulations.get((tenant_id, matter_id, receipt_id))
