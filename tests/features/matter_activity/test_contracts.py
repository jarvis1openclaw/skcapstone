from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_api.features.matter_activity.contracts import (
    ActivityCursorPayload,
    ActivityExportProposalRead,
    ActivitySourceRead,
    activity_export_content_sha256,
    activity_selection_sha256,
    decode_activity_cursor,
    encode_activity_cursor,
    validate_activity_supersession_graph,
)
from sklegal_api.features.matter_activity.synthetic import (
    OTHER_MATTER_ID,
    build_public_synthetic_activity_store,
)

TENANT = UUID("10000000-0000-4000-8000-000000000001")
MATTER = UUID("20000000-0000-4000-8000-000000000001")
FIXTURE = Path("tests/fixtures/mvp/fragments/matter_activity/activity-v1.json")


def test_cursor_round_trip_and_tamper_denial() -> None:
    payload = ActivityCursorPayload(
        tenant_id=TENANT,
        matter_id=MATTER,
        after_sequence=4,
        snapshot_sequence=9,
        snapshot_sha256="a" * 64,
    )
    token = encode_activity_cursor(payload)
    assert decode_activity_cursor(token) == payload
    replacement = "A" if token[-1] != "A" else "B"
    with pytest.raises(ValueError, match="cursor is invalid"):
        decode_activity_cursor(token[:-1] + replacement)
    with pytest.raises(ValueError, match="cursor is invalid"):
        decode_activity_cursor("not-base64***")


def test_source_requires_exact_correction_and_supersession_shape() -> None:
    with pytest.raises(ValidationError, match="correction cannot correct itself"):
        ActivitySourceRead(
            kind="correction",
            source_id=MATTER,
            source_version=1,
            source_sha256="b" * 64,
            status="recorded",
            recorded_at=datetime(2026, 8, 23, tzinfo=UTC),
            corrects_source_id=MATTER,
        )
    with pytest.raises(ValidationError, match="status and successor"):
        ActivitySourceRead(
            kind="matter_event",
            source_id=MATTER,
            source_version=1,
            source_sha256="b" * 64,
            status="superseded",
            recorded_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="identity and version"):
        ActivitySourceRead(
            kind="matter_event",
            source_id=MATTER,
            source_version=1,
            source_sha256="b" * 64,
            status="superseded",
            recorded_at=datetime(2026, 8, 23, tzinfo=UTC),
            superseded_by_source_id=TENANT,
        )
    with pytest.raises(ValidationError, match="version must be higher"):
        ActivitySourceRead(
            kind="matter_event",
            source_id=MATTER,
            source_version=2,
            source_sha256="b" * 64,
            status="superseded",
            recorded_at=datetime(2026, 8, 23, tzinfo=UTC),
            superseded_by_source_id=TENANT,
            superseded_by_source_version=1,
        )


def test_supersession_graph_rejects_dangling_wrong_kind_and_cross_scope() -> None:
    items = build_public_synthetic_activity_store()._items
    superseded_index = next(
        index for index, item in enumerate(items) if item.source.status == "superseded"
    )
    superseded = items[superseded_index]

    dangling = list(items)
    dangling[superseded_index] = superseded.model_copy(
        update={
            "source": superseded.source.model_copy(
                update={"superseded_by_source_id": TENANT}
            )
        }
    )
    with pytest.raises(ValueError, match="same-scope same-kind"):
        validate_activity_supersession_graph(tuple(dangling))

    wrong_kind = list(items)
    wrong_kind[superseded_index] = superseded.model_copy(
        update={
            "source": superseded.source.model_copy(
                update={
                    "superseded_by_source_id": items[9].source.source_id,
                    "superseded_by_source_version": items[9].source.source_version,
                }
            )
        }
    )
    with pytest.raises(ValueError, match="same-scope same-kind"):
        validate_activity_supersession_graph(tuple(wrong_kind))

    cross_scope = list(items)
    successor = cross_scope[11]
    cross_scope[11] = successor.model_copy(update={"matter_id": OTHER_MATTER_ID})
    with pytest.raises(ValueError, match="same-scope same-kind"):
        validate_activity_supersession_graph(tuple(cross_scope))

    cyclic = list(items)
    successor = cyclic[11]
    cyclic[11] = successor.model_copy(
        update={
            "source": successor.source.model_copy(
                update={
                    "status": "superseded",
                    "superseded_by_source_id": superseded.source.source_id,
                    "superseded_by_source_version": superseded.source.source_version,
                }
            )
        }
    )
    with pytest.raises(ValueError, match="same-scope same-kind"):
        validate_activity_supersession_graph(tuple(cyclic))


