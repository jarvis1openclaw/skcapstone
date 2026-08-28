"""PostgreSQL adapter for the joined Matter activity projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sklegal_api.features.matter_activity.contracts import (
    ActivityExportProposalRead,
    ActivitySourceRead,
    ActivityTraceRead,
    ActivityWatermarkRead,
    MatterActivityItemRead,
)
from sklegal_api.features.matter_activity.store import (
    ActivityAuditFact,
    ActivityIdempotencyConflict,
    ActivityOutboxMessage,
    ActivityStoreUnavailable,
)


class SqlSession(Protocol):
    def execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]: ...


type SessionFactory = Callable[[], AbstractContextManager[SqlSession]]


@dataclass(frozen=True, slots=True)
class ActivityProjectionRecord:
    """One verified source fact projected from one exact Tenant audit event."""

    tenant_id: UUID
    matter_id: UUID
    audit_event_id: UUID
    event_sequence: int
    event_sha256: str
    source_kind: str
    source_id: UUID
    source_version: int
    source_sha256: str
    source_status: str
    source_recorded_at: datetime
    corrects_source_id: UUID | None
    superseded_by_source_id: UUID | None
    superseded_by_source_version: int | None
    causation_id: UUID | None
    workflow_reference_id: UUID | None
    agent_run_id: UUID | None
    tool_call_id: UUID | None
    idempotency_key_sha256: str
    request_sha256: str
    projection_audit_event_id: UUID
    projected_at: datetime


_PROJECT_EVENT_SQL = """
SELECT sklegal_activity.project_event(
    %(tenant_id)s, %(matter_id)s, %(audit_event_id)s,
    %(event_sequence)s, %(event_sha256)s, %(source_kind)s,
    %(source_id)s, %(source_version)s, %(source_sha256)s,
    %(source_status)s, %(source_recorded_at)s,
    %(corrects_source_id)s, %(superseded_by_source_id)s,
    %(superseded_by_source_version)s,
    %(causation_id)s, %(workflow_reference_id)s, %(agent_run_id)s,
    %(tool_call_id)s, %(idempotency_key_sha256)s, %(request_sha256)s,
    %(projection_audit_event_id)s, %(projected_at)s
) AS projected;
"""

_PAGE_SQL = """
SELECT
    entry.audit_event_id AS activity_id,
    entry.tenant_id,
    entry.matter_id,
    entry.event_sequence,
    entry.event_sha256,
    entry.previous_event_sha256,
    entry.action,
    entry.boundary,
    entry.outcome,
    entry.reason_code,
    entry.occurred_at,
    entry.recorded_at,
    entry.actor_principal_id,
    entry.authorization_decision_id,
    entry.policy_decision_id,
    jsonb_build_object(
        'correlationId', entry.correlation_id,
        'causationId', entry.causation_id,
        'runId', entry.run_id,
        'workflowReferenceId', entry.workflow_reference_id,
        'agentRunId', entry.agent_run_id,
        'toolCallId', entry.tool_call_id
    ) AS trace,
    jsonb_build_object(
        'kind', entry.source_kind,
        'sourceId', entry.source_id,
        'sourceVersion', entry.source_version,
        'sourceSha256', entry.source_sha256,
        'status', entry.source_status,
        'recordedAt', entry.source_recorded_at,
        'correctsSourceId', entry.corrects_source_id,
        'supersededBySourceId', entry.superseded_by_source_id,
        'supersededBySourceVersion', entry.superseded_by_source_version
    ) AS source
FROM sklegal_activity.entries AS entry
WHERE entry.tenant_id = %(tenant_id)s
  AND entry.matter_id = %(matter_id)s
  AND entry.event_sequence > %(after_sequence)s
  AND entry.event_sequence <= %(snapshot_sequence)s
ORDER BY entry.event_sequence, entry.audit_event_id
LIMIT %(row_limit)s;
"""

_WATERMARK_SQL = """
SELECT
    head.last_event_sequence AS tenant_head_sequence,
    head.last_event_sha256 AS tenant_head_sha256,
    watermark.event_sequence AS projected_sequence,
    watermark.event_sha256 AS projected_event_sha256
FROM sklegal_audit.chain_heads AS head
JOIN sklegal_audit.projection_watermarks AS watermark
  ON watermark.tenant_id = head.tenant_id
 AND watermark.projection = 'matter_activity.v1'
