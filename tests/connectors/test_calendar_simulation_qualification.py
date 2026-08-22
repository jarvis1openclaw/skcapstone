"""SKL-S5-03A calendar connector simulation qualification.

Exercises the complete simulation path for the calendar connector end to
end: exact-version approval, destination verification against the exact
calendar, duplicate suppression, synthetic receipts, receipt
reconciliation, and the structural guarantee that the connector code
contains no live transport.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sklegal_calendar import CalendarConnector, CalendarEvent, destination_digest
from sklegal_connectors.base import (
    Action,
    ActionStatus,
    ApprovalBinding,
    ConnectorInvariantError,
    SimulationRegistry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

BANNED_MODULES = {
    "socket",
    "smtplib",
    "ssl",
    "urllib",
    "urllib3",
    "http",
    "httpx",
    "requests",
    "aiohttp",
    "ftplib",
    "imaplib",
    "poplib",
    "telnetlib",
    "subprocess",
}
BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "input"}
BANNED_ATTR_CALLS = {("os", "system"), ("os", "popen")}


class Capability:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, *, action: Action, capability_ref: str) -> bool:
        return self.allowed and capability_ref == "calendar-capability"


def make_event(
    event_id: str = "event-1", calendar_id: str = "court-calendar"
) -> CalendarEvent:
    start = datetime(2026, 8, 24, 15, tzinfo=UTC)
    return CalendarEvent(
        event_id, calendar_id, "Hearing", start, start + timedelta(hours=1), "UTC"
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
        destination_sha256=destination_digest(event.calendar_id),
    )


def approved(action: Action) -> Action:
    validated = action.validate(artifact_sha256=action.artifact_sha256)
    return validated.approve(
        ApprovalBinding("approval-1", action.artifact_id, 1, action.artifact_sha256)
    )


def queue(action: Action) -> Action:
    return action.queue(
        destination_sha256=action.destination_sha256,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )


def assert_no_live_transport(*packages: str) -> None:
    for package in packages:
        root = REPO_ROOT / "packages" / "connectors" / package / "src"
        assert root.is_dir(), f"missing connector source tree: {root}"
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_module = alias.name.split(".")[0]
                        assert root_module not in BANNED_MODULES, (
                            f"{path}: {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root_module = node.module.split(".")[0]
                    assert root_module not in BANNED_MODULES, f"{path}: {node.module}"
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        assert node.func.id not in BANNED_CALLS, (
                            f"{path}: {node.func.id}"
                        )
                    elif isinstance(node.func, ast.Attribute) and isinstance(
                        node.func.value, ast.Name
                    ):
                        pair = (node.func.value.id, node.func.attr)
                        assert pair not in BANNED_ATTR_CALLS, f"{path}: {pair}"


def test_end_to_end_simulation_reaches_receipt_verified() -> None:
    registry = SimulationRegistry()
    connector = CalendarConnector(registry=registry)
    event = make_event()

    result, write_receipt = connector.simulate(
        approved(make_action(event)),
        event=event,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )

    assert result.status is ActionStatus.RECEIPT_VERIFIED
    assert write_receipt.event_id == event.event_id
    assert write_receipt.calendar_id == event.calendar_id
    assert write_receipt.event_digest == event.digest
    assert write_receipt.simulation_receipt.simulated is True
    assert write_receipt.simulation_receipt.artifact_sha256 == event.digest
    assert write_receipt.simulation_receipt.destination_sha256 == destination_digest(
        event.calendar_id
    )
    assert write_receipt.simulation_receipt.idempotency_key == result.idempotency_key
    assert registry.receipt_count == 1


def test_exact_version_approval_is_enforced() -> None:
    connector = CalendarConnector()
    event = make_event()

    with pytest.raises(ConnectorInvariantError, match="exact-version approval"):
        connector.simulate(
            make_action(event),
            event=event,
            capability_ref="calendar-capability",
            capability_verifier=Capability(),
        )

    validated = make_action(event).validate(artifact_sha256=event.digest)
    with pytest.raises(ConnectorInvariantError, match="exact artifact version"):
        validated.approve(
            ApprovalBinding("approval-1", event.event_id, 2, event.digest)
        )
    with pytest.raises(ConnectorInvariantError, match="exact artifact version"):
        validated.approve(ApprovalBinding("approval-1", event.event_id, 1, "e" * 64))

    changed_event = CalendarEvent(
        event.event_id,
        event.calendar_id,
        "Changed",
        event.starts_at,
        event.ends_at,
        "UTC",
    )
    with pytest.raises(ConnectorInvariantError, match="another event version"):
        connector.simulate(
            approved(make_action(event)),
            event=changed_event,
            capability_ref="calendar-capability",
            capability_verifier=Capability(),
        )


def test_destination_verification_binds_exact_calendar() -> None:
    connector = CalendarConnector()
    event = make_event()

    expected = hashlib.sha256(
        "\0".join(("sklegal-calendar-destination-v1", "court-calendar")).encode("utf-8")
    ).hexdigest()
    assert destination_digest("court-calendar") == expected
    with pytest.raises(ConnectorInvariantError, match="calendar id"):
        destination_digest("")

    misdirected = Action(
        tenant_id="tenant-1",
        matter_id="matter-1",
        action_id=event.event_id,
        connector="calendar",
        artifact_id=event.event_id,
        artifact_version=event.version,
        artifact_sha256=event.digest,
        destination_sha256=destination_digest("other-calendar"),
    )
    with pytest.raises(ConnectorInvariantError, match="another destination calendar"):
        connector.validate(misdirected, event)

    elsewhere = make_event(calendar_id="other-calendar")
    assert make_action(elsewhere).idempotency_key != make_action(event).idempotency_key

    with pytest.raises(ConnectorInvariantError, match="destination digest changed"):
        approved(make_action(event)).queue(
            destination_sha256=destination_digest("other-calendar"),
            capability_ref="calendar-capability",
            capability_verifier=Capability(),
        )


def test_duplicate_dispatch_suppression() -> None:
    registry = SimulationRegistry()
    connector = CalendarConnector(registry=registry)
    event = make_event()
    action = approved(make_action(event))

    first, receipt_one = connector.simulate(
        action,
        event=event,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )
    second, receipt_two = connector.simulate(
        action,
        event=event,
        capability_ref="calendar-capability",
        capability_verifier=Capability(),
    )

    assert first == second
    assert receipt_one == receipt_two
    assert registry.receipt_count == 1


def test_receipt_reconciliation_binds_exact_action() -> None:
    registry = SimulationRegistry()
    event = make_event()
    dispatched = queue(approved(make_action(event))).dispatch()
    receipt = registry.dispatch(dispatched)

    foreign_event = make_event(event_id="event-2")
    foreign_dispatched = queue(approved(make_action(foreign_event))).dispatch()
    foreign_receipt = registry.dispatch(foreign_dispatched)
    assert registry.receipt_count == 2

    with pytest.raises(ConnectorInvariantError, match="receipt does not bind"):
        dispatched.verify_receipt(foreign_receipt)

    verified = dispatched.verify_receipt(receipt)
    assert verified.status is ActionStatus.RECEIPT_VERIFIED
    assert verified.receipt == receipt
    assert registry.receipt_count == 2


def test_connector_code_has_no_live_transport() -> None:
    assert_no_live_transport("calendar", "base")
