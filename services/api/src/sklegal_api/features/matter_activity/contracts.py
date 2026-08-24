"""Frozen contracts for the joined Matter activity projection."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafeCode = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:/@-]{0,254}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=512)]

ActivitySourceKind = Literal[
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
]


class ActivityContractModel(BaseModel):
    """Strict immutable camel-case model for this feature boundary."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must use UTC offset zero")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


class ActivitySourceRead(ActivityContractModel):
    kind: ActivitySourceKind
    source_id: UUID
    source_version: int = Field(ge=1)
    source_sha256: Sha256
    status: SafeCode
    recorded_at: datetime
    corrects_source_id: UUID | None = None
    superseded_by_source_id: UUID | None = None
    superseded_by_source_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        _require_utc(self.recorded_at)
        if self.corrects_source_id == self.source_id:
            raise ValueError("a correction cannot correct itself")
        if self.superseded_by_source_id == self.source_id:
            raise ValueError("a source cannot supersede itself")
        has_successor = self.superseded_by_source_id is not None
        if (self.status == "superseded") != has_successor:
            raise ValueError("superseded status and successor must agree")
        if has_successor != (self.superseded_by_source_version is not None):
            raise ValueError("successor identity and version must agree")
        if (
            self.superseded_by_source_version is not None
            and self.superseded_by_source_version <= self.source_version
        ):
            raise ValueError("successor version must be higher")
        return self


class ActivityTraceRead(ActivityContractModel):
    correlation_id: UUID
    causation_id: UUID | None = None
    run_id: UUID
    workflow_reference_id: UUID | None = None
    agent_run_id: UUID | None = None
    tool_call_id: UUID | None = None


class MatterActivityItemRead(ActivityContractModel):
    activity_id: UUID
    tenant_id: UUID
    matter_id: UUID
    event_sequence: int = Field(ge=1)
    event_sha256: Sha256
    previous_event_sha256: Sha256 | None = None
    chain_status: Literal["verified"] = "verified"
    action: SafeCode
    boundary: Literal["api", "workflow", "tool", "model", "human", "connector"]
    outcome: Literal["allow", "deny", "success", "failure"]
    reason_code: SafeCode
    occurred_at: datetime
    recorded_at: datetime
    actor_principal_id: UUID
    authorization_decision_id: UUID | None = None
    policy_decision_id: UUID | None = None
    trace: ActivityTraceRead
    source: ActivitySourceRead

    @model_validator(mode="after")
    def validate_activity(self) -> Self:
        _require_utc(self.occurred_at)
        _require_utc(self.recorded_at)
        if self.recorded_at < self.occurred_at:
            raise ValueError("recorded time cannot precede occurrence")
        if self.event_sequence == 1 and self.previous_event_sha256 is not None:
            raise ValueError("the first Tenant event cannot have a predecessor")
        return self


def validate_activity_supersession_graph(
    items: tuple[MatterActivityItemRead, ...],
) -> None:
    """Require each successor to be one later same-scope, same-kind version."""

    by_identity: dict[
        tuple[UUID, UUID, ActivitySourceKind, UUID, int],
        list[MatterActivityItemRead],
    ] = {}
    for item in items:
        key = (
            item.tenant_id,
            item.matter_id,
            item.source.kind,
            item.source.source_id,
            item.source.source_version,
        )
        by_identity.setdefault(key, []).append(item)
    if any(len(matches) != 1 for matches in by_identity.values()):
        raise ValueError("activity source identity and version must be unique")
    for item in items:
        successor_id = item.source.superseded_by_source_id
        successor_version = item.source.superseded_by_source_version
        if successor_id is None or successor_version is None:
            continue
        matches = by_identity.get(
            (
                item.tenant_id,
                item.matter_id,
                item.source.kind,
                successor_id,
                successor_version,
            ),
            [],
        )
        if (
            len(matches) != 1
            or matches[0].event_sequence <= item.event_sequence
            or successor_version <= item.source.source_version
        ):
            raise ValueError(
                "activity successor must be a later same-scope same-kind version"
            )


class ActivityWatermarkRead(ActivityContractModel):
    projection: Literal["matter_activity.v1"] = "matter_activity.v1"
    tenant_head_sequence: int = Field(ge=1)
    tenant_head_sha256: Sha256
    projected_sequence: int = Field(ge=1)
    projected_event_sha256: Sha256
    lag_events: int = Field(ge=0)
    verified_at: datetime

    @model_validator(mode="after")
    def validate_watermark(self) -> Self:
        _require_utc(self.verified_at)
        if self.projected_sequence > self.tenant_head_sequence:
            raise ValueError("projection cannot lead the Tenant audit chain")
        if self.lag_events != self.tenant_head_sequence - self.projected_sequence:
            raise ValueError("projection lag does not match the watermarks")
        return self


