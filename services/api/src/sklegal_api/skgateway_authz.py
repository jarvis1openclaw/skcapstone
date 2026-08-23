"""Narrow SKLegal authorization adapter for the SKGateway live path.

This module is an HTTP boundary only. It does not implement authorization and
does not call the broad CapAuth service. The injected evaluator must delegate
to the canonical SKLegal CapAuth and policy gateways.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sklegal_audit import (
    AuditBoundary,
    PostgresAuditRepository,
    RequestCorrelatedDurableAuditSink,
)
from sklegal_capauth import (
    Audience,
    AuthorizedContext,
    Capability,
    FileTrustedIssuerBackend,
    ModelCapabilityBoundary,
    PostgresPrincipalPolicyBackend,
    PostgresRevocationBackend,
    Purpose,
    TrustedIssuerBackend,
    VersionedTrustedIssuerBackend,
)
from sklegal_model_gateway import SkGatewayRoutePolicyVerifier
from sklegal_model_gateway.errors import ModelGatewayError
from sklegal_policies import (
    CapAuthCurrentStateVerifier,
    DataFlowBoundary,
    PolicyAccessRequest,
    PolicyBoundaryRequirement,
    PolicyDenied,
    PolicyGateway,
    PolicyReason,
    PostgresAuthorizationUseBackend,
    PostgresPolicyBackend,
)

from .capauth import (
    PrincipalResolver,
    ProtectedRouteDependency,
    ScopeResolver,
    build_postgres_capability_authorizer,
)

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
DecisionReason = Literal[
    "allow",
    "policy_denied",
    "capability_denied",
    "audit_unavailable",
]
SKGATEWAY_AUTHZ_PRODUCTION_COMPOSITION_REVISION = (
    "sklegal-skgateway-authz-production-composition/v1"
)
_SAFE_RESPONSE_FIELDS = frozenset(
    {
        "allow",
        "reason",
        "decision_id",
        "policy_revision",
        "correlation_id",
        "obligations",
    }
)
_REQUIRED_PROHIBITED_FIELDS = frozenset(
    {
        "prompt",
        "matter_content",
        "source_span",
        "bearer_token",
        "private_key",
        "raw_capability",
    }
)
SKGATEWAY_AUTHZ_SNAPSHOT_SQL = "SELECT sklegal_legal.skgateway_authorization_snapshot(%s, %s, %s::jsonb, %s::jsonb)"
SqlExecutor = Callable[[str, tuple[object, ...]], object]


class SkGatewayAuthzDeployment(BaseModel):
    """Strict deployment contract for the disabled chiap01 adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["sklegal-skgateway-authz-adapter/v1"] = Field(alias="schema")
    card: Literal["a060fa3d"]
    enabled: Literal[False]
    endpoint_reference: Literal["SKLEGAL_CAPAUTH_AUTHZ_ENDPOINT"]
    bind: Literal["loopback-only"]
    service_identity: Literal["capauth:sklegal-model-gateway@chiap01.skworld"]
    service_secret_reference: str
    service_header: Literal["X-SKLegal-Service-Authorization"]
    capability: Literal["skgateway.infer"]
    transport: Literal["authenticated-local-http"]
    fail_closed: Literal[True]
    allow_cache: Literal[False]
    protected_traffic: Literal["denied-until-live-path-report-and-human-approval"]
    response_fields: list[str]
    prohibited_fields: list[str]

    @model_validator(mode="after")
    def validate_security_surface(self) -> Self:
        if not self.service_secret_reference.startswith("vault:"):
            raise ValueError("service secret must be a vault reference")
        if set(self.response_fields) != _SAFE_RESPONSE_FIELDS:
            raise ValueError(
                "authorization response fields must match the safe contract"
            )
        if not _REQUIRED_PROHIBITED_FIELDS.issubset(self.prohibited_fields):
            raise ValueError("authorization prohibited-field set is incomplete")
        return self


