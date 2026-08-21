"""Append-only audit, transactional outbox, and protected telemetry."""

from .adapters import (
    APPEND_AUDIT_EVENT_SQL,
    AuditRepository,
    DurableAuditSink,
    PostgresAuditRepository,
)
from .ledger import (
    AuditUnavailable,
    InMemoryAuditLedger,
    WatermarkConflict,
    recompute_event_sha256,
    verify_event_chain,
)
from .models import (
    AuditAttributes,
    AuditBoundary,
    AuditEventDraft,
    AuditOutcome,
    DurableAuditEvent,
    OutboxDelivery,
    OutboxMessage,
    ProjectionWatermark,
    RunCorrelation,
    TelemetrySpan,
    TelemetryStatus,
)
from .telemetry import LocalTelemetryBuffer, TraceContextPropagator

PACKAGE_NAME = "sklegal-audit"

__all__ = [
    "APPEND_AUDIT_EVENT_SQL",
    "PACKAGE_NAME",
    "AuditAttributes",
    "AuditBoundary",
    "AuditEventDraft",
    "AuditOutcome",
    "AuditRepository",
    "AuditUnavailable",
    "DurableAuditEvent",
    "DurableAuditSink",
    "InMemoryAuditLedger",
    "LocalTelemetryBuffer",
    "OutboxDelivery",
    "OutboxMessage",
    "PostgresAuditRepository",
    "ProjectionWatermark",
    "RunCorrelation",
    "TelemetrySpan",
    "TelemetryStatus",
    "TraceContextPropagator",
    "WatermarkConflict",
    "recompute_event_sha256",
    "verify_event_chain",
]
