from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sklegal_persistence.features.agent_runs.models import (
    AgentRunAuditEvent,
    AgentRunRecord,
    canonical_sha256,
)
from sklegal_persistence.features.agent_runs.postgres import (
    IDEMPOTENCY_SQL,
    INSERT_ATTEMPT_SQL,
    INSERT_AUDIT_SQL,
    INSERT_CHALLENGE_SQL,
    INSERT_DISPOSITION_SQL,
    INSERT_IDEMPOTENCY_SQL,
    INSERT_IDENTITY_SQL,
    INSERT_RECOMMENDATION_SQL,
    INSERT_RUN_SQL,
    INSERT_TOOL_CALL_SQL,
    LOCK_CURRENT_RUN_SQL,
    LOCK_SCOPE_SQL,
    MEMBERSHIP_SQL,
    PostgresAgentRunRepository,
    SqlTransaction,
)
from sklegal_persistence.features.agent_runs.repository import (
    AgentRunIdempotencyConflict,
    AgentRunRepositoryUnavailable,
)

from tests.features.agent_runs.helpers import fixture_record


def audit_for(record: AgentRunRecord) -> AgentRunAuditEvent:
    return AgentRunAuditEvent(
        event_id=record.audit_event_ids[-1],
        tenant_id=record.tenant_id,
        matter_id=record.matter_id,
        run_id=record.run_id,
        run_version=record.version,
        correlation_id=record.authorization.correlation_id,
        actor_principal_id=record.authorization.principal_id,
        action=(
            "agent_run.recorded"
            if record.version == 1
            else "agent_run.challenge_recorded"
        ),
        outcome="completed",
        subject_sha256=canonical_sha256(record),
        occurred_at=record.updated_at,
    )


class FakeTransaction:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.responses: dict[str, Any] = {}
        self.fail_sql: str | None = None

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        if sql == self.fail_sql:
            raise RuntimeError("synthetic transaction failure")
        self.executed.append((sql, parameters))

    def fetch_one(self, sql: str, parameters: tuple[object, ...]):  # type: ignore[no-untyped-def]
        del parameters
        value = self.responses.get(sql)
        if callable(value):
            return value()
        return value

    def fetch_all(self, sql: str, parameters: tuple[object, ...]):  # type: ignore[no-untyped-def]
        del parameters
        return tuple(self.responses.get(sql, ()))


def runner(transaction: FakeTransaction):  # type: ignore[no-untyped-def]
    def run(operation: Callable[[SqlTransaction], Any]) -> Any:
        return operation(transaction)

    return run


def test_postgres_commit_writes_identity_version_evidence_receipt_and_audit() -> None:
    transaction = FakeTransaction()
    repository = PostgresAgentRunRepository(runner(transaction))
    record = fixture_record()
    audit = audit_for(record)
    committed = repository.commit_new(
        request_sha256="1" * 64, record=record, audit=audit
    )
    assert committed == record
    statements = [sql for sql, _ in transaction.executed]
    assert statements[0] == LOCK_SCOPE_SQL
    assert INSERT_IDENTITY_SQL in statements
    assert INSERT_RUN_SQL in statements
    assert statements.count(INSERT_ATTEMPT_SQL) == 1
    assert statements.count(INSERT_TOOL_CALL_SQL) == 1
    assert statements.count(INSERT_RECOMMENDATION_SQL) == 1
    assert statements[-2:] == [INSERT_IDEMPOTENCY_SQL, INSERT_AUDIT_SQL]
    run_parameters = next(
        parameters for sql, parameters in transaction.executed if sql == INSERT_RUN_SQL
    )
    assert INSERT_RUN_SQL.count("%s") == len(run_parameters)
    assert INSERT_CHALLENGE_SQL.count("%s") == 22
    assert INSERT_DISPOSITION_SQL.count("%s") == 20
    assert record.authorization.credential_digest in run_parameters
    assert all("Bearer " not in str(value) for value in run_parameters)


def test_postgres_commit_returns_same_idempotent_record_and_denies_drift() -> None:
    transaction = FakeTransaction()
    record = fixture_record()
    transaction.responses[IDEMPOTENCY_SQL] = {
        "request_sha256": "1" * 64,
        "record": record.model_dump(mode="json", by_alias=True),
    }
    repository = PostgresAgentRunRepository(runner(transaction))
    audit = audit_for(record)
    assert (
        repository.commit_new(request_sha256="1" * 64, record=record, audit=audit)
        == record
    )
    assert [sql for sql, _ in transaction.executed] == [LOCK_SCOPE_SQL]
    with pytest.raises(AgentRunIdempotencyConflict):
        repository.commit_new(request_sha256="2" * 64, record=record, audit=audit)


def test_postgres_append_locks_current_version_and_writes_new_projection() -> None:
    transaction = FakeTransaction()
    current = fixture_record()
    changed = current.model_copy(
        update={
            "version": 2,
            "audit_event_ids": (
                *current.audit_event_ids,
                UUID("10000000-0000-4000-8000-000000000103"),
            ),
        }
    )
    transaction.responses[LOCK_CURRENT_RUN_SQL] = {
        "record": current.model_dump(mode="json", by_alias=True)
    }
    repository = PostgresAgentRunRepository(runner(transaction))
    audit = audit_for(changed)
    assert (
        repository.append_version(
            expected_version=1,
            idempotency_key=UUID("10000000-0000-4000-8000-000000000104"),
            request_sha256="2" * 64,
            record=changed,
            audit=audit,
        )
        == changed
    )
    statements = [sql for sql, _ in transaction.executed]
    assert INSERT_IDENTITY_SQL not in statements
    assert INSERT_RUN_SQL in statements
    assert statements[-2:] == [INSERT_IDEMPOTENCY_SQL, INSERT_AUDIT_SQL]


def test_postgres_membership_and_transaction_failures_fail_closed() -> None:
    transaction = FakeTransaction()
    repository = PostgresAgentRunRepository(runner(transaction))
    record = fixture_record()
    transaction.responses[MEMBERSHIP_SQL] = {"active": True}
    assert repository.is_matter_member(
        record.tenant_id, record.matter_id, record.request.principal_id
    )
    transaction.fail_sql = INSERT_AUDIT_SQL
    with pytest.raises(AgentRunRepositoryUnavailable):
        repository.commit_new(
            request_sha256="1" * 64,
            record=record,
            audit=audit_for(record),
        )
