from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pytest
from sklegal_api.features.task_deadlines.contracts import UpsertTaskCommand
from sklegal_persistence.features.task_deadlines.postgres import (
    IDEMPOTENCY_SQL,
    INSERT_AUDIT_SQL,
    INSERT_IDEMPOTENCY_SQL,
    INSERT_OUTBOX_SQL,
    INSERT_TASK_SQL,
    LOCK_SQL,
    MEMBERSHIP_SQL,
    PostgresTaskDeadlineRepository,
)
from sklegal_persistence.features.task_deadlines.repository import (
    TaskDeadlineIdempotencyConflict,
    TaskDeadlineRepositoryUnavailable,
)

from .helpers import MATTER, PRINCIPAL, TENANT, access, composition


class RecordingTransaction:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.rows: dict[str, Mapping[str, object] | None] = {}

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.executed.append((sql, parameters))

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        self.executed.append((sql, parameters))
        return self.rows.get(sql)

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        self.executed.append((sql, parameters))
        return ()


def _created_task_evidence():
    service, repository, _, _ = composition()
    receipt = service.create_task(
        context=access(),
        matter_id=MATTER,
        idempotency_key="postgres-task",
        command=UpsertTaskCommand(
            title="Persist Task", description="Public-synthetic record."
        ),
    )
    return receipt.task, repository.audit_events[0], repository.outbox_records[0]


def test_postgres_commit_writes_record_receipt_audit_and_outbox_in_one_callback() -> (
    None
):
    transaction = RecordingTransaction()
    callback_count = 0

    def runner(callback: Callable):
        nonlocal callback_count
        callback_count += 1
        return callback(transaction)

    adapter = PostgresTaskDeadlineRepository(runner)
    record, audit, outbox = _created_task_evidence()
    result = adapter.commit_task(
        operation="task.create",
        idempotency_key_sha256="11" * 32,
        request_sha256=audit.request_sha256,
        expected_version=0,
        record=record,
        audit=audit,
        outbox=outbox,
    )
    assert result == record
    assert callback_count == 1
    statements = [sql for sql, _ in transaction.executed]
    for expected in (
        LOCK_SQL,
        INSERT_TASK_SQL,
        INSERT_IDEMPOTENCY_SQL,
        INSERT_AUDIT_SQL,
        INSERT_OUTBOX_SQL,
    ):
        assert expected in statements
    assert statements.index(INSERT_TASK_SQL) < statements.index(INSERT_AUDIT_SQL)
    assert statements.index(INSERT_AUDIT_SQL) < statements.index(INSERT_OUTBOX_SQL)


def test_postgres_idempotency_conflict_fails_before_any_insert() -> None:
    transaction = RecordingTransaction()
    record, audit, outbox = _created_task_evidence()
    transaction.rows[IDEMPOTENCY_SQL] = {
        "request_sha256": "22" * 32,
        "response_record": record.model_dump(mode="json", by_alias=True),
    }
    adapter = PostgresTaskDeadlineRepository(lambda callback: callback(transaction))
    with pytest.raises(TaskDeadlineIdempotencyConflict):
        adapter.commit_task(
            operation="task.create",
            idempotency_key_sha256="11" * 32,
            request_sha256=audit.request_sha256,
            expected_version=0,
            record=record,
            audit=audit,
            outbox=outbox,
        )
    assert INSERT_TASK_SQL not in [sql for sql, _ in transaction.executed]


def test_postgres_membership_is_scoped_and_driver_errors_are_sanitized() -> None:
    transaction = RecordingTransaction()
    transaction.rows[MEMBERSHIP_SQL] = {"allowed": True}
    adapter = PostgresTaskDeadlineRepository(lambda callback: callback(transaction))
    assert adapter.is_matter_member(TENANT, MATTER, PRINCIPAL) is True
    assert transaction.executed[-1][1] == (TENANT, MATTER, PRINCIPAL)

    def broken(_callback):
        raise RuntimeError("driver detail with internal host")

    unavailable = PostgresTaskDeadlineRepository(broken)
    with pytest.raises(
        TaskDeadlineRepositoryUnavailable,
        match="Task and Deadline store is unavailable",
    ) as captured:
        unavailable.is_matter_member(TENANT, MATTER, PRINCIPAL)
    assert "internal host" not in str(captured.value)
