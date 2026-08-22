"""Tamper-evident in-memory audit and outbox contract for isolated development."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

from .models import (
    AuditEventDraft,
    DurableAuditEvent,
    OutboxDelivery,
    OutboxMessage,
    ProjectionWatermark,
)


class AuditUnavailable(RuntimeError):
    """Durable audit or outbox evidence could not be accepted safely."""


class WatermarkConflict(RuntimeError):
    """A projection attempted to advance from stale or unknown evidence."""


def _canonical_timestamp(value: datetime) -> str:
    """Render one UTC timestamp with fixed microseconds for every adapter."""

    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("canonical audit timestamps require UTC offset zero")
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}Z"
    )


def _canonical_json_bytes(value: object) -> bytes:
    """Encode the closed audit JSON subset as compact sorted UTF-8 JSON."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _event_payload(
    draft: AuditEventDraft,
    *,
    event_sequence: int,
    previous_event_sha256: str | None,
    recorded_at: datetime,
) -> dict[str, object]:
    payload = draft.model_dump(mode="json")
    payload.update(
        {
            "occurred_at": _canonical_timestamp(draft.occurred_at),
            "attributes": draft.attributes.model_dump(mode="json", exclude_none=True),
            "event_sequence": event_sequence,
            "previous_event_sha256": previous_event_sha256,
            "recorded_at": _canonical_timestamp(recorded_at),
        }
    )
    return payload


def _event_digest(
    draft: AuditEventDraft,
    *,
    event_sequence: int,
    previous_event_sha256: str | None,
    recorded_at: datetime,
) -> str:
    encoded = _canonical_json_bytes(
        _event_payload(
            draft,
            event_sequence=event_sequence,
            previous_event_sha256=previous_event_sha256,
            recorded_at=recorded_at,
        )
    )
    return hashlib.sha256(encoded).hexdigest()


def _draft_from_event(event: DurableAuditEvent) -> AuditEventDraft:
    return AuditEventDraft.model_validate(
        {field: getattr(event, field) for field in AuditEventDraft.model_fields}
    )


def recompute_event_sha256(event: DurableAuditEvent) -> str:
    """Recompute one event digest from the shared canonical byte contract."""

    return _event_digest(
        _draft_from_event(event),
        event_sequence=event.event_sequence,
        previous_event_sha256=event.previous_event_sha256,
        recorded_at=event.recorded_at,
    )


def verify_event_chain(events: Sequence[DurableAuditEvent]) -> bool:
    """Verify one contiguous tenant chain without trusting stored digests."""

    if not events:
        return True
    tenant_id = events[0].tenant_id
    previous: str | None = None
    expected_sequence = events[0].event_sequence
    if expected_sequence == 1 and events[0].previous_event_sha256 is not None:
        return False
    for event in events:
        if (
            event.tenant_id != tenant_id
            or event.event_sequence != expected_sequence
            or event.previous_event_sha256 != previous
        ):
            return False
        expected = recompute_event_sha256(event)
        if event.event_sha256 != expected:
            return False
        previous = event.event_sha256
        expected_sequence += 1
    return True