def load_skgateway_authz_deployment(path: Path) -> SkGatewayAuthzDeployment:
    """Load the deployment contract with bounded, fail-closed parsing."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("authorization deployment contract must be a regular file")
    raw = path.read_bytes()
    if len(raw) > 32 * 1024:
        raise ValueError("authorization deployment contract exceeds the size limit")
    if any(byte >= 128 for byte in raw):
        raise ValueError("authorization deployment contract must be ASCII")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("authorization deployment contract is malformed") from exc
    return SkGatewayAuthzDeployment.model_validate(payload)


class SkGatewayAuthzRequest(BaseModel):
    """Trusted facts supplied by the SKLegal PEP, not a grant by themselves."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: ShortText
    capability: ShortText
    resource: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=16)
    context: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=16)


class SkGatewayAuthzResponse(BaseModel):
    """Sanitized decision data safe for the SKGateway audit boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allow: bool
    reason: DecisionReason
    decision_id: ShortText | None = None
    policy_revision: ShortText | None = None
    correlation_id: ShortText | None = None
    obligations: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_decision_reason(self) -> Self:
        if self.allow != (self.reason == "allow"):
            raise ValueError("authorization decision reason disagrees with allow")
        return self


class SkGatewayTrustedFacts(BaseModel):
    """Facts rehydrated by SKLegal trusted state, never accepted as authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: ShortText
    capability: ShortText
    resource: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=16)
    context: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=16)


