"""Dedicated FastAPI router for the frozen artifact lifecycle operations."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

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
    Capability,
    CapabilityAuthorizer,
    Purpose,
)

from .contracts import (
    ArtifactContractModel,
    ArtifactCorrectionCommand,
    ArtifactIntakeCommand,
    ArtifactIntakeReceipt,
    ArtifactRead,
    ArtifactReviewCommand,
    ArtifactReviewReceipt,
    ArtifactSupersessionCommand,
)
from .service import ArtifactAccessContext, ArtifactIntakeService, ArtifactServiceError

_STATUS_BY_CODE = {
    "authentication_required": status.HTTP_401_UNAUTHORIZED,
    "access_denied": status.HTTP_403_FORBIDDEN,
    "validation_failed": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "precondition_failed": status.HTTP_409_CONFLICT,
    "idempotency_conflict": status.HTTP_409_CONFLICT,
    "policy_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "dependency_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "resource_unavailable": status.HTTP_404_NOT_FOUND,
    "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _http_error(error: ArtifactServiceError) -> HTTPException:
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


def _access_context(
    authorized: AuthorizedContext, matter_id: UUID
) -> ArtifactAccessContext:
    return ArtifactAccessContext(
        tenant_id=authorized.principal.tenant_id,
        matter_id=matter_id,
        principal_id=authorized.principal.principal_id,
        capability=cast(Any, authorized.decision.capability.value),
        purpose=cast(Any, authorized.decision.purpose.value),
        authorization_decision_id=authorized.decision.decision_id,
        credential_expires_at=authorized.credential_expires_at,
    )


async def _parse_command[ArtifactCommandT: ArtifactContractModel](
    request: Request, model: type[ArtifactCommandT]
) -> ArtifactCommandT:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("request body is not an object")
        return model.model_validate(body)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "validation_failed"},
        ) from None


def build_artifact_intake_router(
    *,
    service: ArtifactIntakeService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    """Build the isolated artifact router without central app mutation."""

    router = APIRouter()

    def dependency(
        capability: Capability, purpose: Purpose, route_name: str
    ) -> ProtectedRouteDependency:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"artifact_intake.{route_name}",
            capability=capability,
            purpose=purpose,
        )
        return ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

    create_dependency = dependency(
        Capability.EVIDENCE_MANAGE, Purpose.EVIDENCE_REVIEW, "create"
    )
    get_dependency = dependency(
        Capability.EVIDENCE_READ, Purpose.EVIDENCE_REVIEW, "get"
    )
    review_dependency = dependency(
        Capability.EVIDENCE_MANAGE, Purpose.EVIDENCE_REVIEW, "review"
    )
    correction_dependency = dependency(
        Capability.EVIDENCE_MANAGE, Purpose.EVIDENCE_REVIEW, "correct"
    )
    supersession_dependency = dependency(
        Capability.EVIDENCE_MANAGE, Purpose.EVIDENCE_REVIEW, "supersede"
    )

    async def create_artifact(
        request: Request,
        authorized: AuthorizedContext = Depends(create_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "validation_failed"},
            )
        command = await _parse_command(request, ArtifactIntakeCommand)
        try:
            receipt = service.intake(
                context=_access_context(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                command=command,
            )
        except ArtifactServiceError as error:
            raise _http_error(error) from None
        return receipt.model_dump(mode="json", by_alias=True)

    async def get_artifact(
        request: Request,
        authorized: AuthorizedContext = Depends(get_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        artifact_id = _path_uuid(request, "artifact_id")
        try:
            artifact = service.get(
                context=_access_context(authorized, matter_id),
                matter_id=matter_id,
                artifact_id=artifact_id,
            )
        except ArtifactServiceError as error:
            raise _http_error(error) from None
        return artifact.model_dump(mode="json", by_alias=True)

    async def review_artifact(
        request: Request,
        authorized: AuthorizedContext = Depends(review_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        artifact_id = _path_uuid(request, "artifact_id")
        idempotency_key = _idempotency_key(request)
        command = await _parse_command(request, ArtifactReviewCommand)
        if command.artifact_id != artifact_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "validation_failed"},
            )
        try:
            receipt = service.review(
                context=_access_context(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                command=command,
            )
        except ArtifactServiceError as error:
            raise _http_error(error) from None
        return receipt.model_dump(mode="json", by_alias=True)

    async def correct_artifact(
        request: Request,
        authorized: AuthorizedContext = Depends(correction_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        artifact_id = _path_uuid(request, "artifact_id")
        idempotency_key = _idempotency_key(request)
        command = await _parse_command(request, ArtifactCorrectionCommand)
        if command.target_artifact_id != artifact_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "validation_failed"},
            )
        try:
            receipt = service.correct(
                context=_access_context(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                command=command,
            )
        except ArtifactServiceError as error:
            raise _http_error(error) from None
        return receipt.model_dump(mode="json", by_alias=True)

    async def supersede_artifact(
        request: Request,
        authorized: AuthorizedContext = Depends(supersession_dependency),
    ) -> Any:
        matter_id = _path_uuid(request, "matter_id")
        artifact_id = _path_uuid(request, "artifact_id")
        idempotency_key = _idempotency_key(request)
        command = await _parse_command(request, ArtifactSupersessionCommand)
        if command.artifact_id != artifact_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "validation_failed"},
            )
        try:
            receipt = service.supersede(
                context=_access_context(authorized, matter_id),
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                command=command,
            )
        except ArtifactServiceError as error:
            raise _http_error(error) from None
        return receipt.model_dump(mode="json", by_alias=True)

    router.post(
        "/v1/matters/{matter_id}/artifacts",
        operation_id="artifact_intake_create",
        status_code=status.HTTP_201_CREATED,
        response_model=ArtifactIntakeReceipt,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": ArtifactIntakeCommand.model_json_schema(by_alias=True)
                    }
                },
            }
        },
    )(create_artifact)
    router.get(
        "/v1/matters/{matter_id}/artifacts/{artifact_id}",
        operation_id="artifact_intake_get",
        response_model=ArtifactRead,
    )(get_artifact)
    router.post(
        "/v1/matters/{matter_id}/artifacts/{artifact_id}/reviews",
        operation_id="artifact_intake_review",
        response_model=ArtifactReviewReceipt,
    )(review_artifact)
    router.post(
        "/v1/matters/{matter_id}/artifacts/{artifact_id}/corrections",
        operation_id="artifact_intake_correct",
        status_code=status.HTTP_201_CREATED,
        response_model=ArtifactReviewReceipt,
    )(correct_artifact)
    router.post(
        "/v1/matters/{matter_id}/artifacts/{artifact_id}/supersessions",
        operation_id="artifact_intake_supersede",
        status_code=status.HTTP_201_CREATED,
        response_model=ArtifactReviewReceipt,
    )(supersede_artifact)
    return router


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_failed"},
        )
    return value
