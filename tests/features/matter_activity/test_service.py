from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sklegal_api.features.matter_activity.contracts import ActivityExportCommand
from sklegal_api.features.matter_activity.service import (
    ActivityAccessContext,
    ActivityServiceError,
    MatterActivityService,
    StaticActivityPolicy,
)
from sklegal_api.features.matter_activity.synthetic import (
    MATTER_ID,
    OTHER_MATTER_ID,
    PRINCIPAL_ID,
    TENANT_ID,
    build_public_synthetic_activity_store,
)

NOW = datetime(2026, 8, 23, 13, 0, tzinfo=UTC)


def fixture() -> tuple[
    MatterActivityService,
    object,
    StaticActivityPolicy,
    ActivityAccessContext,
    ActivityAccessContext,
]:
    store = build_public_synthetic_activity_store()
    policy = StaticActivityPolicy(
        memberships={(TENANT_ID, MATTER_ID, PRINCIPAL_ID)},
        revision="9" * 64,
        valid_until=NOW + timedelta(hours=1),
    )
    service = MatterActivityService(store=store, policy=policy, clock=lambda: NOW)
    read = ActivityAccessContext(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        resource_id=MATTER_ID,
        principal_id=PRINCIPAL_ID,
        capability="audit.read",
        purpose="audit_review",
        authorization_decision_id=UUID("60000000-0000-4000-8000-000000000001"),
        correlation_id=UUID("61000000-0000-4000-8000-000000000001"),
        credential_expires_at=NOW + timedelta(minutes=30),
    )
    export = read
    return service, store, policy, read, export


def test_pagination_is_stable_ordered_and_excludes_another_matter() -> None:
    service, _store, _policy, read, _export = fixture()
    first = service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=3
    )
    assert [item.event_sequence for item in first.items] == [1, 2, 3]
    assert first.next_cursor is not None
    second = service.list_activity(
        context=read,
        matter_id=MATTER_ID,
        cursor=first.next_cursor,
        limit=3,
    )
    assert [item.event_sequence for item in second.items] == [4, 5, 6]
    assert all(item.matter_id != OTHER_MATTER_ID for item in second.items)
    assert second.snapshot_sha256 == first.snapshot_sha256


def test_lag_and_watermark_are_truthful() -> None:
    service, store, _policy, read, _export = fixture()
    store.set_projected_for_test(10, store._links[9].event_sha256)
    page = service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=100
    )
    assert page.snapshot_sequence == 10
    assert page.watermark.tenant_head_sequence == 13
    assert page.watermark.lag_events == 3
    assert max(item.event_sequence for item in page.items) == 10


def test_chain_tamper_and_outage_fail_closed_then_reset_recovers() -> None:
    service, store, _policy, read, _export = fixture()
    tampered = list(store._links)
    tampered[3] = replace(tampered[3], previous_event_sha256="0" * 64)
    store.replace_links_for_test(tuple(tampered))
    with pytest.raises(ActivityServiceError, match="dependency_unavailable"):
        service.list_activity(context=read, matter_id=MATTER_ID, cursor=None, limit=10)
    store.reset()
    assert service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=10
    ).items
    store.available = False
    with pytest.raises(ActivityServiceError, match="dependency_unavailable"):
        service.list_activity(context=read, matter_id=MATTER_ID, cursor=None, limit=10)


def test_missing_chain_link_fails_closed_and_reset_restores_exact_fixture() -> None:
    service, store, _policy, read, _export = fixture()
    original = store._links
    store.replace_links_for_test(original[:4] + original[5:])
    with pytest.raises(ActivityServiceError, match="dependency_unavailable"):
        service.list_activity(context=read, matter_id=MATTER_ID, cursor=None, limit=100)
    store.reset()
    restored = service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=100
    )
    assert restored.snapshot_sequence == 13
    assert store._links == original


def test_all_source_and_trace_lanes_remain_attributable_and_append_only() -> None:
    service, _store, _policy, read, _export = fixture()
    page = service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=100
    )
    assert {item.source.kind for item in page.items} == {
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
    assert all(item.trace.correlation_id for item in page.items)
    assert any(item.trace.workflow_reference_id for item in page.items)
    assert any(item.trace.agent_run_id for item in page.items)
    assert any(item.trace.tool_call_id for item in page.items)
    superseded = next(item for item in page.items if item.source.status == "superseded")
    assert superseded.source.superseded_by_source_id is not None
    correction = next(item for item in page.items if item.source.kind == "correction")
    assert correction.source.corrects_source_id is not None
    with pytest.raises(ValidationError, match="frozen"):
        correction.source.status = "rewritten"  # type: ignore[misc]


def test_scope_capability_expiry_and_policy_deny_before_lookup() -> None:
    service, store, policy, read, _export = fixture()
    store.available = False
    policy.memberships.clear()
    with pytest.raises(ActivityServiceError, match="access_denied"):
        service.list_activity(context=read, matter_id=MATTER_ID, cursor=None, limit=10)
    wrong_scope = read.model_copy(update={"matter_id": OTHER_MATTER_ID})
    with pytest.raises(ActivityServiceError, match="access_denied"):
        service.list_activity(
            context=wrong_scope, matter_id=MATTER_ID, cursor=None, limit=10
        )
    expired = read.model_copy(update={"credential_expires_at": NOW})
    with pytest.raises(ActivityServiceError, match="access_denied"):
        service.list_activity(
            context=expired, matter_id=MATTER_ID, cursor=None, limit=10
        )
    revoked = read.model_copy(update={"revoked": True})
    with pytest.raises(ActivityServiceError, match="access_denied"):
        service.list_activity(
            context=revoked, matter_id=MATTER_ID, cursor=None, limit=10
        )
    wrong_capability = read.model_copy(update={"capability": "matter.read"})
    with pytest.raises(ActivityServiceError, match="access_denied"):
        service.list_activity(
            context=wrong_capability, matter_id=MATTER_ID, cursor=None, limit=10
        )
    other_tenant = read.model_copy(
        update={"tenant_id": UUID("10000000-0000-4000-8000-000000000002")}
    )
    with pytest.raises(ActivityServiceError, match="access_denied"):
        service.list_activity(
            context=other_tenant, matter_id=MATTER_ID, cursor=None, limit=10
        )


def test_policy_unavailable_and_stale_fail_closed() -> None:
    service, _store, policy, read, _export = fixture()
    policy.available = False
    with pytest.raises(ActivityServiceError, match="policy_unavailable"):
        service.list_activity(context=read, matter_id=MATTER_ID, cursor=None, limit=10)
    policy.available = True
    policy.valid_until = NOW
    with pytest.raises(ActivityServiceError, match="policy_unavailable"):
        service.list_activity(context=read, matter_id=MATTER_ID, cursor=None, limit=10)


def test_cursor_scope_and_tamper_are_sanitized() -> None:
    service, _store, _policy, read, _export = fixture()
    page = service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=2
    )
    assert page.next_cursor is not None
    with pytest.raises(ActivityServiceError, match="validation_failed"):
        service.list_activity(
            context=read,
            matter_id=MATTER_ID,
            cursor=page.next_cursor[:-1] + "*",
            limit=2,
        )


