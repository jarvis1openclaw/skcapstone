"""PostgreSQL adapter for governed corpus reads and atomic writes."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Protocol, TypeVar
from uuid import UUID

from .models import (
    Classification,
    CorpusAuditEvent,
    CorpusOutboxRecord,
    CorpusProjectionCommand,
    CorpusSourceVersion,
    ProjectionRegistryState,
    ProjectionState,
    RetrievalQuery,
    canonical_sha256,
)
from .repository import (
    GovernedCorpusIdempotencyConflict,
    GovernedCorpusRepositoryError,
    GovernedCorpusRepositoryUnavailable,
    GovernedCorpusVersionConflict,
    RankedPage,
    RankedSource,
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
CORE_REGISTRY_SQL = """
SELECT tenant_id, matter_id, release_id, projection_generation,
       core_watermark, policy_revision, rights_revision, recorded_at
FROM sklegal_governed_corpus.projection_registry
WHERE tenant_id = %s AND matter_id = %s
ORDER BY projection_generation DESC
LIMIT 1
"""
RETRIEVAL_STATE_SQL = """
SELECT record FROM sklegal_governed_corpus.projection_state
WHERE tenant_id = %s AND matter_id = %s
ORDER BY projection_generation DESC
LIMIT 1
"""
SEARCH_SQL = """
SELECT record, score, full_text_rank, vector_distance
FROM sklegal_governed_corpus.search_v1(
    %s, %s, %s, %s, %s::public.vector, %s, %s, %s, %s, %s, %s,
    %s, %s, %s
)
"""
CURRENT_SOURCE_SQL = """
SELECT record FROM sklegal_governed_corpus.current_source_v1(
    %s, %s, %s, %s, %s, %s, %s, %s
)
"""
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
IDEMPOTENCY_SQL = """
SELECT request_sha256, response_record
FROM sklegal_governed_corpus.idempotency_receipts
WHERE tenant_id = %s AND matter_id = %s AND idempotency_key_sha256 = %s
"""
SOURCE_BY_VERSION_SQL = """
SELECT record FROM sklegal_governed_corpus.source_versions
WHERE tenant_id = %s AND matter_id = %s AND source_version_id = %s
FOR SHARE
"""
CURRENT_BY_SOURCE_SQL = """
SELECT record FROM sklegal_governed_corpus.source_versions candidate
WHERE tenant_id = %s AND matter_id = %s AND source_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM sklegal_governed_corpus.source_versions successor
      WHERE successor.tenant_id = candidate.tenant_id
        AND successor.matter_id = candidate.matter_id
        AND successor.supersedes_source_version_id = candidate.source_version_id
  )
FOR SHARE
"""
INSERT_SOURCE_SQL = """
INSERT INTO sklegal_governed_corpus.source_versions
    (tenant_id, matter_id, source_id, source_version_id, source_version,
     release_id, projection_generation, classification, rights_revision,
     permitted_principal_ids, source_sha256, chunk_sha256, exact_span,
     supersedes_source_version_id, record,
     record_sha256, authorization_decision_id, recorded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s::jsonb, %s, %s, %s)
"""
INSERT_IDEMPOTENCY_SQL = """
INSERT INTO sklegal_governed_corpus.idempotency_receipts
    (tenant_id, matter_id, idempotency_key_sha256, request_sha256,
     response_record, created_at)
