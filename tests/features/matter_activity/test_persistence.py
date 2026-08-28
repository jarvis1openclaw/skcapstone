from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sklegal_api.features.matter_activity.contracts import (
    ActivityExportProposalRead,
)
from sklegal_api.features.matter_activity.store import (
    ActivityAuditFact,
    ActivityIdempotencyConflict,
    ActivityOutboxMessage,
    ActivityStoreUnavailable,
)
from sklegal_api.features.matter_activity.synthetic import (
    MATTER_ID,
    PRINCIPAL_ID,
    TENANT_ID,
    build_public_synthetic_activity_store,
)
from sklegal_persistence.features.matter_activity.repository import (
    ActivityProjectionRecord,
    PostgresMatterActivityRepository,
)

NOW = datetime(2026, 8, 23, 13, 0, tzinfo=UTC)


class FakeSession(AbstractContextManager["FakeSession"]):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.responses: list[list[dict[str, object]]] = []

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(
        self, statement: str, parameters: dict[str, object]
    ) -> list[dict[str, object]]:
        self.calls.append((statement, dict(parameters)))
        if not self.responses:
            raise RuntimeError("no fake response")
        return self.responses.pop(0)


def repository(session: FakeSession) -> PostgresMatterActivityRepository:
    return PostgresMatterActivityRepository(lambda: session)


def source_row(sequence: int = 1) -> dict[str, object]:
    item = build_public_synthetic_activity_store()._items[sequence - 1]
    return {
        "activity_id": item.activity_id,
        "tenant_id": item.tenant_id,
        "matter_id": item.matter_id,
        "event_sequence": item.event_sequence,
        "event_sha256": item.event_sha256,
        "previous_event_sha256": item.previous_event_sha256,
        "action": item.action,
        "boundary": item.boundary,
        "outcome": item.outcome,
        "reason_code": item.reason_code,
        "occurred_at": item.occurred_at,
        "recorded_at": item.recorded_at,
        "actor_principal_id": item.actor_principal_id,
        "authorization_decision_id": item.authorization_decision_id,
        "policy_decision_id": item.policy_decision_id,
        "trace": item.trace.model_dump(mode="python", by_alias=True),
        "source": item.source.model_dump(mode="python", by_alias=True),
    }


def test_projection_passes_every_exact_hash_and_reference() -> None:
    session = FakeSession()
    session.responses.append([{"projected": True}])
    record = ActivityProjectionRecord(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        audit_event_id=UUID("52000000-0000-4000-8000-000000000001"),
        event_sequence=1,
        event_sha256="a" * 64,
        source_kind="matter_event",
        source_id=UUID("51000000-0000-4000-8000-000000000001"),
        source_version=1,
        source_sha256="b" * 64,
        source_status="recorded",
        source_recorded_at=NOW,
        corrects_source_id=None,
        superseded_by_source_id=None,
        superseded_by_source_version=None,
        causation_id=None,
        workflow_reference_id=None,
        agent_run_id=None,
        tool_call_id=None,
        idempotency_key_sha256="c" * 64,
        request_sha256="d" * 64,
        projection_audit_event_id=UUID("62000000-0000-4000-8000-000000000001"),
        projected_at=NOW,
    )
    assert repository(session).project(record)
    assert session.calls[0][1] == asdict(record)


def test_page_uses_snapshot_limit_plus_one_and_typed_rows() -> None:
    session = FakeSession()
    link = build_public_synthetic_activity_store()._links[-1]
    session.responses.extend(
        [
            [{"ready": True}],
            [{"valid": True}],
            [
                {
                    "tenant_head_sequence": 12,
                    "tenant_head_sha256": link.event_sha256,
                    "projected_sequence": 12,
                    "projected_event_sha256": link.event_sha256,
                }
            ],
            [{"valid": True}],
            [source_row(1), source_row(2)],
        ]
    )
    items, more, watermark = repository(session).read_page(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        after_sequence=0,
        snapshot_sequence=None,
        snapshot_sha256=None,
        limit=1,
        verified_at=NOW,
    )
    assert [item.event_sequence for item in items] == [1]
    assert more
    assert watermark.lag_events == 0
    assert session.calls[-1][1]["row_limit"] == 2


def test_invalid_page_and_backend_failures_are_sanitized() -> None:
    session = FakeSession()
    session.responses.append([{"ready": False}])
    with pytest.raises(ActivityStoreUnavailable, match="persistence unavailable"):
        repository(session).ensure_ready()

    session = FakeSession()
    session.responses.append([{"ready": True}])
    session.responses.append([])
    with pytest.raises(ActivityStoreUnavailable, match="supersession graph"):
        repository(session).read_page(
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            after_sequence=0,
            snapshot_sequence=None,
            snapshot_sha256=None,
            limit=1,
            verified_at=NOW,
        )


def test_export_conflict_is_closed_without_returning_a_proposal() -> None:
    store = build_public_synthetic_activity_store()
    item = store._items[0]
    from sklegal_api.features.matter_activity.contracts import (
        activity_export_content_sha256,
        activity_selection_sha256,
    )

    selection = activity_selection_sha256(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        event_ids=(item.activity_id,),
        event_sha256s=(item.event_sha256,),
        first_sequence=1,
        last_sequence=1,
        projected_sequence=13,
        projected_sha256=store._links[-1].event_sha256,
    )
    proposal = ActivityExportProposalRead(
        proposal_id=UUID("70000000-0000-4000-8000-000000000001"),
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        title="Activity",
        first_event_sequence=1,
        last_event_sequence=1,
        item_count=1,
        event_ids=(item.activity_id,),
        event_sha256s=(item.event_sha256,),
        selection_sha256=selection,
        content_sha256=activity_export_content_sha256(
            title="Activity", selection_sha256=selection, item_count=1
        ),
        projected_sequence=13,
        projected_event_sha256=store._links[-1].event_sha256,
        tenant_head_sequence=13,
        tenant_head_sha256=store._links[-1].event_sha256,
        proposed_by_principal_id=PRINCIPAL_ID,
        authorization_decision_id=UUID("71000000-0000-4000-8000-000000000001"),
        policy_decision_id=UUID("72000000-0000-4000-8000-000000000001"),
        idempotency_key_sha256="d" * 64,
        created_at=NOW,
    )
    session = FakeSession()
    session.responses.append(
        [{"result": {"conflict": True, "marker": "must-not-leak"}}]
    )
    with pytest.raises(ActivityIdempotencyConflict):
        repository(session).append_export(
            proposal=proposal,
            policy_revision="f" * 64,
            request_sha256="e" * 64,
            audit_fact=ActivityAuditFact(
                fact_id=UUID("73000000-0000-4000-8000-000000000001"),
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                principal_id=PRINCIPAL_ID,
                action="matter.activity_export.created",
                proposal_id=proposal.proposal_id,
                selection_sha256=selection,
                occurred_at=NOW,
            ),
            outbox=ActivityOutboxMessage(
                message_id=UUID("73000000-0000-4000-8000-000000000001"),
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                audit_fact_id=UUID("73000000-0000-4000-8000-000000000001"),
                proposal_id=proposal.proposal_id,
                selection_sha256=selection,
            ),
        )
