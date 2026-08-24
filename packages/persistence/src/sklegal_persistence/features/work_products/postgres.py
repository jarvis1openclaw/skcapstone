"""PostgreSQL adapter for atomic Work Product aggregate writes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol, TypeVar, cast
from uuid import UUID

from .models import (
    AuthorizationEvidence,
    WorkProductAggregate,
    WorkProductAuditEvent,
    WorkProductOutboxEvent,
)
from .repository import (
    WorkProductIdempotencyConflict,
    WorkProductRepositoryUnavailable,
    WorkProductVersionConflict,
    validate_write_evidence,
)

T = TypeVar("T")
EvidenceT = TypeVar("EvidenceT", WorkProductAuditEvent, WorkProductOutboxEvent)


class SqlTransaction(Protocol):
    def execute(self, sql: str, parameters: tuple[object, ...]) -> None: ...

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None: ...

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]: ...


TransactionRunner = Callable[[Callable[[SqlTransaction], T]], T]

LOCK_SCOPE_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
MEMBERSHIP_SQL = """
SELECT EXISTS (
    SELECT 1 FROM sklegal_legal.matter_memberships
    WHERE tenant_id = %s AND matter_id = %s AND principal_id = %s AND active
) AS allowed
"""
CLAIM_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM sklegal_legal.ledger_claim_identities
    WHERE tenant_id = %s AND matter_id = %s AND id = %s
) AS present
"""
AUTHORIZATION_SNAPSHOT_SQL = """
SELECT sklegal_identity.capability_revocation_snapshot(%s, %s) AS snapshot
"""
CURRENT_SQL = """
SELECT version.aggregate_payload
FROM sklegal_legal.work_product_feature_identities AS identity
JOIN sklegal_legal.work_product_feature_versions AS version
  ON version.tenant_id = identity.tenant_id
 AND version.matter_id = identity.matter_id
 AND version.work_product_id = identity.work_product_id
 AND version.aggregate_version = identity.current_aggregate_version
WHERE identity.tenant_id = %s
  AND identity.matter_id = %s
  AND identity.work_product_id = %s
"""
LIST_SQL = """
SELECT version.aggregate_payload
FROM sklegal_legal.work_product_feature_identities AS identity
JOIN sklegal_legal.work_product_feature_versions AS version
  ON version.tenant_id = identity.tenant_id
 AND version.matter_id = identity.matter_id
 AND version.work_product_id = identity.work_product_id
 AND version.aggregate_version = identity.current_aggregate_version
WHERE identity.tenant_id = %s AND identity.matter_id = %s
ORDER BY identity.work_product_id
"""
IDEMPOTENCY_SQL = """
SELECT request_sha256, aggregate_payload
FROM sklegal_legal.work_product_feature_idempotency
WHERE tenant_id = %s AND matter_id = %s AND idempotency_key = %s
"""
LOCK_CURRENT_SQL = """
SELECT current_aggregate_version
FROM sklegal_legal.work_product_feature_identities
WHERE tenant_id = %s AND matter_id = %s AND work_product_id = %s
FOR UPDATE
"""
INSERT_IDENTITY_SQL = """
INSERT INTO sklegal_legal.work_product_feature_identities
    (tenant_id, matter_id, work_product_id, current_aggregate_version, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s)
"""
UPDATE_IDENTITY_SQL = """
UPDATE sklegal_legal.work_product_feature_identities
SET current_aggregate_version = %s, updated_at = %s
WHERE tenant_id = %s AND matter_id = %s AND work_product_id = %s
"""
INSERT_VERSION_SQL = """
INSERT INTO sklegal_legal.work_product_feature_versions
    (tenant_id, matter_id, work_product_id, aggregate_version,
     current_version_id, current_version_number, current_content_sha256,
     status, aggregate_sha256, aggregate_payload, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
"""
INSERT_IDEMPOTENCY_SQL = """
INSERT INTO sklegal_legal.work_product_feature_idempotency
    (tenant_id, matter_id, idempotency_key, request_sha256,
     work_product_id, aggregate_version, aggregate_payload, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
"""
INSERT_AUDIT_SQL = """
INSERT INTO sklegal_audit.work_product_feature_events
    (tenant_id, matter_id, event_id, work_product_id, aggregate_version,
     correlation_id, actor_principal_id, action, outcome, subject_sha256, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_OUTBOX_SQL = """
