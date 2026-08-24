"""Fail-closed Matter activity query and export proposal service."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, model_validator

from .contracts import (
    ACTIVITY_PROJECTION_REVISION,
    ACTIVITY_SCHEMA_REVISION,
    ActivityCursorPayload,
    ActivityExportCommand,
    ActivityExportProposalRead,
    ActivityExportProvenanceRead,
    ActivityExportResponseRead,
    MatterActivityPage,
    Sha256,
    activity_export_content_sha256,
    activity_selection_sha256,
    canonical_sha256,
    decode_activity_cursor,
    encode_activity_cursor,
)
from .store import (
    ActivityAuditFact,
    ActivityIdempotencyConflict,
    ActivityOutboxMessage,
    ActivityStoreUnavailable,
    MatterActivityStore,
)

ActivityOperation = Literal["read", "create_activity_export"]


class ActivityServiceError(RuntimeError):
    """Sanitized activity failure from a closed error vocabulary."""

    def __init__(
        self,
        code: Literal[
            "access_denied",
            "policy_unavailable",
            "dependency_unavailable",
            "precondition_failed",
            "idempotency_conflict",
            "validation_failed",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class ActivityAccessContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    matter_id: UUID
    resource_id: UUID
    principal_id: UUID
    capability: Literal["audit.read"]
    purpose: Literal["audit_review"]
    authorization_decision_id: UUID
    correlation_id: UUID
    credential_expires_at: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def validate_expiry(self) -> Self:
        if self.credential_expires_at.tzinfo is None:
            raise ValueError("credential expiry must be timezone aware")
        if self.resource_id != self.matter_id:
            raise ValueError("activity resource must be the Matter")
        return self


class ActivityPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    operation: ActivityOperation
    allowed: bool
    revision: Sha256
    evaluated_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (
            self.evaluated_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.valid_until <= self.evaluated_at
        ):
            raise ValueError("activity policy validity window is invalid")
        return self


class ActivityPolicyUnavailable(RuntimeError):
    """The Matter policy service has no current answer."""


class ActivityPolicy(Protocol):
    def authorize(
        self,
        *,
        context: ActivityAccessContext,
        operation: ActivityOperation,
        now: datetime,
    ) -> ActivityPolicyDecision: ...


class StaticActivityPolicy:
    """Deterministic policy used only by the public-synthetic composition."""

    def __init__(
        self,
        *,
        memberships: set[tuple[UUID, UUID, UUID]],
        revision: str,
        valid_until: datetime,
    ) -> None:
        self.memberships = memberships
        self.revision = revision
        self.valid_until = valid_until
        self.available = True
        self.calls: list[tuple[ActivityOperation, UUID, UUID, UUID]] = []

    def authorize(
        self,
        *,
        context: ActivityAccessContext,
        operation: ActivityOperation,
        now: datetime,
    ) -> ActivityPolicyDecision:
        if not self.available:
            raise ActivityPolicyUnavailable("activity policy unavailable")
        self.calls.append(
            (operation, context.tenant_id, context.matter_id, context.principal_id)
        )
        return ActivityPolicyDecision(
            decision_id=uuid5(
                NAMESPACE_URL,
                f"activity-policy:{self.revision}:{context.tenant_id}:"
                f"{context.matter_id}:{context.principal_id}:{operation}",
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            allowed=(
                context.tenant_id,
                context.matter_id,
                context.principal_id,
            )
            in self.memberships,
            revision=self.revision,
            evaluated_at=now,
            valid_until=self.valid_until,
        )


class MatterActivityService:
    """Authorize before reading and only propose immutable hashed exports."""

    def __init__(
        self,
        *,
        store: MatterActivityStore,
        policy: ActivityPolicy,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock

    def _authorize(
        self,
        *,
        context: ActivityAccessContext,
        matter_id: UUID,
        operation: ActivityOperation,
    ) -> ActivityPolicyDecision:
        now = self._clock()
        expected = ("audit.read", "audit_review")
        if (
            context.revoked
            or context.credential_expires_at <= now
            or context.matter_id != matter_id
            or (context.capability, context.purpose) != expected
        ):
            raise ActivityServiceError("access_denied")
        try:
            decision = self._policy.authorize(
                context=context,
                operation=operation,
                now=now,
            )
        except Exception:
            raise ActivityServiceError("policy_unavailable") from None
        if decision.valid_until <= now:
            raise ActivityServiceError("policy_unavailable")
        if (
            not decision.allowed
            or decision.tenant_id != context.tenant_id
            or decision.matter_id != matter_id
            or decision.principal_id != context.principal_id
            or decision.operation != operation
            or decision.evaluated_at > now
        ):
            raise ActivityServiceError("access_denied")
        return decision

    def list_activity(
        self,
        *,
        context: ActivityAccessContext,
        matter_id: UUID,
        cursor: str | None,
        limit: int,
    ) -> MatterActivityPage:
        self._authorize(context=context, matter_id=matter_id, operation="read")
        if not 1 <= limit <= 100:
            raise ActivityServiceError("validation_failed")
        after_sequence = 0
        snapshot_sequence: int | None = None
        snapshot_sha256: str | None = None
        if cursor is not None:
            try:
                decoded = decode_activity_cursor(cursor)
            except ValueError:
                raise ActivityServiceError("validation_failed") from None
            if decoded.tenant_id != context.tenant_id or decoded.matter_id != matter_id:
                raise ActivityServiceError("validation_failed")
            after_sequence = decoded.after_sequence
            snapshot_sequence = decoded.snapshot_sequence
            snapshot_sha256 = decoded.snapshot_sha256
        try:
            items, has_more, watermark = self._store.read_page(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                after_sequence=after_sequence,
                snapshot_sequence=snapshot_sequence,
                snapshot_sha256=snapshot_sha256,
                limit=limit,
                verified_at=self._clock(),
            )
        except Exception:
            raise ActivityServiceError("dependency_unavailable") from None
        next_cursor = None
        if has_more and items:
            next_cursor = encode_activity_cursor(
                ActivityCursorPayload(
                    tenant_id=context.tenant_id,
                    matter_id=matter_id,
                    after_sequence=items[-1].event_sequence,
                    snapshot_sequence=watermark.projected_sequence,
                    snapshot_sha256=watermark.projected_event_sha256,
                )
            )
        return MatterActivityPage(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            items=items,
            next_cursor=next_cursor,
            snapshot_sequence=watermark.projected_sequence,
            snapshot_sha256=watermark.projected_event_sha256,
            watermark=watermark,
        )

    def propose_export(
        self,
        *,
        context: ActivityAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: ActivityExportCommand,
    ) -> ActivityExportResponseRead:
        policy = self._authorize(
            context=context,
            matter_id=matter_id,
            operation="create_activity_export",
        )
        if not 16 <= len(idempotency_key) <= 200:
            raise ActivityServiceError("validation_failed")
        try:
            items, watermark = self._store.read_selection(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                first_sequence=command.first_event_sequence,
                last_sequence=command.last_event_sequence,
                projected_sequence=command.expected_projected_sequence,
                projected_sha256=command.expected_projected_sha256,
                verified_at=self._clock(),
            )
        except Exception:
            raise ActivityServiceError("precondition_failed") from None
        event_ids = tuple(item.activity_id for item in items)
        event_sha256s = tuple(item.event_sha256 for item in items)
        selection_sha256 = activity_selection_sha256(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            event_ids=event_ids,
            event_sha256s=event_sha256s,
            first_sequence=command.first_event_sequence,
            last_sequence=command.last_event_sequence,
            projected_sequence=watermark.projected_sequence,
            projected_sha256=watermark.projected_event_sha256,
        )
        key_sha256 = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        request_sha256 = canonical_sha256(
            {
                "command": command.model_dump(mode="json", by_alias=True),
                "authenticatedPrincipalId": str(context.principal_id),
                "authorizationDecisionId": str(context.authorization_decision_id),
                "capability": context.capability,
                "matterId": str(matter_id),
                "operation": "create_activity_export",
                "policyDecisionId": str(policy.decision_id),
                "policyRevision": policy.revision,
                "purpose": context.purpose,
                "resourceId": str(context.resource_id),
                "tenantId": str(context.tenant_id),
            }
        )
        proposal_id = uuid5(
            NAMESPACE_URL,
            "matter-activity-export:"
            f"{context.tenant_id}:{matter_id}:{context.principal_id}:{key_sha256}",
        )
        occurred_at = self._clock()
        proposal = ActivityExportProposalRead(
            proposal_id=proposal_id,
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            title=command.title,
            first_event_sequence=command.first_event_sequence,
            last_event_sequence=command.last_event_sequence,
            item_count=len(items),
            event_ids=event_ids,
            eventSha256s=event_sha256s,
            selection_sha256=selection_sha256,
            content_sha256=activity_export_content_sha256(
                title=command.title,
                selection_sha256=selection_sha256,
                item_count=len(items),
            ),
            projected_sequence=watermark.projected_sequence,
            projected_event_sha256=watermark.projected_event_sha256,
            tenant_head_sequence=watermark.tenant_head_sequence,
            tenant_head_sha256=watermark.tenant_head_sha256,
            proposed_by_principal_id=context.principal_id,
            authorization_decision_id=context.authorization_decision_id,
            policy_decision_id=policy.decision_id,
            idempotency_key_sha256=key_sha256,
            created_at=occurred_at,
        )
        audit_fact_id = uuid5(NAMESPACE_URL, f"{proposal_id}:audit")
        outbox_id = audit_fact_id
        try:
            stored, _replayed = self._store.append_export(
                proposal=proposal,
                policy_revision=policy.revision,
                request_sha256=request_sha256,
                audit_fact=ActivityAuditFact(
                    fact_id=audit_fact_id,
                    tenant_id=context.tenant_id,
                    matter_id=matter_id,
                    principal_id=context.principal_id,
                    action="matter.activity_export.created",
                    proposal_id=proposal_id,
                    selection_sha256=selection_sha256,
                    occurred_at=occurred_at,
                ),
                outbox=ActivityOutboxMessage(
                    message_id=outbox_id,
                    tenant_id=context.tenant_id,
                    matter_id=matter_id,
                    audit_fact_id=audit_fact_id,
                    proposal_id=proposal_id,
                    selection_sha256=selection_sha256,
                ),
            )
        except ActivityIdempotencyConflict:
            raise ActivityServiceError("idempotency_conflict") from None
        except ActivityStoreUnavailable:
            raise ActivityServiceError("dependency_unavailable") from None
        stable_watermark = watermark.model_copy(
            update={
                "tenant_head_sequence": stored.tenant_head_sequence,
                "tenant_head_sha256": stored.tenant_head_sha256,
                "projected_sequence": stored.projected_sequence,
                "projected_event_sha256": stored.projected_event_sha256,
                "lag_events": stored.tenant_head_sequence - stored.projected_sequence,
                "verified_at": stored.created_at,
            }
        )
        manifest_sha256 = canonical_sha256(
            {
                "exportSha256": stored.content_sha256,
                "selectionSha256": stored.selection_sha256,
                "sourceRecordIds": [str(value) for value in stored.event_ids],
                "sourceHashes": list(stored.event_sha256s),
                "watermark": stable_watermark.model_dump(mode="json", by_alias=True),
            }
        )
        provenance = ActivityExportProvenanceRead(
            recorded_at=stored.created_at,
            source_record_ids=tuple(str(value) for value in stored.event_ids),
            source_hashes=(*stored.event_sha256s, stored.content_sha256),
            schema_revision=ACTIVITY_SCHEMA_REVISION,
            projection_revision=ACTIVITY_PROJECTION_REVISION,
            watermark=stable_watermark,
            export_sha256=stored.content_sha256,
            manifest_sha256=manifest_sha256,
            source_chain_heads=(stored.tenant_head_sha256,),
        )
        return ActivityExportResponseRead(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            resource_id=stored.proposal_id,
            correlation_id=context.correlation_id,
            schema_revision=ACTIVITY_SCHEMA_REVISION,
            projection_revision=ACTIVITY_PROJECTION_REVISION,
            provenance=provenance,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            resource_version=1,
            audit_id=audit_fact_id,
            outbox_id=outbox_id,
            proposal=stored,
        )
