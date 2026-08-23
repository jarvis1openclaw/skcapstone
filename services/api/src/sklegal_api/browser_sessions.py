"""Bounded, cookie-backed browser sessions for the internal MVP."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sklegal_capauth import (
    PresentedCapability,
    PrincipalContext,
    parse_authorization_bearer,
)

SESSION_COOKIE = "__Host-sklegal_session"
SESSION_TTL = timedelta(minutes=30)
PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE = "development:public-synthetic:mvp"
SessionOperation = Literal["bootstrap", "refresh", "tenant_switch", "revoke"]
SessionOutcome = Literal["success", "deny"]
MutationResult = TypeVar("MutationResult")


class BrowserSessionModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class BootstrapRequest(BrowserSessionModel):
    credential_reference: str
    tenant_id: UUID


class TenantSwitchRequest(BrowserSessionModel):
    tenant_id: UUID


class BrowserPrincipal(BrowserSessionModel):
    id: UUID
    display_name: str
    capabilities: tuple[str, ...]
    tenant_ids: tuple[UUID, ...]


class BrowserTenant(BrowserSessionModel):
    id: UUID
    display_name: str


class BrowserSessionView(BrowserSessionModel):
    principal: BrowserPrincipal
    tenants: tuple[BrowserTenant, ...]
    active_tenant_id: UUID
    expires_at: datetime
    csrf_token: str


@dataclass(frozen=True, slots=True)
class BrowserSessionAuthentication:
    """Trusted server-side session state. Raw capabilities never leave it."""

    session_id: str
    principal: PrincipalContext
    display_name: str
    tenants: tuple[BrowserTenant, ...]
    active_tenant_id: UUID
    capability_names: tuple[str, ...]
    capabilities_by_request: dict[tuple[str, str], str]
    csrf_digest: str
    csrf_token: str
    expires_at: datetime

    def presented_for(self, request: Request) -> PresentedCapability | None:
        raw = self.capabilities_by_request.get((request.method, request.url.path))
        if raw is None:
            return None
        return parse_authorization_bearer(raw)


class BrowserSessionBackend(Protocol):
    def bootstrap(
        self, credential_reference: str, tenant_id: UUID
    ) -> BrowserSessionAuthentication: ...

    def resolve(self, session_id: str) -> BrowserSessionAuthentication | None: ...

    def rotate(
        self, session: BrowserSessionAuthentication
    ) -> BrowserSessionAuthentication: ...

    def switch_tenant(
        self, session: BrowserSessionAuthentication, tenant_id: UUID
    ) -> BrowserSessionAuthentication: ...

    def revoke(self, session_id: str) -> None: ...


class BrowserSessionBackendUnavailable(RuntimeError):
    """The trusted session backend cannot provide a current answer."""


@dataclass(frozen=True, slots=True)
class BrowserSessionAuditEvent:
    """Bounded security event with no cookie, CSRF, or capability material."""

    event_id: UUID
    correlation_id: UUID
    operation: SessionOperation
    outcome: SessionOutcome
    tenant_id: UUID
    principal_id: UUID | None
    session_sha256: str | None
    reason_code: str
    occurred_at: datetime
    provenance_revision: str = "sklegal-browser-session/v1"


class BrowserSessionAuditSink(Protocol):
    """Atomically append one outcome with its associated session mutation."""

    synthetic: bool

    def execute(
        self,
        event_factory: Callable[[SessionOutcome, str], BrowserSessionAuditEvent],
        mutation: Callable[[], MutationResult],
    ) -> MutationResult: ...


class InMemoryPublicSyntheticSessionAuditSink:
    """Append-only atomic audit sink for the public-synthetic composition."""

    synthetic = True

    def __init__(self) -> None:
        self._events: list[BrowserSessionAuditEvent] = []
        self.available = True

    @property
    def events(self) -> tuple[BrowserSessionAuditEvent, ...]:
        return tuple(self._events)

    def execute(
        self,
        event_factory: Callable[[SessionOutcome, str], BrowserSessionAuditEvent],
        mutation: Callable[[], MutationResult],
    ) -> MutationResult:
        if not self.available:
            raise BrowserSessionBackendUnavailable("session audit unavailable")
        try:
            result = mutation()
        except Exception:
            self._events.append(event_factory("deny", "session_operation_denied"))
            raise
        self._events.append(event_factory("success", "session_operation_succeeded"))
        return result


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class InMemoryPublicSyntheticSessionBackend:
    """Explicit development backend for public-synthetic fixtures only."""

    synthetic = True

    def __init__(
        self,
        *,
        principal: PrincipalContext,
        display_name: str,
        tenants: tuple[BrowserTenant, ...],
        capability_names: tuple[str, ...],
        capabilities_by_request: dict[tuple[str, str], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._principal = principal
        self._display_name = display_name
        self._tenants = tenants
        self._capability_names = capability_names
        self._capabilities_by_request = dict(capabilities_by_request)
        self._clock = clock
        self._sessions: dict[str, BrowserSessionAuthentication] = {}

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    def _new(self, tenant_id: UUID) -> BrowserSessionAuthentication:
        if self._principal.tenant_id != tenant_id:
            raise PermissionError("tenant does not match the synthetic principal")
        if tenant_id not in {tenant.id for tenant in self._tenants}:
            raise PermissionError("tenant membership is absent")
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = BrowserSessionAuthentication(
            session_id=session_id,
            principal=self._principal,
            display_name=self._display_name,
            tenants=self._tenants,
            active_tenant_id=tenant_id,
            capability_names=self._capability_names,
            capabilities_by_request=dict(self._capabilities_by_request),
            csrf_digest=_digest(csrf_token),
            csrf_token=csrf_token,
            expires_at=self._clock() + SESSION_TTL,
        )
        self._sessions[session_id] = session
        return session

    def bootstrap(
        self, credential_reference: str, tenant_id: UUID
    ) -> BrowserSessionAuthentication:
        if credential_reference != PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE:
            raise PermissionError("credential reference is not approved")
        return self._new(tenant_id)

    def resolve(self, session_id: str) -> BrowserSessionAuthentication | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= self._clock():
            self.revoke(session_id)
            return None
        return session

    def rotate(
        self, session: BrowserSessionAuthentication
    ) -> BrowserSessionAuthentication:
        self.revoke(session.session_id)
        return self._new(session.active_tenant_id)

    def switch_tenant(
        self, session: BrowserSessionAuthentication, tenant_id: UUID
    ) -> BrowserSessionAuthentication:
        if tenant_id != session.principal.tenant_id:
            raise PermissionError("tenant does not match the authenticated principal")
        self.revoke(session.session_id)
        return self._new(tenant_id)

    def revoke(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def revoke_principal(self, principal_id: UUID) -> None:
        for session_id, session in tuple(self._sessions.items()):
            if session.principal.principal_id == principal_id:
                self.revoke(session_id)

    def expire(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions[session_id] = replace(
                session, expires_at=self._clock() - timedelta(seconds=1)
            )


def _set_cookie(response: Response, session: BrowserSessionAuthentication) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.session_id,
        max_age=int(SESSION_TTL.total_seconds()),
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _view(session: BrowserSessionAuthentication) -> BrowserSessionView:
    return BrowserSessionView(
        principal=BrowserPrincipal(
            id=session.principal.principal_id,
            display_name=session.display_name,
            capabilities=session.capability_names,
            tenant_ids=tuple(tenant.id for tenant in session.tenants),
        ),
        tenants=session.tenants,
        active_tenant_id=session.active_tenant_id,
        expires_at=session.expires_at,
        csrf_token=session.csrf_token,
    )


def require_browser_session(request: Request) -> BrowserSessionAuthentication:
    session = getattr(request.state, "browser_session", None)
    if not isinstance(session, BrowserSessionAuthentication):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required"},
        )
    return session


def require_csrf(request: Request, session: BrowserSessionAuthentication) -> None:
    token = request.headers.get("X-CSRF-Token", "")
    if not token or not secrets.compare_digest(_digest(token), session.csrf_digest):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "csrf_denied"},
        )


def build_browser_session_router(
    backend: BrowserSessionBackend, audit: BrowserSessionAuditSink
) -> APIRouter:
    router = APIRouter()

    def execute_audited(
        request: Request,
        operation: SessionOperation,
        tenant_id: UUID,
        principal_id: UUID | None,
        session_id: str | None,
        mutation: Callable[[], MutationResult],
    ) -> MutationResult:
        correlation_id = request.state.correlation_id

        def event(
            outcome: SessionOutcome, reason_code: str
        ) -> BrowserSessionAuditEvent:
            return BrowserSessionAuditEvent(
                event_id=uuid4(),
                correlation_id=correlation_id,
                operation=operation,
                outcome=outcome,
                tenant_id=tenant_id,
                principal_id=principal_id,
                session_sha256=(
                    hashlib.sha256(session_id.encode("ascii")).hexdigest()
                    if session_id is not None
                    else None
                ),
                reason_code=reason_code,
                occurred_at=datetime.now(UTC),
            )

        return audit.execute(event, mutation)

    @router.post("/v1/session/bootstrap", operation_id="session_bootstrap")
    async def bootstrap(
        payload: BootstrapRequest, request: Request, response: Response
    ) -> BrowserSessionView:
        try:
            session = execute_audited(
                request,
                "bootstrap",
                payload.tenant_id,
                None,
                None,
                lambda: backend.bootstrap(
                    payload.credential_reference, payload.tenant_id
                ),
            )
        except BrowserSessionBackendUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "session_audit_unavailable"},
            ) from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "authentication_denied"},
            ) from None
        _set_cookie(response, session)
        response.headers["Cache-Control"] = "no-store"
        return _view(session)

    @router.get("/v1/session", operation_id="session_current")
    async def current(request: Request, response: Response) -> BrowserSessionView:
        response.headers["Cache-Control"] = "no-store"
        return _view(require_browser_session(request))

    @router.post("/v1/session/refresh", operation_id="session_refresh")
    async def refresh(request: Request, response: Response) -> BrowserSessionView:
        session = require_browser_session(request)
        require_csrf(request, session)
        try:
            replacement = execute_audited(
                request,
                "refresh",
                session.active_tenant_id,
                session.principal.principal_id,
                session.session_id,
                lambda: backend.rotate(session),
            )
        except BrowserSessionBackendUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "session_audit_unavailable"},
            ) from None
        except Exception:
            backend.revoke(session.session_id)
            _clear_cookie(response)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "session_refresh_denied"},
            ) from None
        _set_cookie(response, replacement)
        response.headers["Cache-Control"] = "no-store"
        return _view(replacement)

    @router.post("/v1/session/tenant", operation_id="session_tenant_switch")
    async def switch(
        payload: TenantSwitchRequest, request: Request, response: Response
    ) -> BrowserSessionView:
        session = require_browser_session(request)
        require_csrf(request, session)
        try:
            replacement = execute_audited(
                request,
                "tenant_switch",
                payload.tenant_id,
                session.principal.principal_id,
                session.session_id,
                lambda: backend.switch_tenant(session, payload.tenant_id),
            )
        except BrowserSessionBackendUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "session_audit_unavailable"},
            ) from None
        except Exception:
            backend.revoke(session.session_id)
            _clear_cookie(response)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "tenant_switch_denied"},
            ) from None
        _set_cookie(response, replacement)
        response.headers["Cache-Control"] = "no-store"
        return _view(replacement)

    @router.delete("/v1/session", status_code=204, operation_id="session_revoke")
    async def revoke(request: Request, response: Response) -> None:
        session = require_browser_session(request)
        require_csrf(request, session)
        try:
            execute_audited(
                request,
                "revoke",
                session.active_tenant_id,
                session.principal.principal_id,
                session.session_id,
                lambda: backend.revoke(session.session_id),
            )
        except BrowserSessionBackendUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "session_audit_unavailable"},
            ) from None
        _clear_cookie(response)

    return router
