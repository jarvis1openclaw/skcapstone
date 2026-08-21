"""Strict content-free audit, outbox, and telemetry value objects."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
TraceFlags = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{2}$")]
SafeCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9][a-z0-9._:/@-]*$",
    ),
]
ResourceKind = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._:/@-]*$",
    ),
]
OpaqueResourceIdentity = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:/@-]*$",
    ),
]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("audit timestamps must use UTC offset zero")
    return value


def _contains_nil_uuid(value: object) -> bool:
    if isinstance(value, UUID):
        return value.int == 0
    if isinstance(value, BaseModel):
        return any(_contains_nil_uuid(item) for item in value.__dict__.values())
    if isinstance(value, Mapping):
        return any(_contains_nil_uuid(item) for item in value.values())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_nil_uuid(item) for item in value)
    return False


class AuditValue(BaseModel):
    """Immutable strict value used at durable evidence boundaries."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
        revalidate_instances="always",
    )

    @model_validator(mode="after")
    def reject_nil_identifiers(self) -> Self:
        if _contains_nil_uuid(self.__dict__):
            raise ValueError("audit identifiers cannot be nil UUIDs")
        return self

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update:
            raise ValueError("unvalidated audit copy updates are disabled")
        return super().model_copy(deep=deep)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if include is not None or exclude is not None or update:
            raise ValueError("unvalidated audit copy changes are disabled")
        return self.model_copy(deep=deep)

    def __replace__(self, **changes: Any) -> Self:
        if changes:
            raise ValueError("unvalidated audit replacement is disabled")
        return self.model_copy()

    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        del _fields_set, values
        raise ValueError("unvalidated audit construction is disabled")


class AuditBoundary(StrEnum):
    API = "api"
    WORKFLOW = "workflow"
    TOOL = "tool"
    MODEL = "model"
    HUMAN = "human"
    CONNECTOR = "connector"


class AuditOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    SUCCESS = "success"
    FAILURE = "failure"


class TelemetryStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class RunCorrelation(AuditValue):
    """One run identity plus exact W3C trace context."""

    run_id: UUID
    correlation_id: UUID
    trace_id: TraceId
    span_id: SpanId
    trace_flags: TraceFlags = "01"

    @model_validator(mode="after")
    def reject_zero_trace_components(self) -> Self:
        if set(self.trace_id) == {"0"} or set(self.span_id) == {"0"}:
            raise ValueError("trace identifiers cannot be all zero")
        return self

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    def child(self, *, span_id: str) -> RunCorrelation:
        return RunCorrelation(
            run_id=self.run_id,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            span_id=span_id,
            trace_flags=self.trace_flags,
        )


class AuditAttributes(AuditValue):
    """Closed safe metadata fields shared by audit and telemetry.

    There is deliberately no prompt, document, message, query, tool argument,
    model output, credential, bearer, exception, or arbitrary attribute field.
    """

    capability: SafeCode | None = None
    audience: SafeCode | None = None
    target: SafeCode | None = None
    operation: SafeCode | None = None
    purpose: SafeCode | None = None
    model_route: SafeCode | None = None
    workflow_run_id: SafeCode | None = None
    connector_kind: SafeCode | None = None
    error_code: SafeCode | None = None
    event_schema: SafeCode | None = None
    effective_classification: SafeCode | None = None
    resource_identity: OpaqueResourceIdentity | None = None
    policy_boundary: SafeCode | None = None
    resource_version: int | None = Field(default=None, ge=1)
    resource_sha256: Sha256 | None = None
    policy_revision: Sha256 | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)
    retry_count: int | None = Field(default=None, ge=0, le=1000)


class AuditEventDraft(AuditValue):
    event_id: UUID
    tenant_id: UUID
    matter_id: UUID | None = None
    principal_id: UUID
    correlation: RunCorrelation
    boundary: AuditBoundary
    action: SafeCode
    resource_kind: ResourceKind
    resource_id: UUID | None = None
    authorization_decision_id: UUID | None = None
    policy_decision_id: UUID | None = None
    outcome: AuditOutcome
    reason_code: SafeCode
    occurred_at: datetime
    attributes: AuditAttributes = Field(default_factory=AuditAttributes)

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        require_utc(self.occurred_at)
        return self


class DurableAuditEvent(AuditEventDraft):
    event_sequence: int = Field(ge=1)
    event_sha256: Sha256
    previous_event_sha256: Sha256 | None = None
    recorded_at: datetime
    outbox_id: UUID

    @model_validator(mode="after")
    def validate_server_evidence(self) -> Self:
        require_utc(self.recorded_at)
        if self.recorded_at < self.occurred_at:
            raise ValueError("recorded audit time cannot precede occurrence")
        if (self.event_sequence == 1) != (self.previous_event_sha256 is None):
            raise ValueError("audit predecessor shape disagrees with sequence")
        if self.outbox_id != self.event_id:
            raise ValueError("audit outbox identifier must bind the exact event")
        return self


class OutboxMessage(AuditValue):
    outbox_id: UUID
    tenant_id: UUID
    matter_id: UUID | None = None
    event_id: UUID
    run_id: UUID
    correlation_id: UUID
    event_sequence: int = Field(ge=1)
    event_sha256: Sha256
    destination: SafeCode = "audit.local"
    available_at: datetime

    @model_validator(mode="after")
    def validate_available_time(self) -> Self:
        require_utc(self.available_at)
        if self.outbox_id != self.event_id:
            raise ValueError("outbox message must bind one exact audit event")
        return self


class OutboxDelivery(AuditValue):
    delivery_id: UUID
    outbox_id: UUID
    event_id: UUID
    destination: SafeCode
    idempotency_key: Sha256
    event_sha256: Sha256
    delivered_at: datetime
    first_delivery: bool

    @model_validator(mode="after")
    def validate_delivery_time(self) -> Self:
        require_utc(self.delivered_at)
        return self


class ProjectionWatermark(AuditValue):
    tenant_id: UUID
    projection: SafeCode
    event_sequence: int = Field(ge=1)
    event_sha256: Sha256
    updated_at: datetime

    @model_validator(mode="after")
    def validate_update_time(self) -> Self:
        require_utc(self.updated_at)
        return self


class TelemetrySpan(AuditValue):
    correlation: RunCorrelation
    boundary: AuditBoundary
    name: SafeCode
    status: TelemetryStatus
    started_at: datetime
    ended_at: datetime
    attributes: AuditAttributes = Field(default_factory=AuditAttributes)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.started_at)
        require_utc(self.ended_at)
        if self.ended_at < self.started_at:
            raise ValueError("telemetry span interval cannot move backward")
        return self
