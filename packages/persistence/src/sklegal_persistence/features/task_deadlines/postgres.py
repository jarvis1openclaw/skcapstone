"""PostgreSQL adapter for atomic Task and Deadline feature writes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, TypeVar, cast
from uuid import UUID

from .models import (
    DeadlineRecord,
    SimulationReceipt,
    TaskDeadlineAuditEvent,
    TaskDeadlineOutboxRecord,
    TaskRecord,
    canonical_sha256,
)
from .repository import (
    ResourceRecord,
    TaskDeadlineIdempotencyConflict,
    TaskDeadlineNotFound,
    TaskDeadlineRepositoryError,
    TaskDeadlineRepositoryUnavailable,
    TaskDeadlineVersionConflict,
    validate_write_evidence,
)

T = TypeVar("T")


class SqlTransaction(Protocol):
    def execute(self, sql: str, parameters: tuple[object, ...]) -> None: ...

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None: ...

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]: ...


class TransactionRunner(Protocol):
    def __call__(self, callback: Callable[[SqlTransaction], T]) -> T: ...


MEMBERSHIP_SQL = """
SELECT EXISTS (
    SELECT 1 FROM sklegal_legal.matter_memberships
    WHERE tenant_id = %s AND matter_id = %s AND principal_id = %s AND active
) AS allowed
"""
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
IDEMPOTENCY_SQL = """
SELECT request_sha256, response_record
FROM sklegal_task_deadline.idempotency_receipts
WHERE tenant_id = %s AND matter_id = %s AND operation = %s
  AND idempotency_key_sha256 = %s
"""
TASK_CURRENT_SQL = """
SELECT record FROM sklegal_task_deadline.task_versions
WHERE tenant_id = %s AND matter_id = %s AND task_id = %s
ORDER BY version DESC LIMIT 1
"""
TASK_LIST_SQL = """
SELECT DISTINCT ON (task_id) record
FROM sklegal_task_deadline.task_versions
WHERE tenant_id = %s AND matter_id = %s
ORDER BY task_id, version DESC
"""
DEADLINE_CURRENT_SQL = """
SELECT record FROM sklegal_task_deadline.deadline_versions
WHERE tenant_id = %s AND matter_id = %s AND deadline_id = %s
ORDER BY version DESC LIMIT 1
"""
DEADLINE_LIST_SQL = """
SELECT DISTINCT ON (deadline_id) record
FROM sklegal_task_deadline.deadline_versions
WHERE tenant_id = %s AND matter_id = %s
ORDER BY deadline_id, version DESC
"""
SIMULATION_SQL = """
SELECT record FROM sklegal_task_deadline.simulation_receipts
WHERE tenant_id = %s AND matter_id = %s AND receipt_id = %s
"""
LOCK_TASK_SQL = """
SELECT version FROM sklegal_task_deadline.task_versions
WHERE tenant_id = %s AND matter_id = %s AND task_id = %s
ORDER BY version DESC LIMIT 1
"""
LOCK_DEADLINE_SQL = """
SELECT version FROM sklegal_task_deadline.deadline_versions
WHERE tenant_id = %s AND matter_id = %s AND deadline_id = %s
ORDER BY version DESC LIMIT 1
"""
INSERT_TASK_SQL = """
INSERT INTO sklegal_task_deadline.task_versions
    (tenant_id, matter_id, task_id, version, state, record, record_sha256,
     policy_decision_id, policy_revision, actor_principal_id, recorded_at)
VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
"""
INSERT_DEADLINE_SQL = """
INSERT INTO sklegal_task_deadline.deadline_versions
    (tenant_id, matter_id, deadline_id, version, state, review_state,
     candidate_due_at, operative_due_at, calculation_sha256,
     trigger_evidence_sha256, rule_authority_sha256, holiday_calendar_sha256,
     record, record_sha256, policy_decision_id, policy_revision,
     actor_principal_id, recorded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s::jsonb, %s, %s, %s, %s, %s)