class InMemoryAuditLedger:
    """Process-local evidence store for tests, never a production default."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._events: dict[UUID, list[DurableAuditEvent]] = {}
        self._outbox: dict[UUID, OutboxMessage] = {}
        self._deliveries: dict[tuple[UUID, str], OutboxDelivery] = {}
        self._watermarks: dict[tuple[UUID, str], ProjectionWatermark] = {}

    def append(self, draft: AuditEventDraft) -> DurableAuditEvent:
        failed = False
        event: DurableAuditEvent | None = None
        try:
            with self._lock:
                if any(
                    draft.event_id == existing.event_id
                    for values in self._events.values()
                    for existing in values
                ):
                    raise ValueError("audit event identifier already exists")
                recorded_at = self._clock()
                if recorded_at < draft.occurred_at:
                    raise ValueError("audit occurrence is in the future")
                tenant_events = self._events.setdefault(draft.tenant_id, [])
                previous = tenant_events[-1].event_sha256 if tenant_events else None
                sequence = len(tenant_events) + 1
                digest = _event_digest(
                    draft,
                    event_sequence=sequence,
                    previous_event_sha256=previous,
                    recorded_at=recorded_at,
                )
                event = DurableAuditEvent(
                    **draft.model_dump(mode="python"),
                    event_sequence=sequence,
                    event_sha256=digest,
                    previous_event_sha256=previous,
                    recorded_at=recorded_at,
                    outbox_id=draft.event_id,
                )
                message = OutboxMessage(
                    outbox_id=draft.event_id,
                    tenant_id=draft.tenant_id,
                    matter_id=draft.matter_id,
                    event_id=draft.event_id,
                    run_id=draft.correlation.run_id,
                    correlation_id=draft.correlation.correlation_id,
                    event_sequence=sequence,
                    event_sha256=digest,
                    available_at=recorded_at,
                )
                tenant_events.append(event)
                self._outbox[message.outbox_id] = message
        except Exception:
            failed = True
        if failed or event is None:
            raise AuditUnavailable("durable audit append unavailable") from None
        return event

    def replay(self, *, tenant_id: UUID, run_id: UUID) -> tuple[DurableAuditEvent, ...]:
        with self._lock:
            tenant_events = tuple(self._events.get(tenant_id, ()))
            if not verify_event_chain(tenant_events):
                raise AuditUnavailable("audit chain verification failed") from None
            return tuple(
                event for event in tenant_events if event.correlation.run_id == run_id
            )

    def event(self, *, tenant_id: UUID, event_id: UUID) -> DurableAuditEvent:
        """Return one exact event after chain verification, or fail closed."""

        with self._lock:
            tenant_events = tuple(self._events.get(tenant_id, ()))
            if not verify_event_chain(tenant_events):
                raise AuditUnavailable("audit chain verification failed") from None
            match = next(
                (event for event in tenant_events if event.event_id == event_id),
                None,
            )
            if match is None:
                raise AuditUnavailable("audit event unavailable") from None
            return match

    def pending_outbox(self, *, tenant_id: UUID) -> tuple[OutboxMessage, ...]:
        with self._lock:
            return tuple(
                message
                for message in sorted(
                    self._outbox.values(), key=lambda item: item.event_sequence
                )
                if message.tenant_id == tenant_id
                and (message.outbox_id, message.destination) not in self._deliveries
            )

    def deliver(
        self,
        *,
        outbox_id: UUID,
        destination: str,
        handler: Callable[[OutboxMessage], None],
    ) -> OutboxDelivery:
        with self._lock:
            message = self._outbox.get(outbox_id)
            if message is None or message.destination != destination:
                raise AuditUnavailable("outbox message unavailable") from None
            key = (outbox_id, destination)
            existing = self._deliveries.get(key)
            if existing is not None:
                return OutboxDelivery(
                    **{
                        **existing.model_dump(mode="python"),
                        "first_delivery": False,
                    }
                )
            handler(message)
            idempotency_key = hashlib.sha256(
                (
                    f"{message.tenant_id}:{message.event_id}:"
                    f"{destination}:{message.event_sha256}"
                ).encode("ascii")
            ).hexdigest()
            delivery = OutboxDelivery(
                delivery_id=uuid4(),
                outbox_id=outbox_id,
                event_id=message.event_id,
                destination=destination,
                idempotency_key=idempotency_key,
                event_sha256=message.event_sha256,
                delivered_at=self._clock(),
                first_delivery=True,
            )
            self._deliveries[key] = delivery
            return delivery

    def read_watermark(
        self, *, tenant_id: UUID, projection: str
    ) -> ProjectionWatermark | None:
        """Return the durable projection watermark, or None when unset."""

        with self._lock:
            return self._watermarks.get((tenant_id, projection))

    def advance_watermark(
        self,
        *,
        tenant_id: UUID,
        projection: str,
        expected_sequence: int,
        expected_event_sha256: str | None,
        new_sequence: int,
        new_event_sha256: str,
    ) -> ProjectionWatermark:
        with self._lock:
            key = (tenant_id, projection)
            current = self._watermarks.get(key)
            current_sequence = current.event_sequence if current else 0
            current_hash = current.event_sha256 if current else None
            if (
                current_sequence != expected_sequence
                or current_hash != expected_event_sha256
            ):
                raise WatermarkConflict("projection watermark is stale") from None
            if current is not None and (
                current.event_sequence == new_sequence
                and current.event_sha256 == new_event_sha256
            ):
                return current
            target = next(
                (
                    event
                    for event in self._events.get(tenant_id, ())
                    if event.event_sequence == new_sequence
                    and event.event_sha256 == new_event_sha256
                ),
                None,
            )
            if target is None or new_sequence <= current_sequence:
                raise WatermarkConflict("target audit event is unavailable") from None
            watermark = ProjectionWatermark(
                tenant_id=tenant_id,
                projection=projection,
                event_sequence=new_sequence,
                event_sha256=new_event_sha256,
                updated_at=self._clock(),
            )
            self._watermarks[key] = watermark
            return watermark
