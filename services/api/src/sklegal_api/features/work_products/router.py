"""Standalone CapAuth-protected router for Work Product operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sklegal_capauth import (
    ApiCapabilityBoundary,
    AuthorizedContext,
    Capability,
    CapabilityAuthorizer,
    Purpose,
)
from sklegal_persistence.features.work_products.models import VersionBinding

from ...capauth import PrincipalResolver, ProtectedRouteDependency, ScopeResolver
from .contracts import (
    CreateVersionBody,
    CreateWorkProductBody,
    DecideApprovalBody,
    GroundSentenceBody,
    RequestApprovalBody,
    RevokeApprovalBody,
    SupersedeApprovalBody,
    ValidateWorkProductBody,
)
from .service import (
    WorkProductActor,
    WorkProductCapability,
    WorkProductPurpose,
    WorkProductService,
    WorkProductServiceError,
)

IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def _path_uuid(request: Request, name: str) -> UUID:
    try:
        return UUID(str(request.path_params[name]))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "work_product_not_found"},
        ) from None


def _actor(
    authorized: AuthorizedContext, *, authorized_at: datetime
) -> WorkProductActor:
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
    capability = decision.capability.value
    purpose = decision.purpose.value
    if capability not in {
        "work_product.draft",
        "work_product.approve",
        "matter.read",
    } or purpose not in {
        "work_product_preparation",
        "human_approval",
        "matter_management",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "operation_capability_mismatch"},
        )
    return WorkProductActor(
        tenant_id=authorized.principal.tenant_id,
        principal_id=authorized.principal.principal_id,
        authorized_matter_id=decision.matter_id,
        decision_id=decision.decision_id,
        correlation_id=decision.correlation_id,
        capability=cast(WorkProductCapability, capability),
        purpose=cast(WorkProductPurpose, purpose),
        policy_revision=decision.verifier_policy_version,
        revocation_revision=decision.revocation_revision,
        credential_digest=decision.credential_digest,
        ancestor_credential_digests=decision.ancestor_credential_digests,
        authorized_at=authorized_at,
        expires_at=authorized.credential_expires_at,
    )


def _run(call: Callable[[], Any]) -> Any:
    try:
        value = call()
        if isinstance(value, tuple):
            return [item.model_dump(mode="json", by_alias=True) for item in value]
        return value.model_dump(mode="json", by_alias=True)
    except WorkProductServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code},
        ) from None


def build_work_products_router(
    *,
    service: WorkProductService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
    clock: Callable[[], datetime],
) -> APIRouter:
    """Build the unmounted feature router for a later integration lane."""

    router = APIRouter(tags=["Work Products"])

    def dependency(capability: Capability, purpose: Purpose, name: str) -> Any:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"work_products.{name}",
            capability=capability,
            purpose=purpose,
        )
        return ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

    draft_create = dependency(
        Capability.WORK_PRODUCT_DRAFT,
        Purpose.WORK_PRODUCT_PREPARATION,
        "create",
    )
    draft_mutate = dependency(
        Capability.WORK_PRODUCT_DRAFT,
        Purpose.WORK_PRODUCT_PREPARATION,
        "mutate",
    )
    approve = dependency(
        Capability.WORK_PRODUCT_APPROVE,
        Purpose.HUMAN_APPROVAL,
        "approve",
    )
    read = dependency(Capability.MATTER_READ, Purpose.MATTER_MANAGEMENT, "read")

    async def create(
        request: Request,
        command: CreateWorkProductBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(draft_create),
    ) -> Any:
        return _run(
            lambda: service.create(
                matter_id=_path_uuid(request, "matter_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def create_version(
        request: Request,
        command: CreateVersionBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(draft_mutate),
    ) -> Any:
        return _run(
            lambda: service.create_version(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def ground_sentence(
        request: Request,
        command: GroundSentenceBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(draft_mutate),
    ) -> Any:
        return _run(
            lambda: service.ground_sentence(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def validate(
        request: Request,
        command: ValidateWorkProductBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(draft_mutate),
    ) -> Any:
        return _run(
            lambda: service.validate(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def request_approval(
        request: Request,
        command: RequestApprovalBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(draft_mutate),
    ) -> Any:
        return _run(
            lambda: service.request_approval(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def decide_approval(
        request: Request,
        command: DecideApprovalBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(approve),
    ) -> Any:
        return _run(
            lambda: service.decide_approval(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                approval_id=_path_uuid(request, "approval_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def revoke_approval(
        request: Request,
        command: RevokeApprovalBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(approve),
    ) -> Any:
        return _run(
            lambda: service.revoke_approval(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                approval_id=_path_uuid(request, "approval_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def supersede_approval(
        request: Request,
        command: SupersedeApprovalBody,
        idempotency_key: IdempotencyKey,
        authorized: AuthorizedContext = Depends(approve),
    ) -> Any:
        return _run(
            lambda: service.supersede_approval(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                approval_id=_path_uuid(request, "approval_id"),
                command=command,
                idempotency_key=idempotency_key,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def get(
        request: Request,
        authorized: AuthorizedContext = Depends(read),
    ) -> Any:
        return _run(
            lambda: service.get(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def list_for_matter(
        request: Request,
        authorized: AuthorizedContext = Depends(read),
    ) -> Any:
        return _run(
            lambda: service.list(
                matter_id=_path_uuid(request, "matter_id"),
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def compare(
        request: Request,
        left_version_id: UUID = Query(alias="leftVersionId"),
        right_version_id: UUID = Query(alias="rightVersionId"),
        authorized: AuthorizedContext = Depends(read),
    ) -> Any:
        return _run(
            lambda: service.compare(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                left_version_id=left_version_id,
                right_version_id=right_version_id,
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    async def approval_validity(
        request: Request,
        version_id: UUID = Query(alias="versionId"),
        version_number: int = Query(alias="versionNumber", ge=1),
        content_digest: str = Query(alias="contentSha256", pattern=r"^[0-9a-f]{64}$"),
        authorized: AuthorizedContext = Depends(read),
    ) -> Any:
        return _run(
            lambda: service.approval_validity(
                matter_id=_path_uuid(request, "matter_id"),
                work_product_id=_path_uuid(request, "work_product_id"),
                approval_id=_path_uuid(request, "approval_id"),
                binding=VersionBinding(
                    work_product_version_id=version_id,
                    version_number=version_number,
                    content_sha256=content_digest,
                ),
                actor=_actor(authorized, authorized_at=clock()),
            )
        )

    base = "/v1/matters/{matter_id}/work-products"
    item = f"{base}/{{work_product_id}}"
    approval_path = f"{item}/approvals/{{approval_id}}"
    router.post(base, operation_id="work_products_create", status_code=201)(create)
    router.get(base, operation_id="work_products_list")(list_for_matter)
    router.get(item, operation_id="work_products_get")(get)
    router.post(f"{item}/versions", operation_id="work_products_create_version")(
        create_version
    )
    router.post(f"{item}/groundings", operation_id="work_products_ground_sentence")(
        ground_sentence
    )
    router.get(f"{item}/compare", operation_id="work_products_compare")(compare)
    router.post(f"{item}/validations", operation_id="work_products_validate")(validate)
    router.post(f"{item}/approvals", operation_id="approvals_request")(request_approval)
    router.post(f"{approval_path}/decision", operation_id="approvals_decide")(
        decide_approval
    )
    router.post(f"{approval_path}/revocation", operation_id="approvals_revoke")(
        revoke_approval
    )
    router.post(f"{approval_path}/supersession", operation_id="approvals_supersede")(
        supersede_approval
    )
    router.get(f"{approval_path}/validity", operation_id="approvals_validity")(
        approval_validity
    )
    return router
