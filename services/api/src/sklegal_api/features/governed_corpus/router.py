"""CapAuth protected routes for governed corpus retrieval."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
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
from sklegal_persistence.features.governed_corpus.models import canonical_sha256

from .contracts import (
    CorpusSearchCommand,
    CorpusSpanCommand,
    RecordCorpusSourceCommand,
)
from .service import (
    GovernedCorpusAccessContext,
    GovernedCorpusService,
    GovernedCorpusServiceError,
)


def _matter_id(request: Request) -> UUID:
    try:
        return UUID(str(request.path_params["matter_id"]))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found"},
        ) from None


def _context(authorized: AuthorizedContext) -> GovernedCorpusAccessContext:
    matter_id = authorized.decision.matter_id
    if matter_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "access_denied"},
        )
    return GovernedCorpusAccessContext.model_validate(
        {
            "tenant_id": authorized.principal.tenant_id,
            "matter_id": matter_id,
            "principal_id": authorized.principal.principal_id,
            "capability": authorized.decision.capability.value,
            "purpose": authorized.decision.purpose.value,
            "authorization_decision_id": authorized.decision.decision_id,
            "authorization_context_sha256": canonical_sha256(
                {
                    "schema": "sklegal.governed-corpus-auth-context/v1",
                    "principal": authorized.principal.model_dump(mode="json"),
                    "principal_chain": [
                        item.model_dump(mode="json")
                        for item in authorized.principal_chain
                    ],
                    "grant": authorized.grant.model_dump(mode="json"),
                    "verifier_policy_version": (
                        authorized.decision.verifier_policy_version
                    ),
                    "trusted_issuer_policy_revision": (
                        authorized.decision.trusted_issuer_policy_revision
                    ),
                    "principal_policy_revisions": [
                        item.model_dump(mode="json")
                        for item in authorized.decision.principal_policy_revisions
                    ],
                    "revocation_revision": authorized.decision.revocation_revision,
                    "delegation_depth": authorized.decision.delegation_depth,
                }
            ),
            "credential_expires_at": authorized.credential_expires_at,
        }
    )


def _http_error(error: GovernedCorpusServiceError) -> HTTPException:
    code = error.code
    status_code = {
        "authentication_required": status.HTTP_401_UNAUTHORIZED,
        "access_denied": status.HTTP_403_FORBIDDEN,
        "not_found": status.HTTP_404_NOT_FOUND,
        "stale_projection": status.HTTP_409_CONFLICT,
        "validation_failed": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "idempotency_conflict": status.HTTP_409_CONFLICT,
        "version_conflict": status.HTTP_409_CONFLICT,
        "policy_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "resource_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }[code]
    return HTTPException(status_code=status_code, detail={"code": code})


def build_governed_corpus_router(
    *,
    service: GovernedCorpusService,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> APIRouter:
    router = APIRouter()

    def protected(
        capability: Capability, purpose: Purpose, route_name: str
    ) -> ProtectedRouteDependency:
        boundary: ApiCapabilityBoundary[object] = ApiCapabilityBoundary(
            authorizer=authorizer,
            route_name=f"governed_corpus.{route_name}",
            capability=capability,
            purpose=purpose,
        )
        return ProtectedRouteDependency(
            boundary=boundary,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )

    async def search(
        request: Request,
        command: CorpusSearchCommand,
        authorized: AuthorizedContext = Depends(
            protected(Capability.CORPUS_SEARCH, Purpose.LEGAL_RESEARCH, "search")
        ),
    ) -> Any:
        try:
            return service.search(
                context=_context(authorized),
                matter_id=_matter_id(request),
                command=command,
            ).model_dump(mode="json", by_alias=True)
        except GovernedCorpusServiceError as error:
            raise _http_error(error) from None

    async def span(
        request: Request,
        source_id: str,
        expected_release_id: str = Query(alias="expectedReleaseId"),
        expected_projection_generation: int = Query(
            ge=1, alias="expectedProjectionGeneration"
        ),
        required_core_watermark: int = Query(ge=0, alias="requiredCoreWatermark"),
        authorized: AuthorizedContext = Depends(
            protected(
                Capability.CORPUS_ARTIFACT_READ,
                Purpose.LEGAL_RESEARCH,
                "span",
            )
        ),
    ) -> Any:
        try:
            return service.read_span(
                context=_context(authorized),
                matter_id=_matter_id(request),
                command=CorpusSpanCommand(
                    source_id=source_id,
                    expected_release_id=expected_release_id,
                    expected_projection_generation=expected_projection_generation,
                    required_core_watermark=required_core_watermark,
                ),
            ).model_dump(mode="json", by_alias=True)
        except GovernedCorpusServiceError as error:
            raise _http_error(error) from None

    async def record_source(
        request: Request,
        command: RecordCorpusSourceCommand,
        idempotency_key: str = Header(
            min_length=1, max_length=200, alias="Idempotency-Key"
        ),
        authorized: AuthorizedContext = Depends(
            protected(
                Capability.CORPUS_INGEST_SUBMIT,
                Purpose.CORPUS_INGESTION,
                "record_source",
            )
        ),
    ) -> Any:
        try:
            return service.record_source(
                context=_context(authorized),
                matter_id=_matter_id(request),
                idempotency_key=idempotency_key,
                command=command,
            ).model_dump(mode="json", by_alias=True)
        except GovernedCorpusServiceError as error:
            raise _http_error(error) from None

    router.post(
        "/v1/matters/{matter_id}/corpus/search",
        operation_id="governed_corpus_search",
    )(search)
    router.get(
        "/v1/matters/{matter_id}/corpus/sources/{source_id}/span",
        operation_id="governed_corpus_span",
    )(span)
    router.post(
        "/v1/matters/{matter_id}/corpus/sources",
        operation_id="governed_corpus_record_source",
    )(record_source)
    return router


__all__ = ["build_governed_corpus_router"]