def test_public_synthetic_supersession_resolves_to_higher_same_kind_version() -> None:
    items = build_public_synthetic_activity_store()._items
    validate_activity_supersession_graph(items)
    superseded = next(item for item in items if item.source.status == "superseded")
    successor = next(
        item
        for item in items
        if item.source.source_id == superseded.source.superseded_by_source_id
    )
    assert successor.tenant_id == superseded.tenant_id
    assert successor.matter_id == superseded.matter_id
    assert successor.source.kind == superseded.source.kind
    assert successor.source.source_version == 3
    assert successor.source.source_version > superseded.source.source_version


def test_export_proposal_recomputes_selection_and_content_hashes() -> None:
    event_ids = (
        UUID("30000000-0000-4000-8000-000000000001"),
        UUID("30000000-0000-4000-8000-000000000002"),
    )
    event_hashes = ("c" * 64, "d" * 64)
    selection = activity_selection_sha256(
        tenant_id=TENANT,
        matter_id=MATTER,
        event_ids=event_ids,
        event_sha256s=event_hashes,
        first_sequence=1,
        last_sequence=2,
        projected_sequence=2,
        projected_sha256="d" * 64,
    )
    proposal = ActivityExportProposalRead(
        proposal_id=UUID("40000000-0000-4000-8000-000000000001"),
        tenant_id=TENANT,
        matter_id=MATTER,
        title="Verified Matter activity",
        first_event_sequence=1,
        last_event_sequence=2,
        item_count=2,
        event_ids=event_ids,
        event_sha256s=event_hashes,
        selection_sha256=selection,
        content_sha256=activity_export_content_sha256(
            title="Verified Matter activity",
            selection_sha256=selection,
            item_count=2,
        ),
        projected_sequence=2,
        projected_event_sha256="d" * 64,
        tenant_head_sequence=2,
        tenant_head_sha256="d" * 64,
        proposed_by_principal_id=UUID("50000000-0000-4000-8000-000000000001"),
        authorization_decision_id=UUID("60000000-0000-4000-8000-000000000001"),
        policy_decision_id=UUID("70000000-0000-4000-8000-000000000001"),
        idempotency_key_sha256="e" * 64,
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    assert proposal.dispatch_state == "not_requested"
    assert proposal.approval_id is None
    with pytest.raises(ValidationError, match="content hash"):
        ActivityExportProposalRead.model_validate(
            {**proposal.model_dump(mode="python"), "content_sha256": "f" * 64}
        )


def test_public_synthetic_chronology_is_versioned_complete_and_isolated() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["version"] == "sklegal-matter-activity-fixture/v1"
    assert fixture["classification"] == "public"
    assert fixture["externalEffects"] is False
    assert set(fixture["sourceKinds"]) == {
        "audit_event",
        "matter_event",
        "workflow_reference",
        "agent_run",
        "tool_call",
        "source_reference",
        "work_product_version",
        "approval",
        "execution_event",
        "receipt",
        "correction",
    }
    assert fixture["unrelatedTenantChainEvent"] == {
        "eventSequence": 13,
        "matterId": "20000000-0000-4000-8000-000000000002",
        "visibleInMatterProjection": False,
    }
    superseded = next(
        item for item in fixture["chronology"] if item["status"] == "superseded"
    )
    assert superseded["supersededByEventSequence"] == 12
    assert superseded["supersededBySourceVersion"] == 3
    successor = next(
        item
        for item in fixture["chronology"]
        if item["eventSequence"] == superseded["supersededByEventSequence"]
    )
    assert successor["sourceKind"] == superseded["sourceKind"]
    assert successor["sourceVersion"] > superseded["sourceVersion"]