def test_hashed_export_is_idempotent_atomic_and_never_dispatches() -> None:
    service, store, _policy, _read, export = fixture()
    page = service.list_activity(
        context=export.model_copy(
            update={"capability": "audit.read", "purpose": "audit_review"}
        ),
        matter_id=MATTER_ID,
        cursor=None,
        limit=100,
    )
    command = ActivityExportCommand(
        title="Public synthetic Matter activity",
        first_event_sequence=1,
        last_event_sequence=4,
        expected_projected_sequence=page.snapshot_sequence,
        expected_projected_sha256=page.snapshot_sha256,
        expected_resource_version=page.snapshot_sequence,
    )
    first = service.propose_export(
        context=export,
        matter_id=MATTER_ID,
        idempotency_key="activity-export-001",
        command=command,
    )
    replay = service.propose_export(
        context=export,
        matter_id=MATTER_ID,
        idempotency_key="activity-export-001",
        command=command,
    )
    assert replay == first
    assert first.operation_id == "create_activity_export"
    assert first.proposal.status == "proposed"
    assert first.proposal.approval_id is None
    assert first.proposal.dispatch_state == "not_requested"
    assert len(store.audit_facts) == 1
    assert len(store.outbox_messages) == 1

    changed = command.model_copy(update={"title": "Different activity export"})
    with pytest.raises(ActivityServiceError, match="idempotency_conflict"):
        service.propose_export(
            context=export,
            matter_id=MATTER_ID,
            idempotency_key="activity-export-001",
            command=changed,
        )


def test_failed_export_leaves_no_proposal_audit_or_outbox() -> None:
    service, store, _policy, _read, export = fixture()
    store.fail_next_export = True
    command = ActivityExportCommand(
        title="Unavailable export",
        first_event_sequence=1,
        last_event_sequence=4,
        expected_projected_sequence=13,
        expected_projected_sha256=store._links[-1].event_sha256,
        expected_resource_version=13,
    )
    with pytest.raises(ActivityServiceError, match="dependency_unavailable"):
        service.propose_export(
            context=export,
            matter_id=MATTER_ID,
            idempotency_key="activity-export-fail",
            command=command,
        )
    assert store.audit_facts == ()
    assert store.outbox_messages == ()


def test_export_idempotency_rejects_cross_principal_and_cross_decision_reuse() -> None:
    service, store, policy, read, export = fixture()
    page = service.list_activity(
        context=read, matter_id=MATTER_ID, cursor=None, limit=100
    )
    command = ActivityExportCommand(
        title="Principal-bound activity export",
        first_event_sequence=1,
        last_event_sequence=4,
        expected_projected_sequence=page.snapshot_sequence,
        expected_projected_sha256=page.snapshot_sha256,
        expected_resource_version=page.snapshot_sequence,
    )
    first = service.propose_export(
        context=export,
        matter_id=MATTER_ID,
        idempotency_key="activity-bound-001",
        command=command,
    )
    other_principal = UUID("30000000-0000-4000-8000-000000000002")
    policy.memberships.add((TENANT_ID, MATTER_ID, other_principal))
    with pytest.raises(ActivityServiceError, match="idempotency_conflict"):
        service.propose_export(
            context=export.model_copy(update={"principal_id": other_principal}),
            matter_id=MATTER_ID,
            idempotency_key="activity-bound-001",
            command=command,
        )
    with pytest.raises(ActivityServiceError, match="idempotency_conflict"):
        service.propose_export(
            context=export.model_copy(
                update={
                    "authorization_decision_id": UUID(
                        "60000000-0000-4000-8000-000000000002"
                    )
                }
            ),
            matter_id=MATTER_ID,
            idempotency_key="activity-bound-001",
            command=command,
        )
    policy.revision = "8" * 64
    with pytest.raises(ActivityServiceError, match="idempotency_conflict"):
        service.propose_export(
            context=export,
            matter_id=MATTER_ID,
            idempotency_key="activity-bound-001",
            command=command,
        )
    assert first.proposal.proposed_by_principal_id == PRINCIPAL_ID
    assert len(store.audit_facts) == 1
    assert len(store.outbox_messages) == 1
