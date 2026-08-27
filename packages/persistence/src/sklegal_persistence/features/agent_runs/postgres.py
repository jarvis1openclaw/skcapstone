"""Driver-neutral PostgreSQL adapter for governed Agent Runs.

Every write is performed inside the transaction supplied by the composition
root. The adapter writes the version record, normalized evidence rows,
idempotency receipt, and audit event in that same transaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel

from .models import AgentRunAuditEvent, AgentRunRecord, canonical_sha256
from .repository import (
    AgentRunIdempotencyConflict,
    AgentRunNotFound,
    AgentRunRepositoryUnavailable,
    AgentRunVersionConflict,
    validate_write_evidence,
)

T = TypeVar("T")
type Row = Mapping[str, object]


class SqlTransaction(Protocol):
    def execute(self, sql: str, parameters: tuple[object, ...]) -> None: ...

    def fetch_one(self, sql: str, parameters: tuple[object, ...]) -> Row | None: ...

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> tuple[Row, ...]: ...


class TransactionRunner(Protocol):
    def __call__(self, operation: Callable[[SqlTransaction], T]) -> T: ...


LOCK_SCOPE_SQL = """
SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))
""".strip()

MEMBERSHIP_SQL = """
SELECT active
FROM sklegal_legal.matter_memberships
WHERE tenant_id = %s::uuid
  AND matter_id = %s::uuid
  AND principal_id = %s::uuid
  AND active
""".strip()

IDEMPOTENCY_SQL = """
SELECT receipt.request_sha256, current.record
FROM sklegal_workflow.agent_run_idempotency AS receipt
JOIN sklegal_workflow.agent_run_current AS current
  ON current.tenant_id = receipt.tenant_id
 AND current.matter_id = receipt.matter_id
 AND current.run_id = receipt.run_id
WHERE receipt.tenant_id = %s::uuid
  AND receipt.matter_id = %s::uuid
  AND receipt.idempotency_key = %s::uuid
""".strip()

CURRENT_RUN_SQL = """
SELECT record
FROM sklegal_workflow.agent_runs
WHERE tenant_id = %s::uuid AND matter_id = %s::uuid AND run_id = %s::uuid
ORDER BY version DESC
LIMIT 1
""".strip()

LOCK_CURRENT_RUN_SQL = CURRENT_RUN_SQL

LIST_RUNS_SQL = """
SELECT record
FROM sklegal_workflow.agent_run_current
WHERE tenant_id = %s::uuid AND matter_id = %s::uuid
ORDER BY updated_at, run_id
""".strip()

RUN_HISTORY_SQL = """
SELECT record
FROM sklegal_workflow.agent_runs
WHERE tenant_id = %s::uuid AND matter_id = %s::uuid AND run_id = %s::uuid
ORDER BY version
""".strip()

AUDIT_EVENTS_SQL = """
SELECT event_id, tenant_id, matter_id, run_id, run_version, correlation_id,
       actor_principal_id, action, outcome, subject_sha256, occurred_at
FROM sklegal_workflow.agent_run_audit_events
ORDER BY occurred_at, event_id
""".strip()

INSERT_IDENTITY_SQL = """
INSERT INTO sklegal_workflow.agent_run_identities (
    tenant_id, matter_id, run_id, request_id, requested_by_principal_id,
    retry_of_run_id, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s)
