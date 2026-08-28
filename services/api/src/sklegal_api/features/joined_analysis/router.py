"""CapAuth-first FastAPI router for joined Matter analysis."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sklegal_api.capauth import (
    PrincipalResolver,
    ProtectedRouteDependency,
    ScopeResolver,
)
from sklegal_capauth import (
    ApiCapabilityBoundary,
    AuthorizedContext,
    Capability,
    CapabilityAuthorizer,
    Purpose,
)

from .service import (
    JoinedAnalysisCursorInvalid,
    JoinedAnalysisStore,
    build_matter_analysis,
)


def _error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


def _path_uuid(request: Request) -> UUID:
    try:
        return UUID(str(request.path_params["matter_id"]))
    except (KeyError, ValueError):
        raise _error(status.HTTP_404_NOT_FOUND, "not_found") from None


def build_joined_analysis_router(
    *,
    store: JoinedAnalysisStore,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build an unmounted router for the integration owner to compose later."""

    boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
        authorizer=authorizer,
        route_name="joined_analysis.read",
        capability=Capability.CLAIM_REVIEW,
        purpose=Purpose.CLAIM_REVIEW,
    )
    protected = ProtectedRouteDependency(
        boundary=boundary,
        principal_resolver=principal_resolver,
        scope_resolver=scope_resolver,
    )
    router = APIRouter()

    async def get_joined_analysis(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        authorized: AuthorizedContext = Depends(protected),
    ) -> Any:
        matter_id = _path_uuid(request)
        try:
            member = store.is_matter_member(
                authorized.principal.tenant_id,
                matter_id,
                authorized.principal.principal_id,
            )
        except Exception:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "joined_analysis_unavailable",
            ) from None
        if not member:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "matter_membership_denied",
            )
        try:
            projection = store.projection(authorized.principal.tenant_id, matter_id)
        except Exception:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "joined_analysis_unavailable",
            ) from None
        if projection is None:
            raise _error(status.HTTP_404_NOT_FOUND, "not_found")
        try:
            response = build_matter_analysis(
                projection=projection,
                authorized=authorized,
                limit=limit,
                cursor=cursor,
            )
        except JoinedAnalysisCursorInvalid:
            raise _error(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from None
        except Exception:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "joined_analysis_unavailable",
            ) from None
        return response.model_dump(mode="json", by_alias=True)

    router.get(
        "/v1/matters/{matter_id}/analysis",
        operation_id="get_joined_analysis",
    )(get_joined_analysis)
    return router
