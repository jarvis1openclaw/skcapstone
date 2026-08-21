"""Current CapAuth-bound policy gateways that load no payload before allow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from sklegal_capauth import AuthorizedContext
from sklegal_domain import DataClassification

from .backends import (
    CapAuthCurrentStateVerifier,
    CurrentAuthorizationExpired,
    CurrentAuthorizationReplayed,
    CurrentAuthorizationStale,
    CurrentAuthorizationUnavailable,
    PolicyAuditSink,
    PolicyBackend,
)
from .engine import PolicyEngine
from .models import (
    PolicyAccessRequest,
    PolicyAuthorizedContext,
    PolicyBoundaryRequirement,
    PolicyDecision,
    PolicyReason,
    RetentionBoundaryRequirement,
    RetentionDecision,
    RetentionRequest,
    Sha256,
)

ResultT = TypeVar("ResultT")
_FAILED_EVALUATION_AT = datetime(1970, 1, 1, tzinfo=UTC)


class PolicyDenied(PermissionError):
    """Sanitized denial carrying only an audit-safe policy decision."""

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        super().__init__("policy authorization denied")


class RetentionDenied(PermissionError):
    """Sanitized denial of retention eligibility, never a deletion decision."""

    def __init__(self, decision: RetentionDecision) -> None:
        self.decision = decision
        super().__init__("retention eligibility denied")


def _current_authorization_reason(error: Exception) -> PolicyReason:
    if isinstance(error, CurrentAuthorizationExpired):
        return PolicyReason.CAPAUTH_EXPIRED
    if isinstance(error, CurrentAuthorizationReplayed):
        return PolicyReason.CAPAUTH_REPLAYED
    if isinstance(error, CurrentAuthorizationStale):
        return PolicyReason.CAPAUTH_STALE
    return PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE


def _invocation_digest(
    authorized: AuthorizedContext,
    requirement: PolicyBoundaryRequirement | RetentionBoundaryRequirement,
    request: PolicyAccessRequest | RetentionRequest,
) -> Sha256:
    encoded = json.dumps(
        {
            "schema": "sklegal-policy-invocation/v1",
            "capauth_decision_id": str(authorized.decision.decision_id),
            "requirement": requirement.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PolicyGateway:
    def __init__(
        self,
        *,
        backend: PolicyBackend,
        audit_sink: PolicyAuditSink,
        current_authorization: CapAuthCurrentStateVerifier,
        engine: PolicyEngine | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._audit_sink = audit_sink
        self._current_authorization = current_authorization
        self._engine = engine or PolicyEngine()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _current_request(self, request: PolicyAccessRequest) -> PolicyAccessRequest:
        return PolicyAccessRequest.model_validate(
            {**request.model_dump(mode="python"), "evaluated_at": self._clock()}
        )

    def _decision(
        self,
        authorized: AuthorizedContext,
        request: PolicyAccessRequest,
        reason: PolicyReason,
        *,
        classification: DataClassification | None = None,
        policy_revision: str | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            capauth_decision_id=authorized.decision.decision_id,
            correlation_id=authorized.decision.correlation_id,
            principal_id=request.principal_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            material_id=request.material_id,
            material_version=request.material_version,
            boundary=request.boundary,
            allow=reason == PolicyReason.ALLOW,
            reason=reason,
            effective_classification=classification,
            policy_revision=policy_revision,
            evaluated_at=request.evaluated_at,
        )

    def _record_or_deny(
        self,
        authorized: AuthorizedContext,
        request: PolicyAccessRequest,
        decision: PolicyDecision,
    ) -> None:
        try:
            self._audit_sink.record(decision)
        except Exception:
            raise PolicyDenied(
                self._decision(
                    authorized,
                    request,
                    PolicyReason.AUDIT_UNAVAILABLE,
                    classification=decision.effective_classification,
                    policy_revision=decision.policy_revision,
                )
            ) from None

    def _deny(
        self,
        authorized: AuthorizedContext,
        request: PolicyAccessRequest,
        reason: PolicyReason,
        *,
        policy_revision: str | None = None,
    ) -> None:
        decision = self._decision(
            authorized,
            request,
            reason,
            policy_revision=policy_revision,
        )
        self._record_or_deny(authorized, request, decision)
        raise PolicyDenied(decision) from None

    def authorize(
        self,
        authorized: AuthorizedContext,
        requirement: PolicyBoundaryRequirement,
        request: PolicyAccessRequest,
    ) -> PolicyAuthorizedContext:
        denied_decision: PolicyDecision | None = None
        try:
            return self._authorize(authorized, requirement, request)
        except PolicyDenied as exc:
            denied_decision = exc.decision
        if denied_decision is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("policy denial lost its sanitized decision")
        raise PolicyDenied(denied_decision) from None

    def invoke[InvokeT](
        self,
        authorized: AuthorizedContext,
        requirement: PolicyBoundaryRequirement,
        request: PolicyAccessRequest,
        handler: Callable[[PolicyAuthorizedContext], InvokeT],
    ) -> InvokeT:
        context = self.authorize(authorized, requirement, request)
        return handler(context)

    def _authorize(
        self,
        authorized: AuthorizedContext,
        requirement: PolicyBoundaryRequirement,
        request: PolicyAccessRequest,
    ) -> PolicyAuthorizedContext:
        try:
            current_request = self._current_request(request)
        except Exception:
            failed_request = PolicyAccessRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "evaluated_at": _FAILED_EVALUATION_AT,
                }
            )
            self._deny(authorized, failed_request, PolicyReason.POLICY_UNAVAILABLE)

        invocation_digest = _invocation_digest(
            authorized,
            requirement,
            current_request,
        )
        try:
            self._current_authorization.reserve(
                authorized,
                invocation_digest=invocation_digest,
                evaluated_at=current_request.evaluated_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny(
                authorized,
                current_request,
                _current_authorization_reason(exc),
            )
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
            )

        try:
            expected_grant = requirement.bind(current_request)
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.CAPAUTH_SCOPE_MISMATCH,
            )
        if (
            authorized.principal.principal_id != current_request.principal_id
            or authorized.principal.tenant_id != current_request.tenant_id
            or authorized.grant != expected_grant
        ):
            self._deny(
                authorized,
                current_request,
                PolicyReason.CAPAUTH_SCOPE_MISMATCH,
            )

        try:
            facts = self._backend.load(current_request)
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.POLICY_UNAVAILABLE,
            )
        try:
            after_load_request = self._current_request(request)
            if after_load_request.evaluated_at < current_request.evaluated_at:
                raise ValueError("trusted clock moved backwards")
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.POLICY_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        try:
            self._current_authorization.verify_current(
                authorized,
                evaluated_at=after_load_request.evaluated_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny(
                authorized,
                after_load_request,
                _current_authorization_reason(exc),
                policy_revision=facts.policy_revision,
            )
        except Exception:
            self._deny(
                authorized,
                after_load_request,
                PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        try:
            reason, classification = self._engine.reason_for_access(
                after_load_request,
                facts,
            )
        except Exception:
            self._deny(
                authorized,
                after_load_request,
                PolicyReason.POLICY_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        decision = self._decision(
            authorized,
            after_load_request,
            reason,
            classification=classification,
            policy_revision=facts.policy_revision,
        )
        self._record_or_deny(authorized, after_load_request, decision)
        if not decision.allow or classification is None:
            raise PolicyDenied(decision) from None
        partition_material = "\x00".join(
            (
                "sklegal-policy-cache/v2",
                str(after_load_request.principal_id),
                str(after_load_request.tenant_id),
                str(after_load_request.matter_id),
                str(after_load_request.material_id),
                str(after_load_request.material_version),
                after_load_request.material_sha256,
                after_load_request.purpose.value,
                after_load_request.boundary.value,
                classification.value,
                expected_grant.audience.value,
                expected_grant.target,
                expected_grant.capability.value,
                expected_grant.operation.value,
                expected_grant.resource_type.value,
                (
                    expected_grant.model_route.value
                    if expected_grant.model_route is not None
                    else "none"
                ),
                expected_grant.workflow_run_id or "none",
                facts.policy_revision,
            )
        )
        context = PolicyAuthorizedContext(
            decision=decision,
            classification=classification,
            cache_partition_key=hashlib.sha256(
                partition_material.encode("utf-8")
            ).hexdigest(),
        )
        try:
            before_handler_request = self._current_request(request)
            if before_handler_request.evaluated_at < after_load_request.evaluated_at:
                raise ValueError("trusted clock moved backwards")
        except Exception:
            self._deny(
                authorized,
                after_load_request,
                PolicyReason.POLICY_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        try:
            self._current_authorization.verify_current(
                authorized,
                evaluated_at=before_handler_request.evaluated_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny(
                authorized,
                before_handler_request,
                _current_authorization_reason(exc),
                policy_revision=facts.policy_revision,
            )
        except Exception:
            self._deny(
                authorized,
                before_handler_request,
                PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        return context


class ProtectedDataFlow[ResultT]:
    """Invoke a payload handler only after exact current authorization."""

    def __init__(
        self,
        *,
        gateway: PolicyGateway,
        requirement: PolicyBoundaryRequirement,
    ) -> None:
        self._gateway = gateway
        self.requirement = requirement
        self.boundary = requirement.boundary

    def invoke(
        self,
        *,
        authorized: AuthorizedContext,
        request: PolicyAccessRequest,
        handler: Callable[[PolicyAuthorizedContext], ResultT],
    ) -> ResultT:
        if request.boundary != self.boundary:
            raise ValueError("data-flow request does not match protected boundary")
        return self._gateway.invoke(
            authorized,
            self.requirement,
            request,
            handler,
        )


class RetentionGateway:
    """Evaluate eligibility through current CapAuth, policy, clock, and audit."""

    def __init__(
        self,
        *,
        backend: PolicyBackend,
        audit_sink: PolicyAuditSink,
        current_authorization: CapAuthCurrentStateVerifier,
        requirement: RetentionBoundaryRequirement,
        engine: PolicyEngine | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._audit_sink = audit_sink
        self._current_authorization = current_authorization
        self.requirement = requirement
        self._engine = engine or PolicyEngine()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _current_request(self, request: RetentionRequest) -> RetentionRequest:
        return RetentionRequest.model_validate(
            {**request.model_dump(mode="python"), "evaluated_at": self._clock()}
        )

    @staticmethod
    def _decision(
        authorized: AuthorizedContext,
        request: RetentionRequest,
        reason: PolicyReason,
        *,
        policy_revision: str | None = None,
    ) -> RetentionDecision:
        return RetentionDecision(
            capauth_decision_id=authorized.decision.decision_id,
            correlation_id=authorized.decision.correlation_id,
            principal_id=request.principal_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            material_id=request.material_id,
            material_version=request.material_version,
            allow=reason == PolicyReason.ALLOW,
            reason=reason,
            policy_revision=policy_revision,
            evaluated_at=request.evaluated_at,
        )

    def _record_or_deny(self, decision: RetentionDecision) -> None:
        try:
            self._audit_sink.record(decision)
        except Exception:
            raise RetentionDenied(
                RetentionDecision.model_validate(
                    {
                        **decision.model_dump(mode="python"),
                        "decision_id": uuid4(),
                        "allow": False,
                        "reason": PolicyReason.AUDIT_UNAVAILABLE,
                    }
                )
            ) from None

    def _deny(
        self,
        authorized: AuthorizedContext,
        request: RetentionRequest,
        reason: PolicyReason,
        *,
        policy_revision: str | None = None,
    ) -> None:
        decision = self._decision(
            authorized,
            request,
            reason,
            policy_revision=policy_revision,
        )
        self._record_or_deny(decision)
        raise RetentionDenied(decision) from None

    def evaluate(
        self,
        authorized: AuthorizedContext,
        request: RetentionRequest,
    ) -> RetentionDecision:
        denied_decision: RetentionDecision | None = None
        try:
            return self._evaluate(authorized, request)
        except RetentionDenied as exc:
            denied_decision = exc.decision
        if denied_decision is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("retention denial lost its sanitized decision")
        raise RetentionDenied(denied_decision) from None

    def _evaluate(
        self,
        authorized: AuthorizedContext,
        request: RetentionRequest,
    ) -> RetentionDecision:
        try:
            current_request = self._current_request(request)
        except Exception:
            failed_request = RetentionRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "evaluated_at": max(
                        _FAILED_EVALUATION_AT,
                        request.material_created_at,
                    ),
                }
            )
            self._deny(authorized, failed_request, PolicyReason.POLICY_UNAVAILABLE)

        invocation_digest = _invocation_digest(
            authorized,
            self.requirement,
            current_request,
        )
        try:
            self._current_authorization.reserve(
                authorized,
                invocation_digest=invocation_digest,
                evaluated_at=current_request.evaluated_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny(
                authorized,
                current_request,
                _current_authorization_reason(exc),
            )
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
            )

        expected_grant = self.requirement.bind(current_request)
        if (
            authorized.principal.principal_id != current_request.principal_id
            or authorized.principal.tenant_id != current_request.tenant_id
            or authorized.grant != expected_grant
        ):
            self._deny(
                authorized,
                current_request,
                PolicyReason.CAPAUTH_SCOPE_MISMATCH,
            )
        try:
            facts = self._backend.load(current_request)
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.POLICY_UNAVAILABLE,
            )
        try:
            after_load_request = self._current_request(request)
            if after_load_request.evaluated_at < current_request.evaluated_at:
                raise ValueError("trusted clock moved backwards")
        except Exception:
            self._deny(
                authorized,
                current_request,
                PolicyReason.POLICY_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        try:
            self._current_authorization.verify_current(
                authorized,
                evaluated_at=after_load_request.evaluated_at,
            )
        except (
            CurrentAuthorizationExpired,
            CurrentAuthorizationReplayed,
            CurrentAuthorizationStale,
            CurrentAuthorizationUnavailable,
        ) as exc:
            self._deny(
                authorized,
                after_load_request,
                _current_authorization_reason(exc),
                policy_revision=facts.policy_revision,
            )
        except Exception:
            self._deny(
                authorized,
                after_load_request,
                PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        try:
            engine_decision = self._engine.decide_retention(
                after_load_request,
                facts,
            )
        except Exception:
            self._deny(
                authorized,
                after_load_request,
                PolicyReason.POLICY_UNAVAILABLE,
                policy_revision=facts.policy_revision,
            )
        decision = self._decision(
            authorized,
            after_load_request,
            engine_decision.reason,
            policy_revision=facts.policy_revision,
        )
        self._record_or_deny(decision)
        if not decision.allow:
            raise RetentionDenied(decision) from None
        return decision
