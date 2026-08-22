"""Fail-closed deadline calculation and simulation-only calendar delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowError(ValueError):
    """Raised when a workflow input cannot be safely interpreted."""


class CalendarState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RECEIPT_VERIFIED = "receipt_verified"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DeadlineTrigger(StrictModel):
    trigger_id: UUID
    triggered_at: datetime
    period_days: int = Field(gt=0, le=3650)
    day_type: str = Field(pattern="^(calendar|business)$")
    timezone: str
    governing_rule_reference: str = Field(min_length=1)
    governing_rule_version: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    superseded_by: str | None = None

    @model_validator(mode="after")
    def validate_trigger(self) -> Self:
        if self.triggered_at.tzinfo is None or self.triggered_at.utcoffset() is None:
            raise WorkflowError("triggered_at must be timezone-aware")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise WorkflowError("unknown IANA timezone") from exc
        if self.superseded_by:
            raise WorkflowError("superseded rule cannot calculate a new deadline")
        return self


class DeadlineCandidate(StrictModel):
    candidate_id: UUID = Field(default_factory=uuid4)
    trigger_id: UUID
    candidate_due_at: datetime
    timezone: str
    confidence: str = Field(pattern="^(high|medium|low|unknown)$")
    governing_rule_reference: str
    governing_rule_version: str
    source_reference: str
    status: str = Field(
        default="candidate", pattern="^(candidate|operative|superseded)$"
    )
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    def review(self, *, reviewer: str, reviewed_at: datetime) -> DeadlineCandidate:
        if not reviewer.strip():
            raise WorkflowError("reviewer is required")
        if self.status != "candidate":
            raise WorkflowError("only a candidate can be reviewed")
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise WorkflowError("reviewed_at must be timezone-aware")
        return self.model_copy(
            update={
                "status": "operative",
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
            }
        )


class Reminder(StrictModel):
    reminder_id: UUID = Field(default_factory=uuid4)
    deadline_id: UUID
    remind_at: datetime
    channel: str = Field(pattern="^(in_app|calendar)$")
    idempotency_key: str = Field(min_length=1)


class CalendarEvent(StrictModel):
    event_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=512)
    starts_at: datetime
    ends_at: datetime
    timezone: str
    state: CalendarState = CalendarState.DRAFT

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise WorkflowError("calendar event timestamps must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise WorkflowError("calendar event must end after it starts")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise WorkflowError("unknown IANA timezone") from exc
        return self

    def validate_for_simulation(self) -> CalendarEvent:
        return self.model_copy(update={"state": CalendarState.VALIDATED})

    def approve_for_simulation(self) -> CalendarEvent:
        if self.state != CalendarState.VALIDATED:
            raise WorkflowError("calendar event must be validated before approval")
        return self.model_copy(update={"state": CalendarState.APPROVED})

    def to_ics(self) -> str:
        if self.state not in {
            CalendarState.APPROVED,
            CalendarState.QUEUED,
            CalendarState.DISPATCHED,
            CalendarState.RECEIPT_VERIFIED,
        }:
            raise WorkflowError("only an approved event can be exported")
        zone = ZoneInfo(self.timezone)
        start = self.starts_at.astimezone(zone).strftime("%Y%m%dT%H%M%S")
        end = self.ends_at.astimezone(zone).strftime("%Y%m%dT%H%M%S")
        return "\r\n".join(
            (
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//SKLegal//Calendar//EN",
                "BEGIN:VEVENT",
                f"UID:{self.event_id}",
                f"DTSTART;TZID={self.timezone}:{start}",
                f"DTEND;TZID={self.timezone}:{end}",
                f"SUMMARY:{self.summary}",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            )
        )


class CalendarReceipt(StrictModel):
    idempotency_key: str
    event_id: UUID
    receipt_sha256: str
    state: CalendarState = CalendarState.RECEIPT_VERIFIED


class CalendarSimulation:
    """In-memory connector that cannot dispatch to a real calendar."""

    def __init__(self) -> None:
        self._receipts: dict[str, CalendarReceipt] = {}

    def dispatch(self, event: CalendarEvent) -> CalendarReceipt:
        if event.state != CalendarState.APPROVED:
            raise WorkflowError("calendar dispatch requires exact-version approval")
        existing = self._receipts.get(event.idempotency_key)
        if existing:
            if existing.event_id != event.event_id:
                raise WorkflowError("idempotency key is bound to another event")
            return existing
        digest = sha256(event.to_ics().encode("utf-8")).hexdigest()
        receipt = CalendarReceipt(
            idempotency_key=event.idempotency_key,
            event_id=event.event_id,
            receipt_sha256=digest,
        )
        self._receipts[event.idempotency_key] = receipt
        return receipt

    def reconcile(self, receipt: CalendarReceipt, event: CalendarEvent) -> bool:
        return (
            receipt.event_id == event.event_id
            and receipt.idempotency_key == event.idempotency_key
            and receipt.receipt_sha256
            == sha256(event.to_ics().encode("utf-8")).hexdigest()
        )


class DeterministicDeadlineCalculator:
    """Calculate candidates only; review is required before operative status."""

    @staticmethod
    def calculate(trigger: DeadlineTrigger) -> DeadlineCandidate:
        local_zone = ZoneInfo(trigger.timezone)
        local = trigger.triggered_at.astimezone(local_zone)
        current = local.date()
        remaining = trigger.period_days
        while remaining:
            current += timedelta(days=1)
            if trigger.day_type == "calendar" or current.weekday() < 5:
                remaining -= 1
        due = datetime.combine(
            current, local.timetz().replace(tzinfo=None), tzinfo=local_zone
        ).astimezone(UTC)
        return DeadlineCandidate(
            trigger_id=trigger.trigger_id,
            candidate_due_at=due,
            timezone=trigger.timezone,
            confidence="high",
            governing_rule_reference=trigger.governing_rule_reference,
            governing_rule_version=trigger.governing_rule_version,
            source_reference=trigger.source_reference,
        )