WHERE head.tenant_id = %(tenant_id)s;
"""

_SUPERSESSION_GRAPH_SQL = """
SELECT sklegal_activity.supersession_graph_is_valid(
    %(tenant_id)s, %(matter_id)s
) AS valid;
"""

_APPEND_EXPORT_SQL = """
SELECT sklegal_activity.propose_export(
    %(proposal_id)s, %(tenant_id)s, %(matter_id)s, %(title)s,
    %(first_event_sequence)s, %(last_event_sequence)s,
    %(item_count)s, %(event_ids)s, %(event_sha256s)s,
    %(selection_sha256)s, %(content_sha256)s,
    %(projected_sequence)s, %(projected_event_sha256)s,
    %(tenant_head_sequence)s, %(tenant_head_sha256)s,
    %(proposed_by_principal_id)s, %(authorization_decision_id)s,
    %(policy_decision_id)s, %(policy_revision)s,
    %(idempotency_key_sha256)s,
    %(request_sha256)s, %(audit_fact_id)s, %(outbox_id)s,
    %(created_at)s
) AS result;
"""


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ActivityStoreUnavailable("activity row is invalid")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ActivityStoreUnavailable("activity row is invalid")
    return value


def _item(row: Mapping[str, object]) -> MatterActivityItemRead:
    try:
        return MatterActivityItemRead.model_validate(
            {
                "activity_id": row["activity_id"],
                "tenant_id": row["tenant_id"],
                "matter_id": row["matter_id"],
                "event_sequence": row["event_sequence"],
                "event_sha256": row["event_sha256"],
                "previous_event_sha256": row.get("previous_event_sha256"),
                "action": row["action"],
                "boundary": row["boundary"],
                "outcome": row["outcome"],
                "reason_code": row["reason_code"],
                "occurred_at": row["occurred_at"],
                "recorded_at": row["recorded_at"],
                "actor_principal_id": row["actor_principal_id"],
                "authorization_decision_id": row.get("authorization_decision_id"),
                "policy_decision_id": row.get("policy_decision_id"),
                "trace": ActivityTraceRead.model_validate(_mapping(row["trace"])),
                "source": ActivitySourceRead.model_validate(_mapping(row["source"])),
            }
        )
    except Exception:
        raise ActivityStoreUnavailable("activity row is invalid") from None


class PostgresMatterActivityRepository:
    """Execute only feature-owned SQL through a request-scoped session."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def _execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        try:
            with self._session_factory() as session:
                return session.execute(statement, parameters)
        except (ActivityIdempotencyConflict, ActivityStoreUnavailable):
            raise
        except Exception:
            raise ActivityStoreUnavailable("activity persistence unavailable") from None

    def ensure_ready(self) -> None:
        rows = self._execute(
            "SELECT sklegal_activity.runtime_ready() AS ready;",
            {},
        )
        if len(rows) != 1 or rows[0].get("ready") is not True:
            raise ActivityStoreUnavailable("activity persistence unavailable")

    def project(self, record: ActivityProjectionRecord) -> bool:
        rows = self._execute(_PROJECT_EVENT_SQL, asdict(record))
        if len(rows) != 1 or not isinstance(rows[0].get("projected"), bool):
            raise ActivityStoreUnavailable("activity projection result is invalid")
        return bool(rows[0]["projected"])

    def _watermark(
        self,
        *,
        tenant_id: UUID,
        snapshot_sequence: int | None,
        snapshot_sha256: str | None,
        verified_at: datetime,
    ) -> ActivityWatermarkRead:
        rows = self._execute(_WATERMARK_SQL, {"tenant_id": tenant_id})
        if len(rows) != 1:
            raise ActivityStoreUnavailable("activity watermark unavailable")
        row = rows[0]
        current_sequence = _integer(row["projected_sequence"])
        current_sha256 = str(row["projected_event_sha256"])
        if snapshot_sequence is None:
            snapshot_sequence = current_sequence
            snapshot_sha256 = current_sha256
        if snapshot_sha256 is None or snapshot_sequence > current_sequence:
            raise ActivityStoreUnavailable("activity snapshot unavailable")
        if snapshot_sequence == current_sequence and snapshot_sha256 != current_sha256:
            raise ActivityStoreUnavailable("activity snapshot unavailable")
        exact = self._execute(
            """
            SELECT sklegal_activity.snapshot_is_valid(
                %(tenant_id)s, %(snapshot_sequence)s, %(snapshot_sha256)s
            ) AS valid;
            """,
            {
                "tenant_id": tenant_id,
                "snapshot_sequence": snapshot_sequence,
                "snapshot_sha256": snapshot_sha256,
            },
        )
        if len(exact) != 1 or exact[0].get("valid") is not True:
            raise ActivityStoreUnavailable("activity snapshot unavailable")
        head_sequence = _integer(row["tenant_head_sequence"])
        return ActivityWatermarkRead(
            tenant_head_sequence=head_sequence,
            tenant_head_sha256=str(row["tenant_head_sha256"]),
            projected_sequence=snapshot_sequence,
            projected_event_sha256=snapshot_sha256,
            lag_events=head_sequence - snapshot_sequence,
            verified_at=verified_at,
        )

    def _verify_supersession_graph(self, *, tenant_id: UUID, matter_id: UUID) -> None:
        rows = self._execute(
            _SUPERSESSION_GRAPH_SQL,
            {"tenant_id": tenant_id, "matter_id": matter_id},
        )
        if len(rows) != 1 or rows[0].get("valid") is not True:
            raise ActivityStoreUnavailable("activity supersession graph unavailable")

    def read_page(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        after_sequence: int,
        snapshot_sequence: int | None,
        snapshot_sha256: str | None,
        limit: int,
        verified_at: datetime,
    ) -> tuple[tuple[MatterActivityItemRead, ...], bool, ActivityWatermarkRead]:
        self.ensure_ready()
        self._verify_supersession_graph(tenant_id=tenant_id, matter_id=matter_id)
        watermark = self._watermark(
            tenant_id=tenant_id,
            snapshot_sequence=snapshot_sequence,
            snapshot_sha256=snapshot_sha256,
            verified_at=verified_at,
        )
        rows = self._execute(
            _PAGE_SQL,
            {
                "tenant_id": tenant_id,
                "matter_id": matter_id,
                "after_sequence": after_sequence,
                "snapshot_sequence": watermark.projected_sequence,
                "row_limit": limit + 1,
            },
        )
        items = tuple(_item(row) for row in rows[:limit])
        return items, len(rows) > limit, watermark

    def read_selection(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        first_sequence: int,
        last_sequence: int,
        projected_sequence: int,
        projected_sha256: str,
        verified_at: datetime,
    ) -> tuple[tuple[MatterActivityItemRead, ...], ActivityWatermarkRead]:
        self.ensure_ready()
        self._verify_supersession_graph(tenant_id=tenant_id, matter_id=matter_id)
        watermark = self._watermark(
            tenant_id=tenant_id,
            snapshot_sequence=projected_sequence,
            snapshot_sha256=projected_sha256,
            verified_at=verified_at,
        )
        rows = self._execute(
            _PAGE_SQL,
            {
                "tenant_id": tenant_id,
                "matter_id": matter_id,
                "after_sequence": first_sequence - 1,
                "snapshot_sequence": last_sequence,
                "row_limit": 10_001,
            },
        )
        items = tuple(_item(row) for row in rows)
        if (
            not items
            or len(items) > 10_000
            or items[0].event_sequence != first_sequence
            or items[-1].event_sequence != last_sequence
        ):
            raise ActivityStoreUnavailable("activity export selection unavailable")
        return items, watermark

    def append_export(
        self,
        *,
        proposal: ActivityExportProposalRead,
        policy_revision: str,
        request_sha256: str,
        audit_fact: ActivityAuditFact,
        outbox: ActivityOutboxMessage,
    ) -> tuple[ActivityExportProposalRead, bool]:
        rows = self._execute(
            _APPEND_EXPORT_SQL,
            {
                **proposal.model_dump(mode="python"),
                "policy_revision": policy_revision,
                "request_sha256": request_sha256,
                "audit_fact_id": audit_fact.fact_id,
                "outbox_id": outbox.message_id,
            },
        )
        if len(rows) != 1:
            raise ActivityStoreUnavailable("activity export result is invalid")
        result = _mapping(rows[0].get("result"))
        if result.get("conflict") is True:
            raise ActivityIdempotencyConflict("activity export key conflict")
        try:
            stored = ActivityExportProposalRead.model_validate(
                _mapping(result["proposal"])
            )
        except Exception:
            raise ActivityStoreUnavailable(
                "activity export result is invalid"
            ) from None
        return stored, bool(result.get("replayed"))
