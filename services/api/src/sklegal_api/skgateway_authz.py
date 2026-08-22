"""Narrow SKLegal authorization adapter for the SKGateway live path.

This module is an HTTP boundary only. It does not implement authorization and
does not call the broad CapAuth service. The injected evaluator must delegate
to the canonical SKLegal CapAuth and policy gateways.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
DecisionReason = Literal[
    "allow",
    "policy_denied",
    "capability_denied",
    "audit_unavailable",
]
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
SKGATEWAY_AUTHZ_SNAPSHOT_SQL = (
    "SELECT sklegal_legal.skgateway_authorization_snapshot(%s, %s, %s::jsonb, %s::jsonb)"
)
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
            raise ValueError("authorization response fields must match the safe contract")
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
            raise ValueError("trusted state backend returned the wrong type")
        if snapshot.service_identity != self._service_identity:
            raise ValueError("trusted service identity does not match the deployment pin")
        facts = snapshot.facts
        if facts.subject != request.subject:
            raise ValueError("trusted subject does not match the requested selector")
        if facts.capability != request.capability:
            raise ValueError("trusted capability does not match the requested capability")
        if facts.resource != request.resource:
            raise ValueError("trusted resource scope does not match the requested scope")
        if facts.context != request.context:
            raise ValueError("trusted policy context does not match the requested context")
        self._route_verifier.verify(
            service_identity=self._service_identity,
            capability=facts.capability,
            resource=facts.resource,
            context=facts.context,
        )
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
    ) -> SkGatewayAuthzResponse: ...


class ServiceAuthenticationRequired(PermissionError):
    """The internal caller supplied no service credential."""


class ServiceAuthenticationDenied(PermissionError):
    """The internal service credential is invalid or no longer usable."""


class ServiceAuthenticationUnavailable(RuntimeError):
    """The trusted service-credential state cannot produce a current answer."""


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


def build_skgateway_authz_router(
    *,
    facts_resolver: SkGatewayFactsResolver,
    evaluator: SkGatewayAuthzEvaluator,
    service_authenticator: SkGatewayServiceAuthenticator,
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
    def decide(
        payload: SkGatewayAuthzRequest,
        authorization: str | None = Header(default=None),
    ) -> SkGatewayAuthzResponse:
        try:
            authenticated_identity = service_authenticator.authenticate(authorization)
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
            trusted = facts_resolver.resolve(payload)
            if trusted.capability != "skgateway.infer":
                raise ValueError("trusted capability mismatch")
            result = evaluator.decide(
                subject=trusted.subject,
                capability=trusted.capability,
                resource=trusted.resource,
                context=trusted.context,
            )
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
