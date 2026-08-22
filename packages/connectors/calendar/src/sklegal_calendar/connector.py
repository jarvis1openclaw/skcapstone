"""Calendar validation, conflict detection, and simulation receipts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sklegal_connectors.base import (
    Action,
    ActionStatus,
    CapabilityVerifier,
    ConnectorInvariantError,
    SimulationReceipt,
    SimulationRegistry,
)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def destination_digest(calendar_id: str) -> str:
    """Derive the destination digest binding an action to one exact calendar."""

    if not calendar_id:
        raise ConnectorInvariantError("calendar destination requires a calendar id")
    return _digest("sklegal-calendar-destination-v1", calendar_id)


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """Immutable event version used for conflict checks and calendar writes."""

    event_id: str
    calendar_id: str
    summary: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    version: int = 1

    def __post_init__(self) -> None:
        if not all((self.event_id, self.calendar_id, self.summary, self.timezone)):
            raise ConnectorInvariantError("calendar event identity is required")
        if self.version < 1 or self.ends_at <= self.starts_at:
            raise ConnectorInvariantError("calendar event interval is invalid")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ConnectorInvariantError(
                "calendar event timestamps must be timezone-aware"
            )

    @property
    def digest(self) -> str:
        return _digest(
            "sklegal-calendar-event-v1",
            self.event_id,
            self.calendar_id,
            self.summary,
            self.starts_at.isoformat(),
            self.ends_at.isoformat(),
            self.timezone,
            str(self.version),
        )

    def overlaps(self, other: CalendarEvent) -> bool:
        return (
            self.calendar_id == other.calendar_id
            and self.starts_at < other.ends_at
            and other.starts_at < self.ends_at
        )


@dataclass(frozen=True, slots=True)
class CalendarWriteReceipt:
    """Connector receipt binding the shared receipt to the exact event version."""

    event_id: str
    calendar_id: str
    event_digest: str
    simulation_receipt: SimulationReceipt


class CalendarConnector:
    """Calendar connector with simulation as its only effect path."""

    connector_name = "calendar"
    simulation_only = True

    def __init__(
        self,
        *,
        registry: SimulationRegistry | None = None,
        existing_events: Iterable[CalendarEvent] = (),
    ) -> None:
        self._registry = registry or SimulationRegistry()
        self._existing_events = tuple(existing_events)

    def conflicts(self, event: CalendarEvent) -> tuple[CalendarEvent, ...]:
        return tuple(
            existing
            for existing in self._existing_events
            if existing.event_id != event.event_id and event.overlaps(existing)
        )

    def validate(self, action: Action, event: CalendarEvent) -> Action:
        if action.connector != self.connector_name:
            raise ConnectorInvariantError("action is not a calendar action")
        if action.artifact_sha256 != event.digest:
            raise ConnectorInvariantError(
                "calendar action is bound to another event version"
            )
        if action.destination_sha256 != destination_digest(event.calendar_id):
            raise ConnectorInvariantError(
                "calendar action is bound to another destination calendar"
            )
        if self.conflicts(event):
            raise ConnectorInvariantError(
                "calendar event conflicts with an existing event"
            )
        if action.status is ActionStatus.APPROVED:
            return action
        if action.status is not ActionStatus.DRAFT:
            raise ConnectorInvariantError("calendar action is not awaiting validation")
        return action.validate(artifact_sha256=event.digest)

    def simulate(
        self,
        action: Action,
        *,
        event: CalendarEvent,
        capability_ref: str,
        capability_verifier: CapabilityVerifier,
    ) -> tuple[Action, CalendarWriteReceipt]:
        """Run the complete approval and receipt path without live delivery."""

        validated = self.validate(action, event)
        if validated.approval is None:
            raise ConnectorInvariantError(
                "calendar action requires exact-version approval"
            )
        queued = validated.queue(
            destination_sha256=validated.destination_sha256,
            capability_ref=capability_ref,
            capability_verifier=capability_verifier,
        )
        dispatched = queued.dispatch()
        simulation_receipt = self._registry.dispatch(dispatched)
        verified = dispatched.verify_receipt(simulation_receipt)
        return verified, CalendarWriteReceipt(
            event_id=event.event_id,
            calendar_id=event.calendar_id,
            event_digest=event.digest,
            simulation_receipt=simulation_receipt,
        )
