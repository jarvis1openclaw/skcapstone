"""CapAuth-gated human policy-decision and revocation HTTP endpoints.

Each route serves one exact human governance decision: conflict dispositions,
waiver evidence, ethical walls and rosters, wall memberships, protected
access grants and revocation, privilege and work-product labels, retention
policies, and legal hold issue and release. Attribution comes only from the
authenticated principal, the idempotency key comes only from the
Idempotency-Key header, and every outcome lands in the S1-05 audit and
outbox pipeline. All failures are sanitized and fail closed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sklegal_audit import (
    AuditAttributes,
    AuditBoundary,
    AuditEventDraft,
    AuditOutcome,
    AuditRepository,
    RunCorrelation,
)
from sklegal_capauth import (
    ApiCapabilityBoundary,
    AuthorizedContext,
    CapabilityAuthorizer,
)
from sklegal_policies import (
    CloseEthicalWall,
    DecideConflict,
    GovernanceAction,
    GovernanceAuditRecord,
    GovernanceCommand,
    GovernanceReason,
    GrantProtectedAccess,
    IssueLegalHold,
    OpenEthicalWall,
    PolicyGovernanceDenied,
    PolicyGovernanceService,
    PolicyGovernanceUnavailable,
    RegisterWaiverReference,
    ReleaseLegalHold,
    RevokeProtectedAccess,
    SealEthicalWallRoster,
    SetProtectionLabel,
    SetRetentionPolicy,
    SetWallMembership,
)
from sklegal_policies.models import Sha256

from .capauth import (
    PrincipalResolver,
    ProtectedRouteDependency,
    ScopeResolver,
)

_CONFLICT_REASONS = frozenset(
    {
        GovernanceReason.STALE_AUTHORITY,
        GovernanceReason.VERSION_CONFLICT,
        GovernanceReason.IDEMPOTENCY_CONFLICT,
    }
)


class AuditLedgerGovernanceSink:
    """Bridge governance audit records into the S1-05 append-only ledger.

    The ledger append commits the audit event and its transactional outbox
    message atomically; any failure raises PolicyGovernanceUnavailable so
    the calling store commits nothing.
    """

    def __init__(self, ledger: AuditRepository) -> None:
        self._ledger = ledger

    def record(self, record: GovernanceAuditRecord) -> Sha256:
        outcome = (
            AuditOutcome.DENY
            if record.outcome.value == "denied"
            else AuditOutcome.SUCCESS
        )
        draft = AuditEventDraft(
            event_id=record.event_id,
            tenant_id=record.tenant_id,
            matter_id=record.matter_id,
            principal_id=record.principal_id,
            correlation=RunCorrelation(
                run_id=record.run_id,
                correlation_id=record.correlation_id,
                trace_id=record.correlation_id.hex,
                span_id=record.event_id.hex[:16],
            ),
            boundary=AuditBoundary.HUMAN,
            action=f"policy.{record.action.value}",
            resource_kind="policy_record",
            resource_id=record.record_id,
            authorization_decision_id=record.capauth_decision_id,
            outcome=outcome,
            reason_code=record.reason.value,
            occurred_at=record.occurred_at,
            attributes=AuditAttributes(
                capability=record.capability,
                purpose=record.purpose,
                target=record.target,
                policy_boundary=record.action.value,
            ),
        )
        try:
            event = self._ledger.append(draft)
        except Exception:
            raise PolicyGovernanceUnavailable(
                "governance audit ledger unavailable"
            ) from None
        return event.event_sha256


type _CommandType = type[GovernanceCommand]

_ROUTE_TABLE: tuple[tuple[str, str, GovernanceAction, _CommandType], ...] = (
    (
        "/matters/{matter_id}/conflict-decisions",
        "policy.conflict.decide",
        GovernanceAction.DECIDE_CONFLICT,
        DecideConflict,
    ),
    (
        "/matters/{matter_id}/waivers",
        "policy.waiver.register",
        GovernanceAction.REGISTER_WAIVER,
        RegisterWaiverReference,
    ),
    (
        "/matters/{matter_id}/ethical-walls",
        "policy.wall.manage",
        GovernanceAction.OPEN_ETHICAL_WALL,
        OpenEthicalWall,
    ),
    (
        "/matters/{matter_id}/ethical-walls/{wall_id}/seal",
        "policy.wall.manage",
        GovernanceAction.SEAL_ETHICAL_WALL_ROSTER,
        SealEthicalWallRoster,
    ),
    (
        "/matters/{matter_id}/ethical-walls/{wall_id}/close",
        "policy.wall.manage",
        GovernanceAction.CLOSE_ETHICAL_WALL,
        CloseEthicalWall,
    ),
    (
        "/matters/{matter_id}/wall-memberships",
        "policy.wall.manage",
        GovernanceAction.SET_WALL_MEMBERSHIP,
        SetWallMembership,
    ),
    (
        "/matters/{matter_id}/protected-access-grants",
        "policy.wall.manage",
        GovernanceAction.GRANT_PROTECTED_ACCESS,
        GrantProtectedAccess,
    ),
    (
        "/matters/{matter_id}/protected-access-grants/{grant_id}/revoke",
        "policy.wall.manage",
        GovernanceAction.REVOKE_PROTECTED_ACCESS,
        RevokeProtectedAccess,
    ),
    (
        "/matters/{matter_id}/protection-labels",
        "policy.matter.manage",
        GovernanceAction.SET_PROTECTION_LABEL,
        SetProtectionLabel,
    ),
    (
        "/matters/{matter_id}/retention-policies",
        "policy.matter.manage",
        GovernanceAction.SET_RETENTION_POLICY,
        SetRetentionPolicy,
    ),
    (
        "/matters/{matter_id}/legal-holds",
        "policy.matter.manage",
        GovernanceAction.ISSUE_LEGAL_HOLD,
        IssueLegalHold,
    ),
    (
        "/matters/{matter_id}/legal-holds/{hold_id}/release",
        "policy.matter.manage",
        GovernanceAction.RELEASE_LEGAL_HOLD,
        ReleaseLegalHold,
    ),
)


def _governance_http_error(exc: PolicyGovernanceDenied) -> HTTPException:
    conflict = exc.decision.reason in _CONFLICT_REASONS
    return HTTPException(
        status_code=(
            status.HTTP_409_CONFLICT if conflict else status.HTTP_403_FORBIDDEN
        ),
        detail={
            "code": (
                "policy_governance_conflict" if conflict else "policy_governance_denied"
            ),
            "reason": exc.decision.reason.value,
            "decision_id": str(exc.decision.decision_id),
        },
    )


async def _parse_command(
    request: Request,
    command_type: _CommandType,
    authorized: AuthorizedContext,
) -> GovernanceCommand:
    key = request.headers.get("Idempotency-Key", "").strip()
    try:
        command_id = UUID(key)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "idempotency_key_required"},
        ) from None
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "request_body_invalid"},
        ) from None
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "command_invalid"},
        )
    payload: dict[str, Any] = {
        **body,
        "command_id": str(command_id),
        "tenant_id": str(authorized.principal.tenant_id),
        "matter_id": str(request.path_params["matter_id"]),
    }
    try:
        command = command_type.model_validate(payload, strict=False)
    except (ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "command_invalid"},
        ) from None
    if not isinstance(command, GovernanceCommand):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "command_invalid"},
        )
    return command


def build_governance_router(
    *,
    service: PolicyGovernanceService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build the governed human policy-decision router.

    Every route is wrapped in the CapAuth protected-route dependency with
    the exact fixed capability contract for its governance action.
    """

    router = APIRouter()

    for path, route_name, action, command_type in _ROUTE_TABLE:
        requirement = PolicyGovernanceService.requirement_for(action)
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=route_name,
            capability=requirement.capability,
            purpose=requirement.purpose,
        )
        dependency = ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

        async def handler(
            request: Request,
            authorized: AuthorizedContext = Depends(dependency),
            _command_type: _CommandType = command_type,
        ) -> dict[str, Any]:
            command = await _parse_command(request, _command_type, authorized)
            try:
                receipt = service.execute(authorized, command)
            except PolicyGovernanceDenied as exc:
                raise _governance_http_error(exc) from None
            except PolicyGovernanceUnavailable:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "policy_governance_unavailable"},
                ) from None
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "policy_governance_unavailable"},
                ) from None
            return receipt.model_dump(mode="json")

        router.post(path, operation_id=f"governance_{action.value}")(handler)

    return router