class SkGatewayTrustedSnapshot(BaseModel):
    """One current-state answer from the durable SKLegal authorization store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: ShortText
    service_identity: ShortText
    facts: SkGatewayTrustedFacts


class SkGatewayTrustedStateBackend(Protocol):
    """Durable source for current service, scope, and policy facts."""

    def snapshot(
        self,
        *,
        service_identity: str,
        request: SkGatewayAuthzRequest,
    ) -> SkGatewayTrustedSnapshot: ...


class SkGatewayRouteVerifier(Protocol):
    """Verify a trusted snapshot against canonical route and egress policy."""

    def verify(
        self,
        *,
        service_identity: str,
        capability: str,
        resource: Mapping[str, str],
        context: Mapping[str, str],
    ) -> object: ...


class PostgresSkGatewayTrustedStateBackend:
    """Load one atomic, current authorization snapshot from PostgreSQL."""

    def __init__(self, executor: SqlExecutor) -> None:
        if not callable(executor):
            raise TypeError("executor must be callable")
        self._executor = executor

    def snapshot(
        self,
        *,
        service_identity: str,
        request: SkGatewayAuthzRequest,
    ) -> SkGatewayTrustedSnapshot:
        params = (
            service_identity,
            request.subject,
            json.dumps(request.resource, sort_keys=True, separators=(",", ":")),
            json.dumps(request.context, sort_keys=True, separators=(",", ":")),
        )
        try:
            row = self._executor(SKGATEWAY_AUTHZ_SNAPSHOT_SQL, params)
            if isinstance(row, tuple) and len(row) == 1:
                row = row[0]
            if isinstance(row, bytes):
                row = row.decode("ascii")
            if isinstance(row, str):
                row = json.loads(row)
            if not isinstance(row, Mapping):
                raise TypeError("snapshot query returned the wrong shape")
            return SkGatewayTrustedSnapshot.model_validate(row)
        except Exception as exc:
            raise RuntimeError("trusted authorization snapshot unavailable") from exc


class SkGatewayFactsResolver(Protocol):
    """Resolve wire facts through authenticated SKLegal state."""

    def resolve(self, request: SkGatewayAuthzRequest) -> SkGatewayTrustedFacts: ...


class CanonicalSkGatewayFactsResolver:
    """Resolve untrusted selectors against one current durable snapshot."""

    def __init__(
        self,
        *,
        backend: SkGatewayTrustedStateBackend,
        route_verifier: SkGatewayRouteVerifier,
        service_identity: str,
    ) -> None:
        if not service_identity or not service_identity.strip():
            raise ValueError("service identity is required")
        self._backend = backend
        self._route_verifier = route_verifier
        self._service_identity = service_identity

    def resolve(self, request: SkGatewayAuthzRequest) -> SkGatewayTrustedFacts:
        snapshot = self._backend.snapshot(
            service_identity=self._service_identity,
            request=request,
        )
        if not isinstance(snapshot, SkGatewayTrustedSnapshot):
            raise SkGatewayScopeDenied
        if snapshot.service_identity != self._service_identity:
            raise SkGatewayScopeDenied
        facts = snapshot.facts
        if facts.subject != request.subject:
            raise SkGatewayScopeDenied
        if facts.capability != request.capability:
            raise SkGatewayScopeDenied
        if facts.resource != request.resource:
            raise SkGatewayScopeDenied
        if facts.context != request.context:
            raise SkGatewayScopeDenied
        try:
            self._route_verifier.verify(
                service_identity=self._service_identity,
                capability=facts.capability,
                resource=facts.resource,
                context=facts.context,
            )
        except ModelGatewayError:
            raise SkGatewayScopeDenied from None
        return facts


class SkGatewayAuthzEvaluator(Protocol):
    """Canonical SKLegal policy decision seam."""

    def decide(
        self,
        *,
        subject: str,
        capability: str,
        resource: Mapping[str, str],
        context: Mapping[str, str],
        authorized: AuthorizedContext,
    ) -> SkGatewayAuthzResponse: ...


class CanonicalSkGatewayPolicyEvaluator:
    """Evaluate model context through the canonical policy gateway.

    The external ``skgateway.infer`` scope selects this narrow endpoint. The
    actual material authority remains the exact request-local CapAuth grant.
    PolicyGateway performs current-state reservation, scope matching,
    information-barrier evaluation, and durable decision audit.
    """

    _CAPAUTH_REASONS = frozenset(
        {
            PolicyReason.CAPAUTH_SCOPE_MISMATCH,
            PolicyReason.CAPAUTH_EXPIRED,
            PolicyReason.CAPAUTH_REPLAYED,
            PolicyReason.CAPAUTH_STALE,
        }
    )
    _OUTAGE_REASONS = frozenset(
        {
            PolicyReason.AUDIT_UNAVAILABLE,
            PolicyReason.CAPAUTH_CURRENT_STATE_UNAVAILABLE,
            PolicyReason.POLICY_UNAVAILABLE,
        }
    )

    def __init__(self, gateway: PolicyGateway) -> None:
        if not isinstance(gateway, PolicyGateway):
            raise TypeError("gateway must be the canonical PolicyGateway")
        self._gateway = gateway

    @staticmethod
    def _require_exact_wire_scope(
        *,
        subject: str,
        capability: str,
        resource: Mapping[str, str],
        context: Mapping[str, str],
        authorized: AuthorizedContext,
    ) -> None:
        grant = authorized.grant
        expected_resource = {
            "tenant_id": str(grant.tenant_id),
            "matter_id": str(grant.matter_id),
            "material_id": str(grant.resource_id),
            "material_version": str(grant.resource_version),
        }
        if capability != "skgateway.infer":
            raise SkGatewayScopeDenied
        if subject != authorized.principal.subject:
            raise SkGatewayScopeDenied
        if grant.audience != Audience.MODEL:
            raise SkGatewayScopeDenied
        if grant.matter_id is None or grant.resource_id is None:
            raise SkGatewayScopeDenied
        if grant.resource_version is None or grant.resource_sha256 is None:
            raise SkGatewayScopeDenied
        if grant.model_route is None or grant.workflow_run_id is None:
            raise SkGatewayScopeDenied
        for key, expected in expected_resource.items():
            if resource.get(key) != expected:
                raise SkGatewayScopeDenied
        if not resource.get("route_id"):
            raise SkGatewayScopeDenied
        if context.get("purpose") != grant.purpose.value:
            raise SkGatewayScopeDenied

    def decide(
        self,
        *,
        subject: str,
        capability: str,
        resource: Mapping[str, str],
        context: Mapping[str, str],
        authorized: AuthorizedContext,
    ) -> SkGatewayAuthzResponse:
        self._require_exact_wire_scope(
            subject=subject,
            capability=capability,
            resource=resource,
            context=context,
            authorized=authorized,
        )
        grant = authorized.grant
        assert grant.matter_id is not None
        assert grant.resource_id is not None
        assert grant.resource_version is not None
        assert grant.resource_sha256 is not None
        assert grant.model_route is not None
        assert grant.workflow_run_id is not None
        request = PolicyAccessRequest(
            boundary=DataFlowBoundary.MODEL_CONTEXT,
            principal_id=authorized.principal.principal_id,
            tenant_id=grant.tenant_id,
            matter_id=grant.matter_id,
            material_id=UUID(grant.resource_id),
            material_version=grant.resource_version,
            material_sha256=grant.resource_sha256,
            purpose=grant.purpose,
            model_route=grant.model_route,
            workflow_run_id=grant.workflow_run_id,
            evaluated_at=datetime.now(UTC),
        )
        requirement = PolicyBoundaryRequirement(
            boundary=DataFlowBoundary.MODEL_CONTEXT,
            audience=grant.audience,
            target=grant.target,
            capability=grant.capability,
            purpose=grant.purpose,
            model_route=grant.model_route,
            workflow_run_id=grant.workflow_run_id,
        )
        try:
            result = self._gateway.authorize(authorized, requirement, request)
        except PolicyDenied as exc:
            decision = exc.decision
            if decision.reason in self._OUTAGE_REASONS:
                raise SkGatewayAuthorizationDependencyUnavailable from None
            if decision.reason in self._CAPAUTH_REASONS:
                reason = "capability_denied"
            else:
                reason = "policy_denied"
            return SkGatewayAuthzResponse(
                allow=False,
                reason=reason,
                decision_id=str(decision.decision_id),
                policy_revision=decision.policy_revision,
                correlation_id=str(decision.correlation_id),
            )
        decision = result.decision
        return SkGatewayAuthzResponse(
            allow=True,
            reason="allow",
            decision_id=str(decision.decision_id),
            policy_revision=decision.policy_revision,
            correlation_id=str(decision.correlation_id),
        )


class ServiceAuthenticationRequired(PermissionError):
    """The internal caller supplied no service credential."""


class ServiceAuthenticationDenied(PermissionError):
    """The internal service credential is invalid or no longer usable."""


class ServiceAuthenticationUnavailable(RuntimeError):
    """The trusted service-credential state cannot produce a current answer."""


class SkGatewayScopeDenied(PermissionError):
    """Trusted scope does not authorize the requested gateway operation."""


class SkGatewayAuthorizationDependencyUnavailable(RuntimeError):
    """A canonical authorization dependency has no authoritative answer."""


class SkGatewayServiceAuthenticator(Protocol):
    """Authenticate the SKGateway caller through trusted credential state."""

    def authenticate(self, authorization: str | None) -> str: ...


class ConstantTimeBearerServiceAuthenticator:
    """Authenticate one secret-custodied bearer against a pinned identity."""

    def __init__(self, *, service_identity: str, service_token: str) -> None:
        if not service_identity or not service_identity.strip():
            raise ValueError("service identity must be configured")
        if not service_token or not service_token.strip():
            raise ValueError("service token must be configured by secret custody")
        self._service_identity = service_identity
        self._expected = f"Bearer {service_token}"

    def authenticate(self, authorization: str | None) -> str:
        if authorization is None:
            raise ServiceAuthenticationRequired
        if not hmac.compare_digest(authorization, self._expected):
            raise ServiceAuthenticationDenied
        return self._service_identity


class VaultServiceCredentialBackend(Protocol):
    """Verify a credential through custody without returning secret material."""

    production_ready: bool

    def available(self, *, reference: str, service_identity: str) -> bool: ...

    def verify(
        self,
        *,
        reference: str,
        service_identity: str,
        authorization: str,
    ) -> bool: ...


class VaultReferenceServiceAuthenticator:
    """Authenticate against one approved vault reference and pinned identity."""

    def __init__(
        self,
        *,
        backend: VaultServiceCredentialBackend,
        secret_reference: str,
        service_identity: str,
    ) -> None:
        if not secret_reference.startswith("vault:"):
            raise ValueError("service credential must use a vault reference")
        if not service_identity or not service_identity.strip():
            raise ValueError("service identity must be configured")
        if getattr(backend, "production_ready", False) is not True:
            raise TypeError("service credential backend is not production-ready")
        self._backend = backend
        self._secret_reference = secret_reference
        self._service_identity = service_identity

    def require_ready(self) -> None:
        try:
            ready = self._backend.available(
                reference=self._secret_reference,
                service_identity=self._service_identity,
            )
        except Exception:
            raise ServiceAuthenticationUnavailable from None
        if ready is not True:
            raise ServiceAuthenticationUnavailable

    def authenticate(self, authorization: str | None) -> str:
        if authorization is None:
            raise ServiceAuthenticationRequired
        try:
            verified = self._backend.verify(
                reference=self._secret_reference,
                service_identity=self._service_identity,
                authorization=authorization,
            )
        except Exception:
            raise ServiceAuthenticationUnavailable from None
        if not isinstance(verified, bool):
            raise ServiceAuthenticationUnavailable
        if not verified:
            raise ServiceAuthenticationDenied
        return self._service_identity


class SkGatewayProductionReadinessBackend(Protocol):
    """Qualification-owned readiness checks for each durable dependency."""

    production_ready: bool

    def check(self, dependency: str) -> bool: ...


class SkGatewayProductionCompositionUnavailable(RuntimeError):
    """The production endpoint cannot start with authoritative dependencies."""


_PRODUCTION_DEPENDENCIES = (
    "trusted_state",
    "service_identity",
    "principal_state",
    "revocation",
    "replay",
    "route_policy",
    "material_policy",
    "append_only_audit",
)


@dataclass(frozen=True, slots=True)
class SkGatewayAuthzProductionComposition:
    """Ready, loopback-only composition for isolated qualification."""

    router: APIRouter
    revision: Literal["sklegal-skgateway-authz-production-composition/v1"]
    bind_host: Literal["127.0.0.1"]
    endpoint_reference: Literal["SKLEGAL_CAPAUTH_AUTHZ_ENDPOINT"]
    service_identity: str
    service_secret_reference: str
    transport: Literal["authenticated-local-http"]
    profile_enabled: Literal[False]
    protected_traffic: Literal[False]


def build_skgateway_authz_rollback_router() -> APIRouter:
    """Return the deterministic denial surface used by rollback."""

    router = APIRouter()

    @router.post("/v1/authz/decide")
    async def unavailable() -> None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "authorization_backend_unavailable"},
        )

    return router


def build_skgateway_authz_production_composition(
    *,
    deployment: SkGatewayAuthzDeployment,
    executor: SqlExecutor,
    trusted_issuers: TrustedIssuerBackend,
    audit_repository: PostgresAuditRepository,
    service_credentials: VaultServiceCredentialBackend,
    route_verifier: SkGatewayRoutePolicyVerifier,
    readiness: SkGatewayProductionReadinessBackend,
    tenant_id: UUID,
    clock: Callable[[], datetime],
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> SkGatewayAuthzProductionComposition:
    """Compose the canonical endpoint from durable, production-owned adapters."""

    if deployment.enabled is not False or deployment.bind != "loopback-only":
        raise SkGatewayProductionCompositionUnavailable(
            "SKGateway authorization profile must remain disabled and loopback-only"
        )
    if deployment.transport != "authenticated-local-http":
        raise SkGatewayProductionCompositionUnavailable(
            "SKGateway authorization transport is not authenticated local HTTP"
        )
    if not isinstance(
        trusted_issuers,
        (FileTrustedIssuerBackend, VersionedTrustedIssuerBackend),
    ):
        raise TypeError("trusted issuer state must use a durable canonical backend")
    if not isinstance(audit_repository, PostgresAuditRepository):
        raise TypeError("audit repository must use the append-only PostgreSQL adapter")
    if not isinstance(route_verifier, SkGatewayRoutePolicyVerifier):
        raise TypeError("route policy must use the canonical SKGateway verifier")
    if getattr(readiness, "production_ready", False) is not True:
        raise TypeError("readiness backend must be qualification-owned")
    try:
        for dependency in _PRODUCTION_DEPENDENCIES:
            if readiness.check(dependency) is not True:
                raise SkGatewayProductionCompositionUnavailable(
                    "required authorization dependency is unavailable"
                )
    except SkGatewayProductionCompositionUnavailable:
        raise
    except Exception:
        raise SkGatewayProductionCompositionUnavailable(
            "required authorization dependency is unavailable"
        ) from None

    service_authenticator = VaultReferenceServiceAuthenticator(
        backend=service_credentials,
        secret_reference=deployment.service_secret_reference,
        service_identity=deployment.service_identity,
    )
    try:
        service_authenticator.require_ready()
    except ServiceAuthenticationUnavailable:
        raise SkGatewayProductionCompositionUnavailable(
            "service authentication dependency is unavailable"
        ) from None

    audit_sink = RequestCorrelatedDurableAuditSink(
        repository=audit_repository,
        boundary=AuditBoundary.MODEL,
        clock=clock,
    )

    authorizer = build_postgres_capability_authorizer(
        executor=executor,
        trusted_issuers=trusted_issuers,
        audit=audit_sink,
        tenant_id=tenant_id,
        clock=clock,
    )
    model_boundary: ModelCapabilityBoundary[object] = ModelCapabilityBoundary(
        authorizer=authorizer,
        model_target="qwen.generate",
        capability=Capability.CORPUS_ARTIFACT_READ,
        purpose=Purpose.LEGAL_RESEARCH,
    )
    protected_route = ProtectedRouteDependency(
        boundary=model_boundary,
        principal_resolver=principal_resolver,
        scope_resolver=scope_resolver,
    )
    current_authorization = CapAuthCurrentStateVerifier(
        trusted_issuers=trusted_issuers,
        principals=PostgresPrincipalPolicyBackend(executor),
        revocations=PostgresRevocationBackend(executor, tenant_id=tenant_id),
        uses=PostgresAuthorizationUseBackend(executor, tenant_id=tenant_id),
    )
    policy_gateway = PolicyGateway(
        backend=PostgresPolicyBackend(executor),
        audit_sink=audit_sink,
        current_authorization=current_authorization,
        clock=clock,
    )
    resolver = CanonicalSkGatewayFactsResolver(
        backend=PostgresSkGatewayTrustedStateBackend(executor),
        route_verifier=route_verifier,
        service_identity=deployment.service_identity,
    )
    router = build_skgateway_authz_router(
        facts_resolver=resolver,
        evaluator=CanonicalSkGatewayPolicyEvaluator(policy_gateway),
        service_authenticator=service_authenticator,
        protected_route=protected_route,
        service_identity=deployment.service_identity,
    )
    return SkGatewayAuthzProductionComposition(
        router=router,
        revision=SKGATEWAY_AUTHZ_PRODUCTION_COMPOSITION_REVISION,
        bind_host="127.0.0.1",
        endpoint_reference=deployment.endpoint_reference,
        service_identity=deployment.service_identity,
        service_secret_reference=deployment.service_secret_reference,
        transport=deployment.transport,
        profile_enabled=False,
        protected_traffic=False,
    )


def build_skgateway_authz_router(
    *,
    facts_resolver: SkGatewayFactsResolver,
    evaluator: SkGatewayAuthzEvaluator,
    service_authenticator: SkGatewayServiceAuthenticator,
    protected_route: ProtectedRouteDependency,
    service_identity: str,
) -> APIRouter:
    """Build the internal decision router around a canonical evaluator.

    Credential verification is injected from trusted state and its result must
    match the deployment-pinned service identity. No credential is returned,
    logged, or placed in the request model.
    """

    if not service_identity or not service_identity.strip():
        raise ValueError("service identity must be configured")
    router = APIRouter()

    @router.post(
        "/v1/authz/decide",
        response_model=SkGatewayAuthzResponse,
        response_model_exclude_none=True,
    )
    async def decide(
        request: Request,
        payload: SkGatewayAuthzRequest,
        service_authorization: str | None = Header(
            default=None,
            alias="X-SKLegal-Service-Authorization",
        ),
    ) -> SkGatewayAuthzResponse:
        try:
            authenticated_identity = service_authenticator.authenticate(
                service_authorization
            )
        except ServiceAuthenticationRequired:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "authentication_required"},
            ) from None
        except ServiceAuthenticationDenied:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "invalid_service_credential"},
            ) from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_backend_unavailable"},
            ) from None
        if authenticated_identity != service_identity:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "invalid_service_identity"},
            )
        if payload.capability != "skgateway.infer":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "capability_denied"},
            )
        try:
            authorized = await protected_route(request)
            trusted = facts_resolver.resolve(payload)
            if trusted.capability != "skgateway.infer":
                raise ValueError("trusted capability mismatch")
            result = evaluator.decide(
                subject=trusted.subject,
                capability=trusted.capability,
                resource=trusted.resource,
                context=trusted.context,
                authorized=authorized,
            )
        except HTTPException:
            raise
        except SkGatewayScopeDenied:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "scope_denied"},
            ) from None
        except Exception as exc:
            del exc
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_backend_unavailable"},
            ) from None
        if not isinstance(result, SkGatewayAuthzResponse):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_backend_unavailable"},
            )
        return result

    return router
