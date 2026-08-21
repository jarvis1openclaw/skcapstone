"""W3C trace propagation and bounded local content-free telemetry."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID

from .ledger import AuditUnavailable
from .models import RunCorrelation, TelemetrySpan

_TRACEPARENT = re.compile(
    r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-"
    r"(?P<trace_flags>[0-9a-f]{2})$"
)


class TraceContextPropagator:
    """Propagate only traceparent, never baggage or protected attributes."""

    @staticmethod
    def inject(correlation: RunCorrelation) -> dict[str, str]:
        return {"traceparent": correlation.traceparent}

    @staticmethod
    def extract(
        headers: Mapping[str, str], *, run_id: UUID, correlation_id: UUID
    ) -> RunCorrelation:
        value = headers.get("traceparent")
        match = _TRACEPARENT.fullmatch(value or "")
        if match is None:
            raise ValueError("traceparent is malformed or unsupported")
        return RunCorrelation(
            run_id=run_id,
            correlation_id=correlation_id,
            trace_id=match.group("trace_id"),
            span_id=match.group("span_id"),
            trace_flags=match.group("trace_flags"),
        )


class LocalTelemetryBuffer:
    """Bounded process-local telemetry with a trusted ingestion-time TTL."""

    def __init__(
        self,
        *,
        retention: timedelta,
        max_spans: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention <= timedelta(0) or retention > timedelta(days=7):
            raise ValueError("local telemetry retention must be within seven days")
        if not 1 <= max_spans <= 100_000:
            raise ValueError("local telemetry capacity is invalid")
        self._retention = retention
        self._max_spans = max_spans
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._spans: list[tuple[datetime, TelemetrySpan]] = []

    def _purge(self, now: datetime) -> None:
        cutoff = now - self._retention
        self._spans = [item for item in self._spans if item[0] > cutoff]

    def record(self, span: TelemetrySpan) -> None:
        checked = TelemetrySpan.model_validate(span, strict=True)
        with self._lock:
            now = self._clock()
            self._purge(now)
            self._spans.append((now, checked))
            if len(self._spans) > self._max_spans:
                self._spans = self._spans[-self._max_spans :]

    def export_local(self) -> tuple[TelemetrySpan, ...]:
        with self._lock:
            self._purge(self._clock())
            try:
                return tuple(
                    TelemetrySpan.model_validate(
                        span.model_dump(mode="python", warnings="none"),
                        strict=True,
                    )
                    for _, span in self._spans
                )
            except Exception:
                raise AuditUnavailable("local telemetry unavailable") from None
