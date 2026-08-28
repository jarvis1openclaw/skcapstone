from __future__ import annotations

from uuid import UUID

import pytest
from sklegal_persistence.features.agent_runs.models import (
    AgentRunAuditEvent,
    canonical_sha256,
)
from sklegal_persistence.features.agent_runs.repository import (
    AgentRunIdempotencyConflict,
    AgentRunRepositoryUnavailable,
    AgentRunVersionConflict,
    InMemoryAgentRunRepository,
)

from tests.features.agent_runs.helpers import fixture_record


def audit_for(record) -> AgentRunAuditEvent:  # type: ignore[no-untyped-def]
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


def test_repository_preserves_versions_idempotency_and_atomic_audit() -> None:
    store = InMemoryAgentRunRepository()
    initial = fixture_record()
    first_audit = audit_for(initial)
    assert (
        store.commit_new(request_sha256="1" * 64, record=initial, audit=first_audit)
        == initial
    )
    second_event_id = UUID("10000000-0000-4000-8000-000000000073")
    changed = initial.model_copy(
        update={
            "version": 2,
            "audit_event_ids": (*initial.audit_event_ids, second_event_id),
        }
    )
    mutation_key = UUID("10000000-0000-4000-8000-000000000072")
    second_audit = audit_for(changed)
    assert (
        store.append_version(
            expected_version=1,
            idempotency_key=mutation_key,
            request_sha256="2" * 64,
            record=changed,
            audit=second_audit,
        )
        == changed
    )
    assert store.history(initial.tenant_id, initial.matter_id, initial.run_id) == (
        initial,
        changed,
    )
    assert store.audit_events() == (first_audit, second_audit)
    assert store.find_idempotent(
        initial.tenant_id, initial.matter_id, mutation_key
    ) == ("2" * 64, changed)


def test_repository_denies_idempotency_drift_and_stale_version() -> None:
    store = InMemoryAgentRunRepository()
    initial = fixture_record()
    audit = audit_for(initial)
    store.commit_new(request_sha256="1" * 64, record=initial, audit=audit)
    with pytest.raises(AgentRunIdempotencyConflict):
        store.commit_new(request_sha256="2" * 64, record=initial, audit=audit)
    with pytest.raises(AgentRunVersionConflict):
        invalid = initial.model_copy(
            update={
                "version": 3,
                "audit_event_ids": (
                    *initial.audit_event_ids,
                    UUID("10000000-0000-4000-8000-000000000075"),
                ),
            }
        )
        store.append_version(
            expected_version=2,
            idempotency_key=UUID("10000000-0000-4000-8000-000000000075"),
            request_sha256="3" * 64,
            record=invalid,
            audit=audit_for(invalid),
        )


def test_audit_outage_prevents_any_durable_mutation() -> None:
    store = InMemoryAgentRunRepository(audit_available=False)
    record = fixture_record()
    audit = audit_for(record)
    with pytest.raises(AgentRunRepositoryUnavailable):
        store.commit_new(request_sha256="1" * 64, record=record, audit=audit)
    store.audit_available = True
    assert store.get(record.tenant_id, record.matter_id, record.run_id) is None
    assert store.audit_events() == ()