class ActivityCursorPayload(ActivityContractModel):
    version: Literal["sklegal-matter-activity-cursor/v1"] = (
        "sklegal-matter-activity-cursor/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    after_sequence: int = Field(ge=0)
    snapshot_sequence: int = Field(ge=1)
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.after_sequence > self.snapshot_sequence:
            raise ValueError("cursor is beyond its immutable snapshot")
        return self


def encode_activity_cursor(payload: ActivityCursorPayload) -> str:
    body = payload.model_dump(mode="json")
    envelope = {"payload": body, "sha256": canonical_sha256(body)}
    return (
        base64.urlsafe_b64encode(_canonical_bytes(envelope)).decode("ascii").rstrip("=")
    )


def decode_activity_cursor(value: str) -> ActivityCursorPayload:
    if not value or len(value) > 2048:
        raise ValueError("activity cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        envelope = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("activity cursor is invalid") from None
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
        raise ValueError("activity cursor is invalid")
    payload = envelope["payload"]
    if not isinstance(payload, dict) or envelope["sha256"] != canonical_sha256(payload):
        raise ValueError("activity cursor is invalid")
    return ActivityCursorPayload.model_validate(payload)


class MatterActivityPage(ActivityContractModel):
    version: Literal["sklegal-matter-activity/v1"] = "sklegal-matter-activity/v1"
    tenant_id: UUID
    matter_id: UUID
    items: tuple[MatterActivityItemRead, ...] = Field(max_length=100)
    next_cursor: str | None = None
    snapshot_sequence: int = Field(ge=1)
    snapshot_sha256: Sha256
    watermark: ActivityWatermarkRead

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        sequences = tuple(item.event_sequence for item in self.items)
        if sequences != tuple(sorted(set(sequences))):
            raise ValueError("activity page must be strictly ordered")
        for item in self.items:
            if item.tenant_id != self.tenant_id or item.matter_id != self.matter_id:
                raise ValueError("activity page contains another scope")
            if item.event_sequence > self.snapshot_sequence:
                raise ValueError("activity item is newer than the page snapshot")
        if self.snapshot_sequence != self.watermark.projected_sequence:
            raise ValueError("page snapshot must bind the projected watermark")
        if self.snapshot_sha256 != self.watermark.projected_event_sha256:
            raise ValueError("page snapshot hash must bind the projected watermark")
        if self.next_cursor is not None:
            cursor = decode_activity_cursor(self.next_cursor)
            if (
                cursor.tenant_id != self.tenant_id
                or cursor.matter_id != self.matter_id
                or cursor.snapshot_sequence != self.snapshot_sequence
                or cursor.snapshot_sha256 != self.snapshot_sha256
                or not self.items
                or cursor.after_sequence != self.items[-1].event_sequence
            ):
                raise ValueError("next cursor does not bind this page")
        return self


class ActivityExportCommand(ActivityContractModel):
    title: BoundedText
    first_event_sequence: int = Field(ge=1)
    last_event_sequence: int = Field(ge=1)
    expected_projected_sequence: int = Field(ge=1)
    expected_projected_sha256: Sha256
    expected_resource_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.first_event_sequence > self.last_event_sequence:
            raise ValueError("export event range is invalid")
        if self.last_event_sequence > self.expected_projected_sequence:
            raise ValueError("export range exceeds its projection watermark")
        if self.expected_resource_version != self.expected_projected_sequence:
            raise ValueError("resource version must bind the projection watermark")
        return self


def activity_selection_sha256(
    *,
    tenant_id: UUID,
    matter_id: UUID,
    event_ids: tuple[UUID, ...],
    event_sha256s: tuple[str, ...],
    first_sequence: int,
    last_sequence: int,
    projected_sequence: int,
    projected_sha256: str,
) -> str:
    return canonical_sha256(
        {
            "eventIds": [str(value) for value in event_ids],
            "eventSha256s": list(event_sha256s),
            "firstEventSequence": first_sequence,
            "lastEventSequence": last_sequence,
            "matterId": str(matter_id),
            "projectedEventSha256": projected_sha256,
            "projectedSequence": projected_sequence,
            "tenantId": str(tenant_id),
            "version": "sklegal-matter-activity-selection/v1",
        }
    )


def activity_export_content_sha256(
    *, title: str, selection_sha256: str, item_count: int
) -> str:
    return canonical_sha256(
        {
            "itemCount": item_count,
            "selectionSha256": selection_sha256,
            "title": title,
            "version": "sklegal-matter-activity-export/v1",
        }
    )


class ActivityExportProposalRead(ActivityContractModel):
    proposal_id: UUID
    tenant_id: UUID
    matter_id: UUID
    work_product_kind: Literal["report"] = "report"
    status: Literal["proposed"] = "proposed"
    title: BoundedText
    first_event_sequence: int = Field(ge=1)
    last_event_sequence: int = Field(ge=1)
    item_count: int = Field(ge=1, le=10_000)
    event_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10_000)
    event_sha256s: tuple[Sha256, ...] = Field(
        alias="eventSha256s", min_length=1, max_length=10_000
    )
    selection_sha256: Sha256
    content_sha256: Sha256
    projected_sequence: int = Field(ge=1)
    projected_event_sha256: Sha256
    tenant_head_sequence: int = Field(ge=1)
    tenant_head_sha256: Sha256
    proposed_by_principal_id: UUID
    authorization_decision_id: UUID
    policy_decision_id: UUID
    idempotency_key_sha256: Sha256
    approval_id: None = None
    dispatch_state: Literal["not_requested"] = "not_requested"
    created_at: datetime

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        _require_utc(self.created_at)
        if self.first_event_sequence > self.last_event_sequence:
            raise ValueError("export proposal event range is invalid")
        if self.tenant_head_sequence < self.projected_sequence:
            raise ValueError("export proposal Tenant head precedes its projection")
        if (
            len(self.event_ids) != self.item_count
            or len(self.event_sha256s) != self.item_count
        ):
            raise ValueError("export proposal count does not match its selection")
        selection = activity_selection_sha256(
            tenant_id=self.tenant_id,
            matter_id=self.matter_id,
            event_ids=self.event_ids,
            event_sha256s=self.event_sha256s,
            first_sequence=self.first_event_sequence,
            last_sequence=self.last_event_sequence,
            projected_sequence=self.projected_sequence,
            projected_sha256=self.projected_event_sha256,
        )
        if selection != self.selection_sha256:
            raise ValueError("export proposal selection hash is invalid")
        if (
            activity_export_content_sha256(
                title=self.title,
                selection_sha256=selection,
                item_count=self.item_count,
            )
            != self.content_sha256
        ):
            raise ValueError("export proposal content hash is invalid")
        return self


ACTIVITY_SCHEMA_REVISION = canonical_sha256(
    {"contract": "sklegal-matter-activity-export-envelope/v1"}
)
ACTIVITY_PROJECTION_REVISION = canonical_sha256(
    {"projection": "matter_activity.v1", "contract": "v2-mvp"}
)


class ActivityExportProvenanceRead(BaseModel):
    """Frozen provenance required by the canonical activity operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recorded_at: datetime
    source_record_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    source_hashes: tuple[Sha256, ...] = Field(min_length=1, max_length=10_000)
    schema_revision: Sha256
    projection_revision: Sha256
    supersedes_record_id: None = None
    watermark: ActivityWatermarkRead
    export_sha256: Sha256
    manifest_sha256: Sha256
    source_chain_heads: tuple[Sha256, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        _require_utc(self.recorded_at)
        if len(set(self.source_record_ids)) != len(self.source_record_ids):
            raise ValueError("source record identities must be unique")
        if len(set(self.source_hashes)) != len(self.source_hashes):
            raise ValueError("source hashes must be unique")
        if self.export_sha256 not in self.source_hashes:
            raise ValueError("export hash must remain in provenance")
        return self


class ActivityExportResponseRead(BaseModel):
    """Canonical V2 response context plus mutation receipt and proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: Literal["create_activity_export"] = "create_activity_export"
    tenant_id: UUID
    matter_id: UUID
    resource_id: UUID
    correlation_id: UUID
    schema_revision: Sha256
    projection_revision: Sha256
    provenance: ActivityExportProvenanceRead
    idempotency_key: Annotated[str, Field(min_length=16, max_length=200)]
    request_sha256: Sha256
    resource_version: int = Field(ge=1)
    audit_id: UUID
    outbox_id: UUID
    proposal: ActivityExportProposalRead

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if (
            self.tenant_id != self.proposal.tenant_id
            or self.matter_id != self.proposal.matter_id
            or self.resource_id != self.proposal.proposal_id
            or self.resource_version != 1
            or self.schema_revision != self.provenance.schema_revision
            or self.projection_revision != self.provenance.projection_revision
            or self.provenance.export_sha256 != self.proposal.content_sha256
        ):
            raise ValueError("activity export receipt is inconsistent")
        return self
