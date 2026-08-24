"""Dedicated CapAuth router for Matter activity and export proposals."""

from __future__ import annotations

import inspect
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sklegal_api.capauth import (
    PrincipalResolver,
    ProtectedRouteDependency,
    ScopeResolver,
)
from sklegal_capauth import (
    ApiCapabilityBoundary,
    AuthorizedContext,
    BoundaryScope,
    Capability,
    CapabilityAuthorizer,
    Purpose,
)

from .contracts import (
    ActivityExportCommand,
    ActivityExportResponseRead,
    MatterActivityPage,
)
from .service import ActivityAccessContext, ActivityServiceError, MatterActivityService

_STATUS_BY_CODE = {
    "access_denied": status.HTTP_403_FORBIDDEN,
    "policy_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "dependency_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "precondition_failed": status.HTTP_409_CONFLICT,
    "idempotency_conflict": status.HTTP_409_CONFLICT,
    "validation_failed": status.HTTP_422_UNPROCESSABLE_CONTENT,
}

_MESSAGE_BY_CODE = {
    "authentication_required": "Authentication is required.",
    "access_denied": "Access is denied.",
    "resource_unavailable": "The resource is unavailable.",
    "validation_failed": "The request is invalid.",
    "precondition_failed": "A required precondition failed.",
    "idempotency_conflict": "The idempotency key conflicts with this request.",
    "policy_unavailable": "Policy is temporarily unavailable.",
    "dependency_unavailable": "A required dependency is temporarily unavailable.",
}


def _correlation_id(request: Request) -> UUID:
    value = getattr(request.state, "correlation_id", None)
    if not isinstance(value, UUID):
        value = uuid4()
        request.state.correlation_id = value
    return value


def _error_detail(request: Request, code: str) -> dict[str, object]:
    return {
        "code": code,
        "message": _MESSAGE_BY_CODE[code],
        "correlation_id": str(_correlation_id(request)),
        "retryable": code in {"policy_unavailable", "dependency_unavailable"},
    }


def _activity_error(request: Request, error: ActivityServiceError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_CODE[error.code],
        detail=_error_detail(request, error.code),
    )


def _path_matter_id(request: Request) -> UUID:
    try:
        return UUID(str(request.path_params["matter_id"]))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error_detail(request, "resource_unavailable"),
        ) from None


def _access(authorized: AuthorizedContext, matter_id: UUID) -> ActivityAccessContext:
    if authorized.decision.resource_id != str(matter_id):
        raise ActivityServiceError("access_denied")
    return ActivityAccessContext(
        tenant_id=authorized.principal.tenant_id,
        matter_id=matter_id,
        resource_id=matter_id,
        principal_id=authorized.principal.principal_id,
        capability=cast(Any, authorized.decision.capability.value),
        purpose=cast(Any, authorized.decision.purpose.value),
        authorization_decision_id=authorized.decision.decision_id,
        correlation_id=authorized.decision.correlation_id,
        credential_expires_at=authorized.credential_expires_at,
    )


async def _export_command(request: Request) -> ActivityExportCommand:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("request body must be an object")
        return ActivityExportCommand.model_validate(body)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error_detail(request, "validation_failed"),
        ) from None


def build_matter_activity_router(
    *,
    service: MatterActivityService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build the isolated feature router without central app mutation."""

    router = APIRouter()

    def dependency(
        capability: Capability,
        purpose: Purpose,
        name: str,
    ) -> Any:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"matter_activity.{name}",
            capability=capability,
            purpose=purpose,
        )

        async def activity_scope_resolver(request: Request) -> BoundaryScope:
            selected = scope_resolver(request)
            if inspect.isawaitable(selected):
                selected = await selected
            if not isinstance(selected, BoundaryScope) or selected.resource_id is None:
                raise TypeError("scope resolver returned the wrong type")
            return BoundaryScope(
                tenant_id=selected.tenant_id,
                resource_id=selected.resource_id,
            )

        raw = ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=activity_scope_resolver,
        )

        async def authorize(request: Request) -> AuthorizedContext:
            _correlation_id(request)
            if (
                not request.headers.get("Authorization")
                and getattr(request.state, "browser_session", None) is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=_error_detail(request, "authentication_required"),
                )
            try:
                return await raw(request)
            except HTTPException as error:
                code = (
                    "authentication_required"
                    if error.status_code == status.HTTP_401_UNAUTHORIZED
                    else "dependency_unavailable"
                    if error.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
                    else "access_denied"
                )
                raise HTTPException(
                    status_code=(
                        status.HTTP_401_UNAUTHORIZED
                        if code == "authentication_required"
                        else status.HTTP_503_SERVICE_UNAVAILABLE
                        if code == "dependency_unavailable"
                        else status.HTTP_403_FORBIDDEN
                    ),
                    detail=_error_detail(request, code),
                ) from None

        return authorize

    read_dependency = dependency(
        Capability.AUDIT_READ,
        Purpose.AUDIT_REVIEW,
        "list_activity",
    )
    export_dependency = dependency(
        Capability.AUDIT_READ,
        Purpose.AUDIT_REVIEW,
        "create_activity_export",
    )

    async def list_activity(
        request: Request,
        authorized: AuthorizedContext = Depends(read_dependency),
    ) -> Any:
        matter_id = _path_matter_id(request)
        try:
            cursor_values = request.query_params.getlist("cursor")
            limit_values = request.query_params.getlist("limit")
            if len(cursor_values) > 1 or len(limit_values) > 1:
                raise ActivityServiceError("validation_failed")
            cursor = cursor_values[0] if cursor_values else None
            if cursor is not None and not 1 <= len(cursor) <= 2048:
                raise ActivityServiceError("validation_failed")
            try:
                limit = int(limit_values[0]) if limit_values else 50
            except ValueError:
                raise ActivityServiceError("validation_failed") from None
            page = service.list_activity(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                cursor=cursor,
                limit=limit,
            )
        except ActivityServiceError as error:
            raise _activity_error(request, error) from None
        return page.model_dump(mode="json", by_alias=True)

    async def propose_export(
        request: Request,
        authorized: AuthorizedContext = Depends(export_dependency),
    ) -> Any:
        matter_id = _path_matter_id(request)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=_error_detail(request, "validation_failed"),
            )
        command = await _export_command(request)
        try:
            proposal = service.propose_export(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                command=command,
            )
        except ActivityServiceError as error:
            raise _activity_error(request, error) from None
        return proposal

    router.get(
        "/v1/matters/{matter_id}/activity",
        operation_id="list_activity",
        response_model=MatterActivityPage,
    )(list_activity)
    router.post(
        "/v1/matters/{matter_id}/activity-exports",
        operation_id="create_activity_export",
        status_code=status.HTTP_201_CREATED,
        response_model=ActivityExportResponseRead,
    )(propose_export)
    return router