INSERT INTO sklegal_audit.work_product_feature_outbox
    (tenant_id, matter_id, outbox_id, event_id, work_product_id,
     aggregate_version, topic, payload_sha256, available_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
AUDIT_SQL = """
SELECT jsonb_build_object(
    'eventId', event_id, 'tenantId', tenant_id, 'matterId', matter_id,
    'workProductId', work_product_id, 'aggregateVersion', aggregate_version,
    'correlationId', correlation_id, 'actorPrincipalId', actor_principal_id,
    'action', action, 'outcome', outcome, 'subjectSha256', subject_sha256,
    'occurredAt', occurred_at
) AS payload
FROM sklegal_audit.work_product_feature_events
ORDER BY occurred_at, event_id
"""
OUTBOX_SQL = """
SELECT jsonb_build_object(
    'outboxId', outbox_id, 'eventId', event_id, 'tenantId', tenant_id,
    'matterId', matter_id, 'workProductId', work_product_id,
    'aggregateVersion', aggregate_version, 'topic', topic,
    'payloadSha256', payload_sha256, 'availableAt', available_at
) AS payload
FROM sklegal_audit.work_product_feature_outbox
ORDER BY available_at, outbox_id
"""


def _json(value: WorkProductAggregate) -> str:
    return json.dumps(
        value.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload(row: Mapping[str, object] | None) -> WorkProductAggregate | None:
    if row is None:
        return None
    value = row.get("aggregate_payload")
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return WorkProductAggregate.model_validate(value)


class PostgresWorkProductRepository:
    """Persist one aggregate version, receipt, audit, and outbox atomically."""

    def __init__(
        self, *, transaction: TransactionRunner, current_policy_revision: str
    ) -> None:
        self._transaction = transaction
        self._policy_revision = current_policy_revision

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        row = self._read_one(MEMBERSHIP_SQL, (tenant_id, matter_id, principal_id))
        return bool(row and row.get("allowed"))

    def current_policy_revision(self, tenant_id: UUID, matter_id: UUID) -> str:
        del tenant_id, matter_id
        return self._policy_revision

    def claim_exists(self, tenant_id: UUID, matter_id: UUID, claim_id: UUID) -> bool:
        row = self._read_one(CLAIM_EXISTS_SQL, (tenant_id, matter_id, claim_id))
        return bool(row and row.get("present"))

    def authorization_is_active(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        evidence: AuthorizationEvidence,
        at: datetime,
    ) -> bool:
        if evidence.tenant_id != tenant_id or evidence.matter_id != matter_id:
            return False
        if not evidence.authorized_at <= at < evidence.expires_at:
            return False
        if evidence.policy_revision != self._policy_revision:
            return False
        requested = [
            evidence.credential_digest,
            *evidence.ancestor_credential_digests,
        ]
        row = self._read_one(AUTHORIZATION_SNAPSHOT_SQL, (tenant_id, requested))
        try:
            if row is None:
                raise ValueError
            snapshot = row.get("snapshot")
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)
            if not isinstance(snapshot, Mapping):
                raise ValueError
            revision = snapshot.get("revision")
            revoked = snapshot.get("revoked_credential_digests")
            if (
                not isinstance(revision, str)
                or len(revision) != 64
                or any(character not in "0123456789abcdef" for character in revision)
                or not isinstance(revoked, list)
                or any(
                    not isinstance(digest, str) or digest not in requested
                    for digest in revoked
                )
                or len(revoked) != len(set(revoked))
            ):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise WorkProductRepositoryUnavailable(
                "malformed authorization snapshot"
            ) from None
        return revision == evidence.revocation_revision and not revoked

    def get(
        self, tenant_id: UUID, matter_id: UUID, work_product_id: UUID
    ) -> WorkProductAggregate | None:
        return _payload(
            self._read_one(CURRENT_SQL, (tenant_id, matter_id, work_product_id))
        )

    def list_for_matter(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[WorkProductAggregate, ...]:
        try:
            rows = self._transaction(
                lambda tx: tx.fetch_all(LIST_SQL, (tenant_id, matter_id))
            )
            records = tuple(_payload(row) for row in rows)
            if any(item is None for item in records):
                raise WorkProductRepositoryUnavailable("malformed Work Product row")
            return cast(tuple[WorkProductAggregate, ...], records)
        except WorkProductRepositoryUnavailable:
            raise
        except Exception:
            raise WorkProductRepositoryUnavailable(
                "Work Product store is unavailable"
            ) from None

    def find_idempotent(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        idempotency_key: UUID,
    ) -> tuple[str, WorkProductAggregate] | None:
        row = self._read_one(IDEMPOTENCY_SQL, (tenant_id, matter_id, idempotency_key))
        if row is None:
            return None
        aggregate = _payload(row)
        request_sha256 = row.get("request_sha256")
        if aggregate is None or not isinstance(request_sha256, str):
            raise WorkProductRepositoryUnavailable("malformed idempotency receipt")
        return request_sha256, aggregate

    def commit(
        self,
        *,
        expected_aggregate_version: int | None,
        idempotency_key: UUID,
        request_sha256: str,
        aggregate: WorkProductAggregate,
        audit: WorkProductAuditEvent,
        outbox: WorkProductOutboxEvent,
    ) -> WorkProductAggregate:
        validate_write_evidence(aggregate, audit, outbox)

        def operation(tx: SqlTransaction) -> WorkProductAggregate:
            tx.execute(
                LOCK_SCOPE_SQL,
                (f"{aggregate.tenant_id}:{aggregate.matter_id}:{idempotency_key}",),
            )
            existing = tx.fetch_one(
                IDEMPOTENCY_SQL,
                (aggregate.tenant_id, aggregate.matter_id, idempotency_key),
            )
            if existing is not None:
                prior = _payload(existing)
                if existing.get("request_sha256") != request_sha256 or prior is None:
                    raise WorkProductIdempotencyConflict("idempotency bytes changed")
                return prior
            current = tx.fetch_one(
                LOCK_CURRENT_SQL,
                (aggregate.tenant_id, aggregate.matter_id, aggregate.work_product_id),
            )
            if expected_aggregate_version is None:
                if current is not None or aggregate.aggregate_version != 1:
                    raise WorkProductVersionConflict("Work Product already exists")
                tx.execute(
                    INSERT_IDENTITY_SQL,
                    (
                        aggregate.tenant_id,
                        aggregate.matter_id,
                        aggregate.work_product_id,
                        aggregate.aggregate_version,
                        aggregate.created_at,
                        aggregate.updated_at,
                    ),
                )
            elif (
                current is None
                or current.get("current_aggregate_version")
                != expected_aggregate_version
                or aggregate.aggregate_version != expected_aggregate_version + 1
            ):
                raise WorkProductVersionConflict("Work Product aggregate is stale")
            payload = _json(aggregate)
            version = aggregate.current_version
            tx.execute(
                INSERT_VERSION_SQL,
                (
                    aggregate.tenant_id,
                    aggregate.matter_id,
                    aggregate.work_product_id,
                    aggregate.aggregate_version,
                    version.version_id,
                    version.version_number,
                    version.content_sha256,
                    aggregate.status.value,
                    audit.subject_sha256,
                    payload,
                    aggregate.updated_at,
                ),
            )
            if expected_aggregate_version is not None:
                tx.execute(
                    UPDATE_IDENTITY_SQL,
                    (
                        aggregate.aggregate_version,
                        aggregate.updated_at,
                        aggregate.tenant_id,
                        aggregate.matter_id,
                        aggregate.work_product_id,
                    ),
                )
            tx.execute(
                INSERT_IDEMPOTENCY_SQL,
                (
                    aggregate.tenant_id,
                    aggregate.matter_id,
                    idempotency_key,
                    request_sha256,
                    aggregate.work_product_id,
                    aggregate.aggregate_version,
                    payload,
                    aggregate.updated_at,
                ),
            )
            tx.execute(
                INSERT_AUDIT_SQL,
                (
                    audit.tenant_id,
                    audit.matter_id,
                    audit.event_id,
                    audit.work_product_id,
                    audit.aggregate_version,
                    audit.correlation_id,
                    audit.actor_principal_id,
                    audit.action,
                    audit.outcome,
                    audit.subject_sha256,
                    audit.occurred_at,
                ),
            )
            tx.execute(
                INSERT_OUTBOX_SQL,
                (
                    outbox.tenant_id,
                    outbox.matter_id,
                    outbox.outbox_id,
                    outbox.event_id,
                    outbox.work_product_id,
                    outbox.aggregate_version,
                    outbox.topic,
                    outbox.payload_sha256,
                    outbox.available_at,
                ),
            )
            return aggregate

        try:
            return self._transaction(operation)
        except (WorkProductIdempotencyConflict, WorkProductVersionConflict):
            raise
        except Exception:
            raise WorkProductRepositoryUnavailable(
                "Work Product transaction failed"
            ) from None

    def audit_events(self) -> tuple[WorkProductAuditEvent, ...]:
        return self._read_models(AUDIT_SQL, WorkProductAuditEvent)

    def outbox_events(self) -> tuple[WorkProductOutboxEvent, ...]:
        return self._read_models(OUTBOX_SQL, WorkProductOutboxEvent)

    def _read_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        try:
            return self._transaction(lambda tx: tx.fetch_one(sql, parameters))
        except Exception:
            raise WorkProductRepositoryUnavailable(
                "Work Product store is unavailable"
            ) from None

    def _read_models(
        self,
        sql: str,
        model: type[EvidenceT],
    ) -> tuple[EvidenceT, ...]:
        try:
            rows = self._transaction(lambda tx: tx.fetch_all(sql, ()))
            values: list[EvidenceT] = []
            for row in rows:
                payload = row.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                values.append(model.model_validate(payload))
            return tuple(values)
        except Exception:
            raise WorkProductRepositoryUnavailable(
                "Work Product evidence store is unavailable"
            ) from None
