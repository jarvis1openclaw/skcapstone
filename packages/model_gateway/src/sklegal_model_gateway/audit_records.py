"""Attributable model-gateway audit records (SKL-S3-10).

One audit record per transported call: identifiers and policy references
only. The record never carries prompt text, source documents, secrets, or
raw capability tokens, so appending it to logs, metrics, or evidence cannot
leak protected content. A transport whose audit sink is unavailable fails
closed before the call leaves SKLegal.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import AuditUnavailableError
from .models import Proposal, ProposalRequest

AUDIT_RECORD_SCHEMA = "sklegal-model-gateway-audit/v1"

FORBIDDEN_KEY_FRAGMENTS = (
    "prompt",
    "api_key",
    "secret",
    "capability_token",
    "token_value",
)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("audit timestamps must be UTC")
    return value


class AuditRecord(BaseModel):
    """Attributable, content-free record of one model call."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, protected_namespaces=(), populate_by_name=True
    )

    schema_marker: str = Field(
        alias="schema", pattern=f"^{AUDIT_RECORD_SCHEMA}$"
    )
    recorded_at: datetime
    request_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    matter_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    classification: str = Field(min_length=1)
    transport_profile_id: str | None = None
    transport_kind: str | None = None
    served_model: str | None = None
    bucket_id: str | None = None
    bucket_member_id: str | None = None
    catalog_generation: str | None = None
    gateway_policy_revision: str | None = None
    policy_revision: str
    egress_rule: str
    outcome: str = Field(min_length=1)
    proposal_sha256: str | None = None

    @model_validator(mode="after")
    def validate_no_leak(self) -> AuditRecord:
        _require_utc(self.recorded_at)
        for key in self.model_dump():
            for fragment in FORBIDDEN_KEY_FRAGMENTS:
                if fragment in key and key not in {
                    "gateway_policy_revision",
                    "policy_revision",
                }:
                    raise ValueError(
                        f"audit record key leaks protected content: {key}"
                    )
        return self

    @classmethod
    def from_proposal(
        cls,
        *,
        request: ProposalRequest,
        proposal: Proposal,
        recorded_at: datetime,
    ) -> AuditRecord:
        transport = proposal.evidence.transport
        return cls(
            schema=AUDIT_RECORD_SCHEMA,
            recorded_at=recorded_at,
            request_id=request.request_id,
            route_id=proposal.route_id,
            provider=str(proposal.provider),
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            purpose=request.purpose,
            classification=str(proposal.policy.classification),
            transport_profile_id=(
                transport.transport_profile_id if transport else None
            ),
            transport_kind=(str(transport.transport_kind) if transport else None),
            served_model=transport.served_model if transport else None,
            bucket_id=transport.bucket_id if transport else None,
            bucket_member_id=transport.bucket_member_id if transport else None,
            catalog_generation=transport.catalog_generation if transport else None,
            gateway_policy_revision=(
                transport.gateway_policy_revision if transport else None
            ),
            policy_revision=proposal.policy.policy_revision,
            egress_rule=proposal.policy.egress_rule,
            outcome="proposal_validated",
            proposal_sha256=proposal.payload_sha256,
        )

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )


class AuditRecorder(Protocol):
    """Append one attributable record; raise to fail closed."""

    def append(self, record: AuditRecord) -> None: ...


class InMemoryAuditRecorder:
    """Thread-safe test sink that keeps records in process memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self.records.append(record)


class FailingAuditRecorder:
    """Sink whose outage must deterministically deny protected calls."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("audit backend unavailable")

    def append(self, record: AuditRecord) -> None:
        raise AuditUnavailableError("model-gateway audit sink is unavailable")


def utc_now() -> datetime:
    return datetime.now(UTC)