"""
INSERT_SIMULATION_SQL = """
INSERT INTO sklegal_task_deadline.simulation_receipts
    (tenant_id, matter_id, receipt_id, task_id, deadline_id,
     work_product_id, work_product_version_id, work_product_version_number,
     work_product_content_sha256, approval_id, approval_snapshot_sha256,
     destination_sha256, state, external_effect, connector_invoked,
     dispatch_attempted, record, record_sha256, policy_decision_id,
     policy_revision, actor_principal_id, recorded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s::jsonb, %s, %s, %s, %s, %s)
"""
INSERT_IDEMPOTENCY_SQL = """
INSERT INTO sklegal_task_deadline.idempotency_receipts
    (tenant_id, matter_id, operation, idempotency_key_sha256, request_sha256,
     resource_kind, resource_id, resource_version, response_record, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
"""
INSERT_AUDIT_SQL = """
INSERT INTO sklegal_task_deadline.audit_events
    (tenant_id, matter_id, audit_id, resource_kind, resource_id,
     resource_version, action, outcome, actor_principal_id, policy_decision_id,
     policy_revision, correlation_id, request_sha256, resource_sha256, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_OUTBOX_SQL = """
INSERT INTO sklegal_task_deadline.outbox
    (tenant_id, matter_id, outbox_id, audit_id, resource_kind,
     resource_id, topic, payload_sha256, dispatch_allowed, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _json(record: ResourceRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload(value: object) -> ResourceRecord:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TaskDeadlineRepositoryUnavailable("malformed feature record")
    schema = value.get("schemaVersion") or value.get("schema_version")
    model: type[TaskRecord] | type[DeadlineRecord] | type[SimulationReceipt]
    if schema == "sklegal.task/v1":
        model = TaskRecord
    elif schema == "sklegal.deadline/v1":
        model = DeadlineRecord
    elif schema == "sklegal.action-simulation/v1":
        model = SimulationReceipt
    else:
        raise TaskDeadlineRepositoryUnavailable("unknown feature record schema")
    return model.model_validate(value)


def _resource(record: ResourceRecord) -> tuple[str, UUID, int]:
    if isinstance(record, TaskRecord):
        return "task", record.task_id, record.version
    if isinstance(record, DeadlineRecord):
        return "deadline", record.deadline_id, record.version
    return "action_simulation", record.receipt_id, 1


class PostgresTaskDeadlineRepository:
    """Use one transaction for record, idempotency, audit, and outbox."""

    def __init__(self, transaction: TransactionRunner) -> None:
        self._transaction = transaction

    def _run(self, callback: Callable[[SqlTransaction], T]) -> T:
        try:
            return self._transaction(callback)
        except TaskDeadlineRepositoryError:
            raise
        except Exception:
            raise TaskDeadlineRepositoryUnavailable(
                "Task and Deadline store is unavailable"
            ) from None

    def _read_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        return self._run(lambda tx: tx.fetch_one(sql, parameters))

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        row = self._read_one(MEMBERSHIP_SQL, (tenant_id, matter_id, principal_id))
        return bool(row and row.get("allowed"))

    def find_idempotent(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        operation: str,
        idempotency_key_sha256: str,
    ) -> tuple[str, ResourceRecord] | None:
        row = self._read_one(
            IDEMPOTENCY_SQL,
            (tenant_id, matter_id, operation, idempotency_key_sha256),
        )
        if row is None:
            return None
        request_sha256 = row.get("request_sha256")
        if not isinstance(request_sha256, str) or "response_record" not in row:
            raise TaskDeadlineRepositoryUnavailable("malformed idempotency receipt")
        return request_sha256, _payload(row["response_record"])

    def _get(self, sql: str, parameters: tuple[object, ...]) -> ResourceRecord | None:
        row = self._read_one(sql, parameters)
        if row is None:
            return None
        if "record" not in row:
            raise TaskDeadlineRepositoryUnavailable("malformed feature record")
        return _payload(row["record"])

    def get_task(
        self, tenant_id: UUID, matter_id: UUID, task_id: UUID
    ) -> TaskRecord | None:
        record = self._get(TASK_CURRENT_SQL, (tenant_id, matter_id, task_id))
        if record is not None and not isinstance(record, TaskRecord):
            raise TaskDeadlineRepositoryUnavailable("Task schema mismatch")
        return record

    def list_tasks(self, tenant_id: UUID, matter_id: UUID) -> tuple[TaskRecord, ...]:
        rows = self._run(lambda tx: tx.fetch_all(TASK_LIST_SQL, (tenant_id, matter_id)))
        records = tuple(_payload(row.get("record")) for row in rows)
        if not all(isinstance(record, TaskRecord) for record in records):
            raise TaskDeadlineRepositoryUnavailable("Task schema mismatch")
        return cast(tuple[TaskRecord, ...], records)

    def get_deadline(
        self, tenant_id: UUID, matter_id: UUID, deadline_id: UUID
    ) -> DeadlineRecord | None:
        record = self._get(DEADLINE_CURRENT_SQL, (tenant_id, matter_id, deadline_id))
        if record is not None and not isinstance(record, DeadlineRecord):
            raise TaskDeadlineRepositoryUnavailable("Deadline schema mismatch")
        return record

    def list_deadlines(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[DeadlineRecord, ...]:
        rows = self._run(
            lambda tx: tx.fetch_all(DEADLINE_LIST_SQL, (tenant_id, matter_id))
        )
        records = tuple(_payload(row.get("record")) for row in rows)
        if not all(isinstance(record, DeadlineRecord) for record in records):
            raise TaskDeadlineRepositoryUnavailable("Deadline schema mismatch")
        return cast(tuple[DeadlineRecord, ...], records)

    def get_simulation(
        self, tenant_id: UUID, matter_id: UUID, receipt_id: UUID
    ) -> SimulationReceipt | None:
        record = self._get(SIMULATION_SQL, (tenant_id, matter_id, receipt_id))
        if record is not None and not isinstance(record, SimulationReceipt):
            raise TaskDeadlineRepositoryUnavailable("simulation schema mismatch")
        return record

    def _commit(
        self,
        *,
        operation: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        expected_version: int,
        record: ResourceRecord,
        audit: TaskDeadlineAuditEvent,
        outbox: TaskDeadlineOutboxRecord,
    ) -> ResourceRecord:
        validate_write_evidence(record, audit, outbox)
        resource_kind, resource_id, resource_version = _resource(record)

        def transaction(tx: SqlTransaction) -> ResourceRecord:
            scope = f"{record.tenant_id}:{record.matter_id}:{operation}:{idempotency_key_sha256}"
            tx.execute(LOCK_SQL, (scope,))
            existing = tx.fetch_one(
                IDEMPOTENCY_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    operation,
                    idempotency_key_sha256,
                ),
            )
            if existing is not None:
                prior_hash = existing.get("request_sha256")
                prior = _payload(existing.get("response_record"))
                if prior_hash != request_sha256 or type(prior) is not type(record):
                    raise TaskDeadlineIdempotencyConflict(
                        "idempotency key is bound to another request"
                    )
                return prior
            current_version = 0
            if isinstance(record, TaskRecord):
                tx.execute(
                    LOCK_SQL,
                    (f"{record.tenant_id}:{record.matter_id}:task:{record.task_id}",),
                )
                current = tx.fetch_one(
                    LOCK_TASK_SQL,
                    (record.tenant_id, record.matter_id, record.task_id),
                )
                version_value = None if current is None else current.get("version")
                if version_value is not None and not isinstance(version_value, int):
                    raise TaskDeadlineRepositoryUnavailable("malformed Task version")
                current_version = 0 if version_value is None else version_value
            elif isinstance(record, DeadlineRecord):
                tx.execute(
                    LOCK_SQL,
                    (
                        f"{record.tenant_id}:{record.matter_id}:deadline:"
                        f"{record.deadline_id}",
                    ),
                )
                current = tx.fetch_one(
                    LOCK_DEADLINE_SQL,
                    (record.tenant_id, record.matter_id, record.deadline_id),
                )
                version_value = None if current is None else current.get("version")
                if version_value is not None and not isinstance(version_value, int):
                    raise TaskDeadlineRepositoryUnavailable(
                        "malformed Deadline version"
                    )
                current_version = 0 if version_value is None else version_value
            else:
                current = tx.fetch_one(
                    SIMULATION_SQL,
                    (record.tenant_id, record.matter_id, record.receipt_id),
                )
                if current is not None:
                    raise TaskDeadlineVersionConflict(
                        "simulation receipt already exists"
                    )
            if (
                current_version != expected_version
                or resource_version != expected_version + 1
            ):
                raise TaskDeadlineVersionConflict("feature record version is stale")
            payload = _json(record)
            record_sha256 = canonical_sha256(record)
            if isinstance(record, TaskRecord):
                tx.execute(
                    INSERT_TASK_SQL,
                    (
                        record.tenant_id,
                        record.matter_id,
                        record.task_id,
                        record.version,
                        record.status,
                        payload,
                        record_sha256,
                        record.policy_decision_id,
                        record.policy_revision,
                        record.updated_by_principal_id,
                        record.updated_at,
                    ),
                )
            elif isinstance(record, DeadlineRecord):
                tx.execute(
                    INSERT_DEADLINE_SQL,
                    (
                        record.tenant_id,
                        record.matter_id,
                        record.deadline_id,
                        record.version,
                        record.state,
                        record.review_state,
                        record.candidate_due_at,
                        record.operative_due_at,
                        record.calculation_sha256,
                        canonical_sha256(record.trigger),
                        canonical_sha256(record.rule),
                        canonical_sha256(record.calendar),
                        payload,
                        record_sha256,
                        record.policy_decision_id,
                        record.policy_revision,
                        record.updated_by_principal_id,
                        record.updated_at,
                    ),
                )
            else:
                tx.execute(
                    INSERT_SIMULATION_SQL,
                    (
                        record.tenant_id,
                        record.matter_id,
                        record.receipt_id,
                        record.task_id,
                        record.deadline_id,
                        record.work_product_id,
                        record.work_product_version_id,
                        record.work_product_version_number,
                        record.work_product_content_sha256,
                        record.approval_id,
                        record.approval_snapshot_sha256,
                        record.destination_sha256,
                        record.state,
                        record.external_effect,
                        record.connector_invoked,
                        record.dispatch_attempted,
                        payload,
                        record_sha256,
                        record.policy_decision_id,
                        record.policy_revision,
                        record.actor_principal_id,
                        record.created_at,
                    ),
                )
            tx.execute(
                INSERT_IDEMPOTENCY_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    operation,
                    idempotency_key_sha256,
                    request_sha256,
                    resource_kind,
                    resource_id,
                    resource_version,
                    payload,
                    audit.occurred_at,
                ),
            )
            tx.execute(
                INSERT_AUDIT_SQL,
                (
                    audit.tenant_id,
                    audit.matter_id,
                    audit.audit_id,
                    audit.resource_kind,
                    audit.resource_id,
                    audit.resource_version,
                    audit.action,
                    audit.outcome,
                    audit.actor_principal_id,
                    audit.policy_decision_id,
                    audit.policy_revision,
                    audit.correlation_id,
                    audit.request_sha256,
                    audit.resource_sha256,
                    audit.occurred_at,
                ),
            )
            tx.execute(
                INSERT_OUTBOX_SQL,
                (
                    outbox.tenant_id,
                    outbox.matter_id,
                    outbox.outbox_id,
                    outbox.audit_id,
                    outbox.resource_kind,
                    outbox.resource_id,
                    outbox.topic,
                    outbox.payload_sha256,
                    outbox.dispatch_allowed,
                    outbox.created_at,
                ),
            )
            return record

        return self._run(transaction)

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
        result = self._commit(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            expected_version=expected_version,
            record=record,
            audit=audit,
            outbox=outbox,
        )
        if not isinstance(result, TaskRecord):
            raise TaskDeadlineNotFound("Task response is unavailable")
        return result

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
        result = self._commit(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            expected_version=expected_version,
            record=record,
            audit=audit,
            outbox=outbox,
        )
        if not isinstance(result, DeadlineRecord):
            raise TaskDeadlineNotFound("Deadline response is unavailable")
        return result

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
        result = self._commit(
            operation=operation,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            expected_version=0,
            record=receipt,
            audit=audit,
            outbox=outbox,
        )
        if not isinstance(result, SimulationReceipt):
            raise TaskDeadlineNotFound("simulation response is unavailable")
        return result