""".strip()

INSERT_RUN_SQL = """
INSERT INTO sklegal_workflow.agent_runs (
    tenant_id, matter_id, run_id, version, schema_version, status,
    classification, public_synthetic, purpose, capability,
    capability_decision_id, verifier_policy_version, revocation_revision,
    credential_digest, agent_spec_id, agent_spec_version, agent_spec_sha256,
    deployment_revision, deployment_sha256, logical_route_id,
    prompt_template_sha256, output_schema_sha256, scoring_policy_sha256,
    matter_snapshot_sha256, corpus_snapshot_sha256, authority_snapshot_sha256,
    policy_snapshot_sha256, proposal_payload_sha256, record_sha256, record,
    created_at, updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
)
""".strip()

INSERT_ATTEMPT_SQL = """
INSERT INTO sklegal_workflow.agent_run_attempts (
    tenant_id, matter_id, run_id, run_version, attempt_id, attempt_number,
    outcome, error_code, retryable, started_at, completed_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
""".strip()

INSERT_TOOL_CALL_SQL = """
INSERT INTO sklegal_workflow.agent_tool_calls (
    tenant_id, matter_id, run_id, run_version, tool_call_id, sequence, tool_id,
    capability_decision_id, arguments_sha256, result_sha256, outcome,
    error_code, started_at, completed_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
""".strip()

INSERT_RECOMMENDATION_SQL = """
INSERT INTO sklegal_workflow.agent_recommendations (
    tenant_id, matter_id, run_id, run_version, recommendation_id,
    recommendation_version, target_kind, target_id, proposed_output,
    score_total, confidence_basis_points, review_state,
    recommendation_sha256, recommendation
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
""".strip()

INSERT_CHALLENGE_SQL = """
INSERT INTO sklegal_workflow.agent_blind_challenges (
    tenant_id, matter_id, run_id, run_version, challenge_id,
    challenge_version, recommendation_id, recommendation_version,
    challenger_spec_sha256, challenger_route_sha256,
    blind_input_sha256, independent_output_sha256,
    initiated_by_principal_id, capability_decision_id,
    verifier_policy_version, revocation_revision, credential_digest,
    saw_challenged_conclusion, outcome, challenge_sha256, challenge, created_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s::jsonb, %s
)
""".strip()

INSERT_DISPOSITION_SQL = """
INSERT INTO sklegal_workflow.agent_human_dispositions (
    tenant_id, matter_id, run_id, run_version, disposition_id,
    disposition_version, recommendation_id, recommendation_version,
    reviewer_principal_id, capability_decision_id, decision,
    verifier_policy_version, revocation_revision, credential_digest,
    creates_domain_record, external_effect, policy_revision,
    disposition_sha256, disposition, decided_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s::jsonb, %s
)
""".strip()

INSERT_IDEMPOTENCY_SQL = """
INSERT INTO sklegal_workflow.agent_run_idempotency (
    tenant_id, matter_id, idempotency_key, request_sha256, run_id
) VALUES (%s, %s, %s, %s, %s)
""".strip()

INSERT_AUDIT_SQL = """
INSERT INTO sklegal_workflow.agent_run_audit_events (
    tenant_id, matter_id, event_id, run_id, run_version, correlation_id,
    actor_principal_id, action, outcome, subject_sha256, occurred_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
""".strip()


def _json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _record(row: Row | None) -> AgentRunRecord | None:
    if row is None:
        return None
    payload = row.get("record")
    if isinstance(payload, str):
        return AgentRunRecord.model_validate_json(payload)
    if isinstance(payload, Mapping):
        return AgentRunRecord.model_validate(payload)
    raise AgentRunRepositoryUnavailable("Agent Run row is malformed")


class PostgresAgentRunRepository:
    """Append-only durable adapter over caller-owned database transactions."""

    def __init__(self, transaction: TransactionRunner) -> None:
        self._transaction = transaction

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        try:
            row = self._transaction(
                lambda tx: tx.fetch_one(
                    MEMBERSHIP_SQL, (tenant_id, matter_id, principal_id)
                )
            )
            return row is not None and row.get("active") is True
        except AgentRunRepositoryUnavailable:
            raise
        except Exception:
            raise AgentRunRepositoryUnavailable(
                "membership store unavailable"
            ) from None

    def find_idempotent(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key: UUID
    ) -> tuple[str, AgentRunRecord] | None:
        try:
            row = self._transaction(
                lambda tx: tx.fetch_one(
                    IDEMPOTENCY_SQL, (tenant_id, matter_id, idempotency_key)
                )
            )
            if row is None:
                return None
            request_sha256 = row.get("request_sha256")
            record = _record(row)
            if not isinstance(request_sha256, str) or record is None:
                raise AgentRunRepositoryUnavailable("idempotency receipt is malformed")
            return request_sha256, record
        except AgentRunRepositoryUnavailable:
            raise
        except Exception:
            raise AgentRunRepositoryUnavailable(
                "idempotency store unavailable"
            ) from None

    def commit_new(
        self,
        *,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> AgentRunRecord:
        record = validate_write_evidence(record, audit)

        def operation(tx: SqlTransaction) -> AgentRunRecord:
            self._lock(
                tx, record.tenant_id, record.matter_id, record.request.idempotency_key
            )
            existing = tx.fetch_one(
                IDEMPOTENCY_SQL,
                (record.tenant_id, record.matter_id, record.request.idempotency_key),
            )
            if existing is not None:
                prior_hash = existing.get("request_sha256")
                prior = _record(existing)
                if prior_hash != request_sha256:
                    raise AgentRunIdempotencyConflict("idempotency bytes changed")
                if prior is None:
                    raise AgentRunRepositoryUnavailable("idempotent run is unavailable")
                return prior
            if record.version != 1:
                raise AgentRunVersionConflict("new Agent Run must begin at version 1")
            tx.execute(
                INSERT_IDENTITY_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    record.run_id,
                    record.request.request_id,
                    record.request.principal_id,
                    record.request.retry_of_run_id,
                    record.created_at,
                ),
            )
            self._insert_version(tx, record)
            self._insert_receipt_and_audit(
                tx,
                idempotency_key=record.request.idempotency_key,
                request_sha256=request_sha256,
                record=record,
                audit=audit,
            )
            return record

        return self._write(operation)

    def append_version(
        self,
        *,
        expected_version: int,
        idempotency_key: UUID,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> AgentRunRecord:
        record = validate_write_evidence(record, audit)

        def operation(tx: SqlTransaction) -> AgentRunRecord:
            self._lock(tx, record.tenant_id, record.matter_id, record.run_id)
            existing = tx.fetch_one(
                IDEMPOTENCY_SQL,
                (record.tenant_id, record.matter_id, idempotency_key),
            )
            if existing is not None:
                prior_hash = existing.get("request_sha256")
                prior = _record(existing)
                if (
                    prior_hash != request_sha256
                    or prior is None
                    or prior.run_id != record.run_id
                ):
                    raise AgentRunIdempotencyConflict("idempotency bytes changed")
                return prior
            current = _record(
                tx.fetch_one(
                    LOCK_CURRENT_RUN_SQL,
                    (record.tenant_id, record.matter_id, record.run_id),
                )
            )
            if current is None:
                raise AgentRunNotFound("Agent Run not found")
            if (
                current.version != expected_version
                or record.version != expected_version + 1
            ):
                raise AgentRunVersionConflict("Agent Run version is stale")
            if (
                current.tenant_id != record.tenant_id
                or current.matter_id != record.matter_id
                or current.run_id != record.run_id
                or current.request != record.request
            ):
                raise AgentRunVersionConflict("Agent Run immutable identity changed")
            self._insert_version(tx, record)
            self._insert_receipt_and_audit(
                tx,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                record=record,
                audit=audit,
            )
            return record

        return self._write(operation)

    def get(
        self, tenant_id: UUID, matter_id: UUID, run_id: UUID
    ) -> AgentRunRecord | None:
        try:
            return self._transaction(
                lambda tx: _record(
                    tx.fetch_one(CURRENT_RUN_SQL, (tenant_id, matter_id, run_id))
                )
            )
        except AgentRunRepositoryUnavailable:
            raise
        except Exception:
            raise AgentRunRepositoryUnavailable("Agent Run store unavailable") from None

    def list_for_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[AgentRunRecord, ...]:
        return self._read_records(LIST_RUNS_SQL, (tenant_id, matter_id))

    def history(
        self, tenant_id: UUID, matter_id: UUID, run_id: UUID
    ) -> tuple[AgentRunRecord, ...]:
        return self._read_records(RUN_HISTORY_SQL, (tenant_id, matter_id, run_id))

    def audit_events(self) -> tuple[AgentRunAuditEvent, ...]:
        try:
            rows = self._transaction(lambda tx: tx.fetch_all(AUDIT_EVENTS_SQL, ()))
            return tuple(AgentRunAuditEvent.model_validate(dict(row)) for row in rows)
        except Exception:
            raise AgentRunRepositoryUnavailable("Agent Run audit unavailable") from None

    def _read_records(
        self, sql: str, parameters: tuple[object, ...]
    ) -> tuple[AgentRunRecord, ...]:
        try:
            rows = self._transaction(lambda tx: tx.fetch_all(sql, parameters))
            records = tuple(_record(row) for row in rows)
            if any(record is None for record in records):
                raise AgentRunRepositoryUnavailable("Agent Run row is malformed")
            return cast(tuple[AgentRunRecord, ...], records)
        except AgentRunRepositoryUnavailable:
            raise
        except Exception:
            raise AgentRunRepositoryUnavailable("Agent Run store unavailable") from None

    @staticmethod
    def _lock(tx: SqlTransaction, tenant_id: UUID, matter_id: UUID, key: UUID) -> None:
        tx.execute(LOCK_SCOPE_SQL, (f"{tenant_id}:{matter_id}:{key}",))

    @staticmethod
    def _insert_version(tx: SqlTransaction, record: AgentRunRecord) -> None:
        request = record.request
        spec = request.agent_specification
        tx.execute(
            INSERT_RUN_SQL,
            (
                record.tenant_id,
                record.matter_id,
                record.run_id,
                record.version,
                record.schema_version,
                record.status.value,
                request.classification,
                request.public_synthetic,
                request.purpose,
                record.authorization.capability,
                record.authorization.decision_id,
                record.authorization.verifier_policy_version,
                record.authorization.revocation_revision,
                record.authorization.credential_digest,
                spec.spec_id,
                spec.spec_version,
                spec.spec_sha256,
                spec.deployment_revision,
                spec.deployment_sha256,
                request.requested_logical_route_id,
                request.prompt_template_sha256,
                request.output_schema_sha256,
                request.scoring_policy_sha256,
                request.snapshots.matter_snapshot_sha256,
                request.snapshots.corpus_snapshot_sha256,
                request.snapshots.authority_snapshot_sha256,
                request.snapshots.policy_snapshot_sha256,
                record.proposal_payload_sha256,
                canonical_sha256(record),
                _json(record),
                record.created_at,
                record.updated_at,
            ),
        )
        for attempt in record.attempts:
            tx.execute(
                INSERT_ATTEMPT_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    record.run_id,
                    record.version,
                    attempt.attempt_id,
                    attempt.attempt_number,
                    attempt.outcome.value,
                    attempt.error_code,
                    attempt.retryable,
                    attempt.started_at,
                    attempt.completed_at,
                ),
            )
        for call in record.tool_calls:
            tx.execute(
                INSERT_TOOL_CALL_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    record.run_id,
                    record.version,
                    call.tool_call_id,
                    call.sequence,
                    call.tool_id,
                    call.capability_decision_id,
                    call.arguments_sha256,
                    call.result_sha256,
                    call.outcome,
                    call.error_code,
                    call.started_at,
                    call.completed_at,
                ),
            )
        for recommendation in record.recommendations:
            tx.execute(
                INSERT_RECOMMENDATION_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    record.run_id,
                    record.version,
                    recommendation.recommendation_id,
                    recommendation.version,
                    recommendation.target_kind,
                    recommendation.target_id,
                    recommendation.proposed_output,
                    recommendation.score_total,
                    recommendation.confidence_basis_points,
                    recommendation.review_state.value,
                    canonical_sha256(recommendation),
                    _json(recommendation),
                ),
            )
        for challenge in record.challenges:
            tx.execute(
                INSERT_CHALLENGE_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    record.run_id,
                    record.version,
                    challenge.challenge_id,
                    challenge.version,
                    challenge.recommendation_id,
                    challenge.recommendation_version,
                    challenge.challenger_spec_sha256,
                    canonical_sha256(challenge.challenger_route),
                    challenge.blind_input_sha256,
                    challenge.independent_output_sha256,
                    challenge.authorization.principal_id,
                    challenge.authorization.decision_id,
                    challenge.authorization.verifier_policy_version,
                    challenge.authorization.revocation_revision,
                    challenge.authorization.credential_digest,
                    challenge.saw_challenged_conclusion,
                    challenge.outcome.value,
                    canonical_sha256(challenge),
                    _json(challenge),
                    challenge.created_at,
                ),
            )
        for disposition in record.dispositions:
            tx.execute(
                INSERT_DISPOSITION_SQL,
                (
                    record.tenant_id,
                    record.matter_id,
                    record.run_id,
                    record.version,
                    disposition.disposition_id,
                    disposition.version,
                    disposition.recommendation_id,
                    disposition.recommendation_version,
                    disposition.reviewer_principal_id,
                    disposition.capability_decision_id,
                    disposition.decision.value,
                    disposition.authorization.verifier_policy_version,
                    disposition.authorization.revocation_revision,
                    disposition.authorization.credential_digest,
                    disposition.creates_domain_record,
                    disposition.external_effect,
                    disposition.policy_revision,
                    canonical_sha256(disposition),
                    _json(disposition),
                    disposition.decided_at,
                ),
            )

    @staticmethod
    def _insert_receipt_and_audit(
        tx: SqlTransaction,
        *,
        idempotency_key: UUID,
        request_sha256: str,
        record: AgentRunRecord,
        audit: AgentRunAuditEvent,
    ) -> None:
        tx.execute(
            INSERT_IDEMPOTENCY_SQL,
            (
                record.tenant_id,
                record.matter_id,
                idempotency_key,
                request_sha256,
                record.run_id,
            ),
        )
        tx.execute(
            INSERT_AUDIT_SQL,
            (
                audit.tenant_id,
                audit.matter_id,
                audit.event_id,
                audit.run_id,
                audit.run_version,
                audit.correlation_id,
                audit.actor_principal_id,
                audit.action,
                audit.outcome,
                audit.subject_sha256,
                audit.occurred_at,
            ),
        )

    def _write(
        self, operation: Callable[[SqlTransaction], AgentRunRecord]
    ) -> AgentRunRecord:
        try:
            return self._transaction(operation)
        except (
            AgentRunIdempotencyConflict,
            AgentRunNotFound,
            AgentRunRepositoryUnavailable,
            AgentRunVersionConflict,
        ):
            raise
        except Exception:
            raise AgentRunRepositoryUnavailable(
                "Agent Run transaction failed"
            ) from None
