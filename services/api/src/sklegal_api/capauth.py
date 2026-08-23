"""FastAPI dependency for the SKLegal CapAuth protected-route boundary."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sklegal_capauth import (
    AuditSink,
    AuthorizationDenied,
    AuthorizedContext,
    BoundaryScope,
    CapabilityAuthorizer,
    CredentialFormatError,
    InMemoryAuditSink,
    PostgresPrincipalPolicyBackend,
    PostgresReplayBackend,
    PostgresRevocationBackend,
    PresentedCapability,
    PrincipalContext,
    ProtectedBoundary,
    SignatureVerificationCache,
    StaticTrustedIssuerBackend,
    TrustedIssuerBackend,
    TrustedIssuerSnapshot,
    UnavailableAuditSink,
    UnavailableTrustedIssuerBackend,
    parse_authorization_bearer,
)

type PrincipalResolution = PrincipalContext | Awaitable[PrincipalContext]
type ScopeResolution = BoundaryScope | Awaitable[BoundaryScope]
type PrincipalResolver = Callable[[Request], PrincipalResolution]
type ScopeResolver = Callable[[Request], ScopeResolution]
type SqlExecutor = Callable[[str, tuple[object, ...]], object]

_SYNTHETIC_ISSUER_BACKENDS = (
    StaticTrustedIssuerBackend,
    UnavailableTrustedIssuerBackend,
)
_SYNTHETIC_AUDIT_SINKS = (InMemoryAuditSink, UnavailableAuditSink)


def build_postgres_capability_authorizer(
    *,
    executor: SqlExecutor,
    trusted_issuers: TrustedIssuerBackend,
    audit: AuditSink,
    tenant_id: UUID,
    clock: Callable[[], datetime],
) -> CapabilityAuthorizer:
    """Build the fail-closed CapAuth authorizer over durable PostgreSQL state.

    Composition is production-readiness validation: every dependency is
    checked before the authorizer exists, synthetic or unavailable adapters
    are refused, and the trusted issuer backend must answer a readiness
    probe. Any misconfiguration fails closed here instead of at the first
    protected request.
    """

    if not callable(executor):
        raise TypeError("executor must be a callable SQL executor")
    if isinstance(trusted_issuers, _SYNTHETIC_ISSUER_BACKENDS):
        raise TypeError("trusted issuer backend must be durable and reloadable")
    if isinstance(audit, _SYNTHETIC_AUDIT_SINKS):
        raise TypeError("audit sink must be durable")
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID")
    if not callable(clock):
        raise TypeError("clock must be callable")
    issuer_snapshot = trusted_issuers.snapshot()
    if not isinstance(issuer_snapshot, TrustedIssuerSnapshot):
        raise TypeError("trusted issuer backend returned the wrong type")

    return CapabilityAuthorizer(
        trusted_issuers=trusted_issuers,
        principals=PostgresPrincipalPolicyBackend(executor),
        revocations=PostgresRevocationBackend(executor, tenant_id=tenant_id),
        replay=PostgresReplayBackend(executor, tenant_id=tenant_id),
        audit=audit,
        signature_cache=SignatureVerificationCache(clock=clock),
        clock=clock,
    )


class ResolutionDenied(PermissionError):
    """A sanitized authentication or scope-resolution denial."""

    def __init__(self, *, status_code: int = status.HTTP_401_UNAUTHORIZED) -> None:
        if status_code not in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }:
            raise ValueError("resolution denial must use 401 or 403")
        self.status_code = status_code
        super().__init__("identity resolution denied")


class ResolutionBackendUnavailable(RuntimeError):
    """A trusted identity or scope source has no current answer."""


async def _resolve(value: PrincipalResolution | ScopeResolution) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


class ProtectedRouteDependency:
    """Resolve trusted context and fail closed before a protected route runs."""

    def __init__(
        self,
        *,
        boundary: ProtectedBoundary[object],
        principal_resolver: PrincipalResolver,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._boundary = boundary
        self._principal_resolver = principal_resolver
        self._scope_resolver = scope_resolver

    async def __call__(self, request: Request) -> AuthorizedContext:
        try:
            principal = await _resolve(self._principal_resolver(request))
            scope = await _resolve(self._scope_resolver(request))
            if not isinstance(principal, PrincipalContext):
                raise TypeError("principal resolver returned the wrong type")
            if not isinstance(scope, BoundaryScope):
                raise TypeError("scope resolver returned the wrong type")
        except ResolutionDenied as exc:
            code = (
                "authentication_required"
                if exc.status_code == status.HTTP_401_UNAUTHORIZED
                else "identity_resolution_denied"
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": code},
            ) from None
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                status_code = status.HTTP_401_UNAUTHORIZED
                code = "authentication_required"
            elif exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                code = "authorization_backend_unavailable"
            else:
                status_code = status.HTTP_403_FORBIDDEN
                code = "identity_resolution_denied"
            raise HTTPException(
                status_code=status_code,
                detail={"code": code},
            ) from None
        except ResolutionBackendUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_backend_unavailable"},
            ) from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_backend_unavailable"},
            ) from None

        authorization = request.headers.get("Authorization", "")
        presented: PresentedCapability | None = None
        if authorization:
            try:
                scheme, separator, raw_bearer = authorization.partition(" ")
                if scheme.lower() != "bearer" or not separator or not raw_bearer:
                    raise CredentialFormatError("authorization scheme is malformed")
                presented = parse_authorization_bearer(raw_bearer)
            except CredentialFormatError:
                presented = PresentedCapability.single("{}")
        try:
            return self._boundary.authorize(
                principal=principal,
                scope=scope,
                correlation_id=uuid4(),
                presented=presented,
            )
        except AuthorizationDenied as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "capability_denied",
                    "decision_id": str(exc.decision.decision_id),
                },
            ) from None
