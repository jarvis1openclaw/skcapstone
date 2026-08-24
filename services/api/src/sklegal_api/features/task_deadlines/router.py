"""Dedicated FastAPI router for the Task and Deadline feature lane."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError
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
from sklegal_persistence.features.task_deadlines.models import (
    DeadlineRecord,
    TaskRecord,
)

from .contracts import (
    ComputeDeadlineCommand,
    DeadlineListRead,
    DeadlineMutationReceipt,
    DeadlineReviewCommand,
    DeadlineTransitionCommand,
    SimulationHandoffCommand,
    SimulationMutationReceipt,
    TaskListRead,
    TaskMutationReceipt,
    TaskTransitionCommand,
    UpsertTaskCommand,
)
from .service import (
    TaskDeadlineAccessContext,
    TaskDeadlineService,
    TaskDeadlineServiceError,
)

_STATUS_BY_CODE = {
    "authentication_required": status.HTTP_401_UNAUTHORIZED,
    "access_denied": status.HTTP_403_FORBIDDEN,
    "resource_unavailable": status.HTTP_404_NOT_FOUND,
    "validation_failed": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "precondition_failed": status.HTTP_409_CONFLICT,
    "idempotency_conflict": status.HTTP_409_CONFLICT,
    "version_conflict": status.HTTP_409_CONFLICT,
    "policy_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "dependency_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _http_error(error: TaskDeadlineServiceError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_CODE[error.code],
        detail={"code": error.code},
    )


def _path_uuid(request: Request, name: str) -> UUID:
    try:
        return UUID(str(request.path_params[name]))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "resource_unavailable"},
        ) from None


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not 1 <= len(key) <= 512:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "validation_failed"},
        )
    return key


async def _parse[CommandT: BaseModel](
    request: Request, model: type[CommandT]
) -> CommandT:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("body must be an object")
        return model.model_validate(body)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "validation_failed"},
        ) from None


def _access(
    authorized: AuthorizedContext, matter_id: UUID
) -> TaskDeadlineAccessContext:
    return TaskDeadlineAccessContext(
        tenant_id=authorized.principal.tenant_id,
        matter_id=matter_id,
        principal_id=authorized.principal.principal_id,
        capability=cast(Any, authorized.decision.capability.value),
        purpose=cast(Any, authorized.decision.purpose.value),
        authorization_decision_id=authorized.decision.decision_id,
        credential_expires_at=authorized.credential_expires_at,
    )


def build_task_deadline_router(
    *,
    service: TaskDeadlineService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build an isolated router without changing central composition."""

    router = APIRouter()

    def protected(
        capability: Capability, purpose: Purpose, route_name: str
    ) -> ProtectedRouteDependency:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"task_deadlines.{route_name}",
            capability=capability,
            purpose=purpose,
        )
        return ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

    read_dependency = protected(
        Capability.MATTER_READ, Purpose.MATTER_MANAGEMENT, "read"
    )
    manage_dependency = protected(
        Capability.MATTER_MANAGE, Purpose.MATTER_MANAGEMENT, "manage"
    )
    simulation_dependency = protected(
        Capability.ACTION_EMAIL_PREPARE,
        Purpose.EXTERNAL_ACTION_PREPARATION,
        "simulate",
    )

    async def create_task(
        request: Request,
        authorized: AuthorizedContext = Depends(manage_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        command = await _parse(request, UpsertTaskCommand)
        try:
            return service.create_task(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=_idempotency_key(request),
                command=command,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def transition_task(
        request: Request,
        authorized: AuthorizedContext = Depends(manage_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        task_id = _path_uuid(request, "task_id")
        command = await _parse(request, TaskTransitionCommand)
        try:
            return service.transition_task(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                task_id=task_id,
                idempotency_key=_idempotency_key(request),
                command=command,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def list_tasks(
        request: Request,
        authorized: AuthorizedContext = Depends(read_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        try:
            return service.list_tasks(
                context=_access(authorized, matter_id), matter_id=matter_id
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def get_task(
        request: Request,
        authorized: AuthorizedContext = Depends(read_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        task_id = _path_uuid(request, "task_id")
        try:
            return service.get_task(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                task_id=task_id,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def compute_deadline(
        request: Request,
        authorized: AuthorizedContext = Depends(manage_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        command = await _parse(request, ComputeDeadlineCommand)
        try:
            return service.compute_deadline(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=_idempotency_key(request),
                command=command,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def review_deadline(
        request: Request,
        authorized: AuthorizedContext = Depends(manage_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        deadline_id = _path_uuid(request, "deadline_id")
        command = await _parse(request, DeadlineReviewCommand)
        try:
            return service.review_deadline(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                deadline_id=deadline_id,
                idempotency_key=_idempotency_key(request),
                command=command,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def transition_deadline(
        request: Request,
        authorized: AuthorizedContext = Depends(manage_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        deadline_id = _path_uuid(request, "deadline_id")
        command = await _parse(request, DeadlineTransitionCommand)
        try:
            return service.transition_deadline(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                deadline_id=deadline_id,
                idempotency_key=_idempotency_key(request),
                command=command,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def list_deadlines(
        request: Request,
        authorized: AuthorizedContext = Depends(read_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        try:
            return service.list_deadlines(
                context=_access(authorized, matter_id), matter_id=matter_id
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def get_deadline(
        request: Request,
        authorized: AuthorizedContext = Depends(read_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        deadline_id = _path_uuid(request, "deadline_id")
        try:
            return service.get_deadline(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                deadline_id=deadline_id,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    async def create_simulation(
        request: Request,
        authorized: AuthorizedContext = Depends(simulation_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        command = await _parse(request, SimulationHandoffCommand)
        try:
            return service.create_simulation_handoff(
                context=_access(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=_idempotency_key(request),
                command=command,
            )
        except TaskDeadlineServiceError as error:
            raise _http_error(error) from None

    router.post(
        "/v1/matters/{matter_id}/tasks",
        operation_id="upsert_task",
        status_code=status.HTTP_201_CREATED,
        response_model=TaskMutationReceipt,
    )(create_task)
    router.get(
        "/v1/matters/{matter_id}/tasks",
        operation_id="list_tasks",
        response_model=TaskListRead,
    )(list_tasks)
    router.get(
        "/v1/matters/{matter_id}/tasks/{task_id}",
        operation_id="get_task",
        response_model=TaskRecord,
    )(get_task)
    router.post(
        "/v1/matters/{matter_id}/tasks/{task_id}/transitions",
        operation_id="transition_task",
        response_model=TaskMutationReceipt,
    )(transition_task)
    router.post(
        "/v1/matters/{matter_id}/deadlines",
        operation_id="compute_deadline",
        status_code=status.HTTP_201_CREATED,
        response_model=DeadlineMutationReceipt,
    )(compute_deadline)
    router.get(
        "/v1/matters/{matter_id}/deadlines",
        operation_id="list_deadlines",
        response_model=DeadlineListRead,
    )(list_deadlines)
    router.get(
        "/v1/matters/{matter_id}/deadlines/{deadline_id}",
        operation_id="get_deadline",
        response_model=DeadlineRecord,
    )(get_deadline)
    router.post(
        "/v1/matters/{matter_id}/deadlines/{deadline_id}/reviews",
        operation_id="review_deadline",
        response_model=DeadlineMutationReceipt,
    )(review_deadline)
    router.post(
        "/v1/matters/{matter_id}/deadlines/{deadline_id}/transitions",
        operation_id="transition_deadline",
        response_model=DeadlineMutationReceipt,
    )(transition_deadline)
    router.post(
        "/v1/matters/{matter_id}/action-simulations",
        operation_id="create_simulation_handoff",
        status_code=status.HTTP_201_CREATED,
        response_model=SimulationMutationReceipt,
    )(create_simulation)

    return router
