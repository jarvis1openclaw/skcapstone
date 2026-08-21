"""Durable decision sinks and the driver-neutral PostgreSQL audit adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sklegal_capauth import AuthorizationDecision
from sklegal_policies import PolicyDecision, RetentionDecision

from .ledger import AuditUnavailable
from .models import (
    AuditAttributes,
    AuditBoundary,
    AuditEventDraft,
    AuditOutcome,
    DurableAuditEvent,
    RunCorrelation,
)

APPEND_AUDIT_EVENT_SQL = """
SELECT sklegal_audit.append_event(
    %s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::uuid,
    %s::text, %s::text, %s::text, %s::text, %s::text, %s::text,
    %s::uuid, %s::uuid, %s::uuid, %s::text, %s::text, %s::timestamptz,
    %s::jsonb
) AS event
""".strip()


class AuditRepository(Protocol):
    def append(self, draft: AuditEventDraft) -> DurableAuditEvent: ...


class PostgresAuditRepository:
    """Execute one constant parameterized append function and validate its result."""

    def __init__(
        self,
        executor: Callable[[str, tuple[object, ...]], Mapping[str, object]],
    ) -> None:
        self._executor = executor

    def append(self, draft: AuditEventDraft) -> DurableAuditEvent:
        attributes = draft.attributes.model_dump(mode="json", exclude_none=True)
        parameters: tuple[object, ...] = (
            draft.event_id,
            draft.tenant_id,
            draft.matter_id,
            draft.principal_id,
            draft.correlation.run_id,
            draft.correlation.correlation_id,
            draft.correlation.trace_id,
            draft.correlation.span_id,
            draft.correlation.trace_flags,
            draft.boundary.value,
            draft.action,
            draft.resource_kind,
            draft.resource_id,
            draft.authorization_decision_id,
            draft.policy_decision_id,
            draft.outcome.value,
            draft.reason_code,
            draft.occurred_at,
            json.dumps(attributes, sort_keys=True, separators=(",", ":")),
        )
        failed = False
        event: DurableAuditEvent | None = None
        try:
            row = self._executor(APPEND_AUDIT_EVENT_SQL, parameters)
            payload = row.get("event")
            if isinstance(payload, str):
                event = DurableAuditEvent.model_validate_json(payload)
            else:
                event = DurableAuditEvent.model_validate_json(
                    json.dumps(payload, sort_keys=True, separators=(",", ":"))
                )
        except Exception:
            failed = True
        if failed or event is None:
            raise AuditUnavailable("PostgreSQL audit append unavailable") from None
        return event


def _safe_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


class DurableAuditSink:
    """Map reviewed CapAuth and policy decisions to the durable audit contract."""

    def __init__(
        self,
        *,
        repository: AuditRepository,
        correlation: RunCorrelation,
        boundary: AuditBoundary,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._correlation = correlation
        self._boundary = boundary
        self._clock = clock or (lambda: datetime.now(UTC))

    def record(
        self,
        decision: AuthorizationDecision | PolicyDecision | RetentionDecision,
    ) -> None:
        failed = False
        draft: AuditEventDraft | None = None
        try:
            draft = self._draft(decision)
            event = self._repository.append(draft)
            if not isinstance(event, DurableAuditEvent):
                raise TypeError("audit repository returned invalid evidence")
        except Exception:
            failed = True
        if failed or draft is None:
            raise AuditUnavailable("durable decision audit unavailable") from None

    def _draft(
        self,
        decision: AuthorizationDecision | PolicyDecision | RetentionDecision,
    ) -> AuditEventDraft:
        correlation_id = decision.correlation_id
        if correlation_id is None or correlation_id != self._correlation.correlation_id:
            raise ValueError("decision correlation does not match the run")
        if isinstance(decision, AuthorizationDecision):
            attributes = AuditAttributes(
                capability=decision.capability.value,
                audience=decision.audience.value,
                target=decision.target,
                operation=decision.operation.value,
                purpose=decision.purpose.value,
                model_route=(
                    decision.model_route.value if decision.model_route else None
                ),
                workflow_run_id=decision.workflow_run_id,
                resource_identity=decision.resource_id,
                resource_version=decision.resource_version,
                resource_sha256=decision.resource_sha256,
                event_schema="sklegal-capauth-decision/v1",
            )
            return AuditEventDraft(
                event_id=decision.decision_id,
                tenant_id=decision.tenant_id,
                matter_id=decision.matter_id,
                principal_id=decision.principal_id,
                correlation=self._correlation,
                boundary=self._boundary,
                action="capauth.authorization",
                resource_kind=decision.resource_type.value,
                resource_id=_safe_uuid(decision.resource_id),
                authorization_decision_id=decision.decision_id,
                outcome=(AuditOutcome.ALLOW if decision.allow else AuditOutcome.DENY),
                reason_code=decision.reason_code.value,
                occurred_at=self._clock(),
                attributes=attributes,
            )
        if isinstance(decision, PolicyDecision):
            attributes = AuditAttributes(
                event_schema="sklegal-policy-decision/v1",
                resource_version=decision.material_version,
                policy_revision=decision.policy_revision,
                effective_classification=(
                    decision.effective_classification.value
                    if decision.effective_classification
                    else None
                ),
                resource_identity=str(decision.material_id),
                policy_boundary=decision.boundary.value,
            )
            return AuditEventDraft(
                event_id=decision.decision_id,
                tenant_id=decision.tenant_id,
                matter_id=decision.matter_id,
                principal_id=decision.principal_id,
                correlation=self._correlation,
                boundary=self._boundary,
                action="policy.access",
                resource_kind="material",
                resource_id=decision.material_id,
                authorization_decision_id=decision.capauth_decision_id,
                policy_decision_id=decision.decision_id,
                outcome=(AuditOutcome.ALLOW if decision.allow else AuditOutcome.DENY),
                reason_code=decision.reason.value,
                occurred_at=self._clock(),
                attributes=attributes,
            )
        checked = decision
        attributes = AuditAttributes(
            event_schema="sklegal-retention-decision/v1",
            resource_version=checked.material_version,
            policy_revision=checked.policy_revision,
            resource_identity=str(checked.material_id),
        )
        return AuditEventDraft(
            event_id=checked.decision_id,
            tenant_id=checked.tenant_id,
            matter_id=checked.matter_id,
            principal_id=checked.principal_id,
            correlation=self._correlation,
            boundary=self._boundary,
            action="policy.retention",
            resource_kind="material",
            resource_id=checked.material_id,
            authorization_decision_id=checked.capauth_decision_id,
            policy_decision_id=checked.decision_id,
            outcome=(AuditOutcome.ALLOW if checked.allow else AuditOutcome.DENY),
            reason_code=checked.reason.value,
            occurred_at=self._clock(),
            attributes=attributes,
        )
