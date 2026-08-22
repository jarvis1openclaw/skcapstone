from datetime import UTC, datetime, timedelta

import pytest
from sklegal_calendar import CalendarConnector, CalendarEvent
from sklegal_connectors.base import (
    Action,
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
)


class Capability:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed

    def verify(self, *, action: Action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "calendar-capability"


def make_event(event_id: str = "event-1") -> CalendarEvent:
    start = datetime(2026, 8, 24, 15, tzinfo=UTC)
    return CalendarEvent(
        event_id, "court-calendar", "Hearing", start, start + timedelta(hours=1), "UTC"
    )


def make_action(event: CalendarEvent) -> Action:
    return Action(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id=event.event_id,
        connector="calendar",
        artifact_id=event.event_id,
        artifact_version=event.version,
        artifact_sha256=event.digest,
        destination_sha256="a" * 64,
    )


def approved(action: Action) -> Action:
    validated = action.validate(artifact_sha256=action.artifact_sha256)
    return validated.approve(
        ApprovalBinding("approval-1", action.artifact_id, 1, action.artifact_sha256)
    )


def test_full_simulation_produces_verified_write_receipt():
    event = make_event()
    result, receipt = CalendarConnector().simulate(
        approved(make_action(event)),
        event=event,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )
    assert result.status is ActionStatus.RECEIPT_VERIFIED
    assert receipt.event_digest == event.digest
    assert receipt.simulation_receipt.simulated is True


def test_conflict_and_wrong_event_version_fail_validation():
    event = make_event()
    with pytest.raises(ConnectorInvariantError, match="conflicts"):
        CalendarConnector(existing_events=[make_event("event-2")]).validate(
            make_action(event), event
        )
    changed = CalendarEvent(
        "event-1", "court-calendar", "Changed", event.starts_at, event.ends_at, "UTC"
    )
    with pytest.raises(ConnectorInvariantError, match="another event version"):
        CalendarConnector().validate(make_action(event), changed)


def test_revocation_mid_flow_fails_closed():
    event = make_event()
    with pytest.raises(ConnectorInvariantError, match="capability"):
        CalendarConnector().simulate(
            approved(make_action(event)),
            event=event,
            capability_ref="calendar-capability",
            capability_verifier=Capability(False),
        )


def test_duplicate_dispatch_returns_same_receipt():
    event = make_event()
    connector = CalendarConnector()
    action = approved(make_action(event))
    first, receipt = connector.simulate(
        action,
        event=event,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )
    second, duplicate = connector.simulate(
        action,
        event=event,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )
    assert first == second
    assert receipt == duplicate