VALUES (%s, %s, %s, %s, %s::jsonb, %s)
"""
INSERT_AUDIT_SQL = """
INSERT INTO sklegal_governed_corpus.audit_events
    (tenant_id, matter_id, audit_id, source_version_id, action,
     actor_principal_id, authorization_decision_id, policy_decision_id,
     policy_revision, request_sha256, source_sha256, correlation_id, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_OUTBOX_SQL = """
INSERT INTO sklegal_governed_corpus.outbox
    (tenant_id, matter_id, outbox_id, audit_id, source_version_id, topic,
     payload_sha256, qdrant_dispatch_allowed, falkordb_dispatch_allowed,
     created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
OUTBOX_PROJECTION_COMMANDS_SQL = """
SELECT outbox.outbox_id AS command_id, outbox.payload_sha256,
       outbox.created_at AS projected_at, source.record
FROM sklegal_governed_corpus.outbox AS outbox
JOIN sklegal_governed_corpus.source_versions AS source
  ON source.tenant_id = outbox.tenant_id
 AND source.matter_id = outbox.matter_id
 AND source.source_version_id = outbox.source_version_id
WHERE outbox.tenant_id = %s AND outbox.matter_id = %s
ORDER BY outbox.created_at, outbox.outbox_id
"""
PROJECTION_COMMAND_SQL = """
SELECT operation, payload_sha256
FROM sklegal_governed_corpus.projection_commands
WHERE tenant_id = %s AND matter_id = %s AND command_id = %s
"""
INSERT_PROJECTION_COMMAND_SQL = """
INSERT INTO sklegal_governed_corpus.projection_commands
    (tenant_id, matter_id, command_id, source_version_id, operation,
     payload_sha256, projected_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""
UPSERT_SOURCE_PROJECTION_SQL = """
INSERT INTO sklegal_governed_corpus.source_projections
    (tenant_id, matter_id, source_id, source_version_id, source_version,
     release_id, projection_generation, classification, rights_revision,
     permitted_principal_ids, source_sha256, chunk_sha256, exact_span,
     search_document, embedding, supersedes_source_version_id, record,
     record_sha256, recorded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        to_tsvector('english', %s), %s::public.vector, %s, %s::jsonb,
        %s, %s)
ON CONFLICT (tenant_id, matter_id, source_version_id) DO UPDATE
SET source_id = EXCLUDED.source_id,
    source_version = EXCLUDED.source_version,
    release_id = EXCLUDED.release_id,
    projection_generation = EXCLUDED.projection_generation,
    classification = EXCLUDED.classification,
    rights_revision = EXCLUDED.rights_revision,
    permitted_principal_ids = EXCLUDED.permitted_principal_ids,
    source_sha256 = EXCLUDED.source_sha256,
    chunk_sha256 = EXCLUDED.chunk_sha256,
    exact_span = EXCLUDED.exact_span,
    search_document = EXCLUDED.search_document,
    embedding = EXCLUDED.embedding,
    supersedes_source_version_id = EXCLUDED.supersedes_source_version_id,
    record = EXCLUDED.record,
    record_sha256 = EXCLUDED.record_sha256,
    recorded_at = EXCLUDED.recorded_at
"""
DELETE_SOURCE_PROJECTION_SQL = """
DELETE FROM sklegal_governed_corpus.source_projections
WHERE tenant_id = %s AND matter_id = %s AND source_version_id = %s
"""
UPSERT_RETRIEVAL_STATE_SQL = """
INSERT INTO sklegal_governed_corpus.projection_state
    (tenant_id, matter_id, projection_generation, release_id,
     backend_watermark, core_watermark, record, recorded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (tenant_id, matter_id, projection_generation) DO UPDATE
SET release_id = EXCLUDED.release_id,
    backend_watermark = EXCLUDED.backend_watermark,
    core_watermark = EXCLUDED.core_watermark,
    record = EXCLUDED.record,
    recorded_at = EXCLUDED.recorded_at
"""
CLEAR_RETRIEVAL_SCOPE_SQL = """
DELETE FROM sklegal_governed_corpus.projection_commands
WHERE tenant_id = %s AND matter_id = %s;
DELETE FROM sklegal_governed_corpus.source_projections
WHERE tenant_id = %s AND matter_id = %s;
DELETE FROM sklegal_governed_corpus.projection_state
WHERE tenant_id = %s AND matter_id = %s
"""


def _json(model: CorpusSourceVersion) -> str:
    return json.dumps(
        model.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )


def _vector(value: Sequence[float] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(list(value), separators=(",", ":"))


def _source(value: object) -> CorpusSourceVersion:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise GovernedCorpusRepositoryUnavailable("malformed corpus record")
    return CorpusSourceVersion.model_validate(value)


def _number(value: object, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GovernedCorpusRepositoryUnavailable("malformed corpus rank response")
    result = float(value)
    if not math.isfinite(result):
        raise GovernedCorpusRepositoryUnavailable("malformed corpus rank response")
    return result


class _PostgresBoundary:
    def __init__(self, transaction: TransactionRunner) -> None:
        self._transaction = transaction

    def _run(self, callback: Callable[[SqlTransaction], T]) -> T:
        try:
            return self._transaction(callback)
        except GovernedCorpusRepositoryError:
            raise
        except Exception:
            raise GovernedCorpusRepositoryUnavailable(
                "governed corpus store is unavailable"
            ) from None


class PostgresGovernedCorpusCoreRepository(_PostgresBoundary):
    """Canonical core custody using one explicit core connection."""

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        row = self._run(
            lambda tx: tx.fetch_one(
                MEMBERSHIP_SQL, (tenant_id, matter_id, principal_id)
            )
        )
        return bool(row and row.get("allowed"))

    def projection_registry(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ProjectionRegistryState | None:
        row = self._run(
            lambda tx: tx.fetch_one(CORE_REGISTRY_SQL, (tenant_id, matter_id))
        )
        if row is None:
            return None
        return ProjectionRegistryState.model_validate(row)

    def get_current_source(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
        source_id: str,
        classification_ceiling: Classification,
        rights_revision: str,
        release_id: str,
        projection_generation: int,
    ) -> CorpusSourceVersion | None:
        row = self._run(
            lambda tx: tx.fetch_one(
                CURRENT_SOURCE_SQL,
                (
                    tenant_id,
                    matter_id,
                    principal_id,
                    source_id,
                    int(classification_ceiling),
                    rights_revision,
                    release_id,
                    projection_generation,
                ),
            )
        )
        return None if row is None else _source(row.get("record"))

    def commit_source(
        self,
        *,
        idempotency_key_sha256: str,
        request_sha256: str,
        source: CorpusSourceVersion,
        audit: CorpusAuditEvent,
        outbox: CorpusOutboxRecord,
    ) -> CorpusSourceVersion:
        validate_write_evidence(source, audit, outbox)

        def transaction(tx: SqlTransaction) -> CorpusSourceVersion:
            scope = f"{source.tenant_id}:{source.matter_id}:{idempotency_key_sha256}"
            tx.execute(LOCK_SQL, (scope,))
            prior = tx.fetch_one(
                IDEMPOTENCY_SQL,
                (source.tenant_id, source.matter_id, idempotency_key_sha256),
            )
            if prior is not None:
                if prior.get("request_sha256") != request_sha256:
                    raise GovernedCorpusIdempotencyConflict(
                        "idempotency key is bound to other bytes"
                    )
                return _source(prior.get("response_record"))
            predecessor = source.supersedes_source_version_id
            if predecessor is not None:
                row = tx.fetch_one(
                    SOURCE_BY_VERSION_SQL,
                    (source.tenant_id, source.matter_id, predecessor),
                )
                prior_source = None if row is None else _source(row.get("record"))
                if prior_source is None or prior_source.source_id != source.source_id:
                    raise GovernedCorpusVersionConflict("supersession lineage is stale")
            else:
                current = tx.fetch_one(
                    CURRENT_BY_SOURCE_SQL,
                    (source.tenant_id, source.matter_id, source.source_id),
                )
                if current is not None:
                    raise GovernedCorpusVersionConflict("current source already exists")
            payload = _json(source)
            payload_sha256 = canonical_sha256(source)
            tx.execute(
                INSERT_SOURCE_SQL,
                (
                    source.tenant_id,
                    source.matter_id,
                    source.source_id,
                    source.source_version_id,
                    source.source_version,
                    source.release_id,
                    source.projection_generation,
                    int(source.classification),
                    source.rights_revision,
                    list(source.permitted_principal_ids),
                    source.source_sha256,
                    source.chunk_sha256,
                    source.exact_span,
                    source.supersedes_source_version_id,
                    payload,
                    payload_sha256,
                    source.authorization_decision_id,
                    source.recorded_at,
                ),
            )
            tx.execute(
                INSERT_IDEMPOTENCY_SQL,
                (
                    source.tenant_id,
                    source.matter_id,
                    idempotency_key_sha256,
                    request_sha256,
                    payload,
                    source.recorded_at,
                ),
            )
            tx.execute(
                INSERT_AUDIT_SQL,
                (
                    audit.tenant_id,
                    audit.matter_id,
                    audit.audit_id,
                    audit.source_version_id,
                    audit.action,
                    audit.actor_principal_id,
                    audit.authorization_decision_id,
                    audit.policy_decision_id,
                    audit.policy_revision,
                    audit.request_sha256,
                    audit.source_sha256,
                    audit.correlation_id,
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
                    outbox.source_version_id,
                    outbox.topic,
                    outbox.payload_sha256,
                    outbox.qdrant_dispatch_allowed,
                    outbox.falkordb_dispatch_allowed,
                    outbox.created_at,
                ),
            )
            return source

        return self._run(transaction)

    def projection_commands(
        self, tenant_id: UUID, matter_id: UUID
    ) -> tuple[CorpusProjectionCommand, ...]:
        registry = self.projection_registry(tenant_id, matter_id)
        if registry is None:
            raise GovernedCorpusRepositoryUnavailable("projection registry is unknown")
        rows = self._run(
            lambda tx: tx.fetch_all(
                OUTBOX_PROJECTION_COMMANDS_SQL, (tenant_id, matter_id)
            )
        )
        commands: list[CorpusProjectionCommand] = []
        for row in rows:
            if set(row) != {
                "command_id",
                "payload_sha256",
                "projected_at",
                "record",
            }:
                raise GovernedCorpusRepositoryUnavailable(
                    "malformed outbox projection command"
                )
            source = _source(row["record"])
            commands.append(
                CorpusProjectionCommand.model_validate(
                    {
                        "command_id": row["command_id"],
                        "operation": (
                            "supersede"
                            if source.supersedes_source_version_id is not None
                            else "create"
                        ),
                        "source": source,
                        "payload_sha256": row["payload_sha256"],
                        "core_watermark": registry.core_watermark,
                        "projected_at": row["projected_at"],
                    }
                )
            )
        return tuple(commands)


class PostgresGovernedCorpusRetrievalRepository(_PostgresBoundary):
    """Rebuildable search projection using one explicit retrieval connection."""

    def projection_state(
        self, tenant_id: UUID, matter_id: UUID
    ) -> ProjectionState | None:
        row = self._run(
            lambda tx: tx.fetch_one(RETRIEVAL_STATE_SQL, (tenant_id, matter_id))
        )
        if row is None:
            return None
        return ProjectionState.model_validate(row.get("record"))

    def retrieve(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        principal_id: UUID,
        query: RetrievalQuery,
    ) -> RankedPage:
        rows = self._run(
            lambda tx: tx.fetch_all(
                SEARCH_SQL,
                (
                    tenant_id,
                    matter_id,
                    principal_id,
                    query.query,
                    _vector(query.query_embedding),
                    query.mode.value,
                    query.expected_release_id,
                    query.expected_projection_generation,
                    int(query.classification_ceiling),
                    query.rights_revision,
                    query.snapshot_at,
                    query.after_score,
                    query.after_source_version_id,
                    query.max_results + 1,
                ),
            )
        )
        ranked: list[RankedSource] = []
        for row in rows:
            if set(row) != {"record", "score", "full_text_rank", "vector_distance"}:
                raise GovernedCorpusRepositoryUnavailable(
                    "malformed corpus rank response"
                )
            ranked.append(
                RankedSource(
                    source=_source(row["record"]),
                    score=_number(row["score"]) or 0.0,
                    full_text_rank=_number(row["full_text_rank"], optional=True),
                    vector_distance=_number(row["vector_distance"], optional=True),
                )
            )
        if len(ranked) > query.max_results + 1:
            raise GovernedCorpusRepositoryUnavailable("rank bound was exceeded")
        return RankedPage(
            rows=tuple(ranked[: query.max_results]),
            has_more=len(ranked) > query.max_results,
        )

    @staticmethod
    def _apply(tx: SqlTransaction, command: CorpusProjectionCommand) -> None:
        source = command.source
        prior = tx.fetch_one(
            PROJECTION_COMMAND_SQL,
            (source.tenant_id, source.matter_id, command.command_id),
        )
        if prior is not None:
            if (
                prior.get("operation") != command.operation
                or prior.get("payload_sha256") != command.payload_sha256
            ):
                raise GovernedCorpusVersionConflict(
                    "projection command is bound to other bytes"
                )
            return
        tx.execute(
            INSERT_PROJECTION_COMMAND_SQL,
            (
                source.tenant_id,
                source.matter_id,
                command.command_id,
                source.source_version_id,
                command.operation,
                command.payload_sha256,
                command.projected_at,
            ),
        )
        if command.operation == "revoke":
            tx.execute(
                DELETE_SOURCE_PROJECTION_SQL,
                (source.tenant_id, source.matter_id, source.source_version_id),
            )
        else:
            payload = _json(source)
            tx.execute(
                UPSERT_SOURCE_PROJECTION_SQL,
                (
                    source.tenant_id,
                    source.matter_id,
                    source.source_id,
                    source.source_version_id,
                    source.source_version,
                    source.release_id,
                    source.projection_generation,
                    int(source.classification),
                    source.rights_revision,
                    list(source.permitted_principal_ids),
                    source.source_sha256,
                    source.chunk_sha256,
                    source.exact_span,
                    f"{source.title} {source.exact_span}",
                    _vector(source.embedding),
                    source.supersedes_source_version_id,
                    payload,
                    command.payload_sha256,
                    source.recorded_at,
                ),
            )
        state = ProjectionState(
            release_id=source.release_id,
            projection_generation=source.projection_generation,
            backend_watermark=command.core_watermark,
            core_watermark=command.core_watermark,
            lag_events=0,
            lag_seconds=0,
            max_lag_events=0,
            max_lag_seconds=0,
        )
        tx.execute(
            UPSERT_RETRIEVAL_STATE_SQL,
            (
                source.tenant_id,
                source.matter_id,
                source.projection_generation,
                source.release_id,
                command.core_watermark,
                command.core_watermark,
                json.dumps(
                    state.model_dump(mode="json", by_alias=True),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                command.projected_at,
            ),
        )

    def apply_projection(self, command: CorpusProjectionCommand) -> None:
        self._run(lambda tx: self._apply(tx, command))

    def rebuild_projection(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        commands: Iterable[CorpusProjectionCommand],
    ) -> None:
        ordered = tuple(
            sorted(commands, key=lambda item: (item.projected_at, item.command_id))
        )
        if any(
            (item.source.tenant_id, item.source.matter_id) != (tenant_id, matter_id)
            for item in ordered
        ):
            raise GovernedCorpusVersionConflict("projection rebuild scope drift")

        def rebuild(tx: SqlTransaction) -> None:
            tx.execute(
                CLEAR_RETRIEVAL_SCOPE_SQL,
                (tenant_id, matter_id, tenant_id, matter_id, tenant_id, matter_id),
            )
            for command in ordered:
                self._apply(tx, command)

        self._run(rebuild)


__all__ = [
    "CURRENT_SOURCE_SQL",
    "CLEAR_RETRIEVAL_SCOPE_SQL",
    "CORE_REGISTRY_SQL",
    "IDEMPOTENCY_SQL",
    "INSERT_AUDIT_SQL",
    "INSERT_IDEMPOTENCY_SQL",
    "INSERT_OUTBOX_SQL",
    "INSERT_PROJECTION_COMMAND_SQL",
    "INSERT_SOURCE_SQL",
    "MEMBERSHIP_SQL",
    "OUTBOX_PROJECTION_COMMANDS_SQL",
    "PROJECTION_COMMAND_SQL",
    "RETRIEVAL_STATE_SQL",
    "PostgresGovernedCorpusCoreRepository",
    "PostgresGovernedCorpusRetrievalRepository",
    "SEARCH_SQL",
    "UPSERT_RETRIEVAL_STATE_SQL",
    "UPSERT_SOURCE_PROJECTION_SQL",
]
