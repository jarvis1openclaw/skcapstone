"""Standalone CapAuth-protected router for governed Agent Runs.

The central application intentionally does not mount this router in RUN-01.
Composition belongs to the later integration lane.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sklegal_capauth import (  # type: ignore[import-untyped]
    ApiCapabilityBoundary,
    AuthorizedContext,
    Capability,
    CapabilityAuthorizer,
    Purpose,
)
from sklegal_persistence.features.agent_runs.models import AgentRunRecord

from ...capauth import PrincipalResolver, ProtectedRouteDependency, ScopeResolver
from .contracts import AnalysisRequestBody, BlindChallengeBody, HumanDispositionBody
from .service import AgentRunService, AgentRunServiceError, RunActor

IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def _path_uuid(request: Request, name: str) -> UUID:
    try:
        return UUID(str(request.path_params[name]))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "agent_run_not_found"},
        ) from None


def _actor(authorized: AuthorizedContext) -> RunActor:
    decision = authorized.decision
    if (
        decision.matter_id is None
        or decision.credential_digest is None
        or decision.revocation_revision is None
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "authorization_evidence_incomplete"},
        )
    return RunActor(
        tenant_id=authorized.principal.tenant_id,
        principal_id=authorized.principal.principal_id,
        authorized_matter_id=decision.matter_id,
        decision_id=decision.decision_id,
        correlation_id=decision.correlation_id,
        capability=decision.capability.value,
        purpose=decision.purpose.value,
        verifier_policy_version=decision.verifier_policy_version,
        revocation_revision=decision.revocation_revision,
        credential_digest=decision.credential_digest,
    )


def _dump(value: AgentRunRecord | tuple[AgentRunRecord, ...]) -> Any:
    if isinstance(value, tuple):
        return [item.model_dump(mode="json", by_alias=True) for item in value]
    return value.model_dump(mode="json", by_alias=True)


def _run(
    service_call: Callable[[], AgentRunRecord | tuple[AgentRunRecord, ...]],
) -> Any:
    try:
        return _dump(service_call())
    except AgentRunServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code},
        ) from None


def build_agent_runs_router(
    *,
    service: AgentRunService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build an unmounted Matter-scoped Agent Run router."""

    router = APIRouter(tags=["Agent Runs"])

    def dependency(capability: Capability, purpose: Purpose, name: str) -> Any:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"agent_runs.{name}",
            capability=capability,
            purpose=purpose,
        )
        return ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

    async def start_run(
        request: Request,
        command: AnalysisRequestBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(
            dependency(
                Capability.CLAIM_PROPOSE,
                Purpose.CLAIM_DEVELOPMENT,
                "start",
            )
        ),
    ) -> Any:
        return _run(
            lambda: service.start(
                matter_id=_path_uuid(request, "matter_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized),
            )
        )

    async def list_runs(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW, "list")
        ),
    ) -> Any:
        return _run(
            lambda: service.list(
                matter_id=_path_uuid(request, "matter_id"),
                actor=_actor(authorized),
            )
        )

    async def get_run(
        request: Request,
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW, "get")
        ),
    ) -> Any:
        return _run(
            lambda: service.get(
                matter_id=_path_uuid(request, "matter_id"),
                run_id=_path_uuid(request, "run_id"),
                actor=_actor(authorized),
            )
        )

    async def challenge_run(
        request: Request,
        command: BlindChallengeBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW, "challenge")
        ),
    ) -> Any:
        return _run(
            lambda: service.challenge(
                matter_id=_path_uuid(request, "matter_id"),
                run_id=_path_uuid(request, "run_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized),
            )
        )

    async def dispose_recommendation(
        request: Request,
        command: HumanDispositionBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(
            dependency(Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW, "dispose")
        ),
    ) -> Any:
        return _run(
            lambda: service.dispose(
                matter_id=_path_uuid(request, "matter_id"),
                run_id=_path_uuid(request, "run_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized),
            )
        )

    router.post(
        "/v1/matters/{matter_id}/agent-runs",
        operation_id="agent_runs_start",
        status_code=status.HTTP_201_CREATED,
        response_model=AgentRunRecord,
    )(start_run)
    router.get(
        "/v1/matters/{matter_id}/agent-runs",
        operation_id="agent_runs_list",
        response_model=list[AgentRunRecord],
    )(list_runs)
    router.get(
        "/v1/matters/{matter_id}/agent-runs/{run_id}",
        operation_id="agent_runs_get",
        response_model=AgentRunRecord,
    )(get_run)
    router.post(
        "/v1/matters/{matter_id}/agent-runs/{run_id}/challenges",
        operation_id="agent_runs_challenge",
        response_model=AgentRunRecord,
    )(challenge_run)
    router.post(
        "/v1/matters/{matter_id}/agent-runs/{run_id}/dispositions",
        operation_id="agent_runs_dispose",
        response_model=AgentRunRecord,
    )(dispose_recommendation)

    return router
