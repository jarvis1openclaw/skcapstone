"""Fail-closed FastAPI composition for the internal public-synthetic MVP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sklegal_capauth import CapabilityAuthorizer
from sklegal_policies import PolicyGovernanceService

from .browser_sessions import (
    SESSION_COOKIE,
    BrowserSessionAuditSink,
    BrowserSessionBackend,
    BrowserSessionBackendUnavailable,
    build_browser_session_router,
    require_csrf,
)
from .capauth import PrincipalResolver, ScopeResolver
from .claims import (
    ClaimLedgerStore,
    InMemoryClaimLedgerStore,
    build_claim_ledger_router,
)
from .corpus import (
    CorpusResearchStore,
    InMemoryCorpusResearchStore,
    build_corpus_research_router,
)
from .governance import build_governance_router
from .mvp_integration import compose_v2_feature_router, rename_operation_ids
from .workspace import (
    InMemoryWorkspaceReadStore,
    WorkspaceReadStore,
    build_workspace_router,
)

RuntimeMode = Literal["production", "development"]
ReadinessCheck = Callable[[], bool]

REQUIRED_DEPENDENCIES = frozenset(
    {"authentication", "policy", "audit", "persistence", "provenance"}
)
HEALTH_BUDGET_MS = 100


class MvpCompositionUnavailable(RuntimeError):
    """The application cannot prove that every required boundary is ready."""


@dataclass(frozen=True, slots=True)
class DependencyProbe:
    """One bounded readiness probe supplied by the owning adapter."""

    name: str
    check: ReadinessCheck
    synthetic: bool = False

    def is_ready(self) -> bool:
        try:
            return self.check() is True
        except Exception:
            return False


@dataclass(frozen=True, slots=True)
class MvpApiComposition:
    """Explicit dependency set for the reviewed MVP router surface."""

    workspace_store: WorkspaceReadStore
    claim_store: ClaimLedgerStore
    corpus_store: CorpusResearchStore
    governance_service: PolicyGovernanceService
    authorizer: CapabilityAuthorizer
    principal_resolver: PrincipalResolver
    scope_resolver: ScopeResolver
    probes: tuple[DependencyProbe, ...]
    mode: RuntimeMode = "production"
    browser_sessions: BrowserSessionBackend | None = None
    browser_session_audit: BrowserSessionAuditSink | None = None
    feature_routers: tuple[APIRouter, ...] = ()


def _probe_map(composition: MvpApiComposition) -> dict[str, DependencyProbe]:
    if composition.mode not in {"production", "development"}:
        raise MvpCompositionUnavailable("runtime mode is invalid")
    probes: dict[str, DependencyProbe] = {}
    for probe in composition.probes:
        if not probe.name or probe.name in probes or not callable(probe.check):
            raise MvpCompositionUnavailable("dependency probe contract is invalid")
        probes[probe.name] = probe
    if set(probes) != REQUIRED_DEPENDENCIES:
        raise MvpCompositionUnavailable("required dependency probes are incomplete")
    if composition.mode == "production" and any(
        probe.synthetic for probe in probes.values()
    ):
        raise MvpCompositionUnavailable(
            "synthetic dependencies are forbidden in production mode"
        )
    if composition.mode == "production" and isinstance(
        composition.workspace_store, InMemoryWorkspaceReadStore
    ):
        raise MvpCompositionUnavailable(
            "in-memory workspace storage is forbidden in production mode"
        )
    if composition.mode == "production" and isinstance(
        composition.claim_store, InMemoryClaimLedgerStore
    ):
        raise MvpCompositionUnavailable(
            "in-memory claim storage is forbidden in production mode"
        )
    if composition.mode == "production" and isinstance(
        composition.corpus_store, InMemoryCorpusResearchStore
    ):
        raise MvpCompositionUnavailable(
            "in-memory corpus storage is forbidden in production mode"
        )
    if composition.mode == "production" and getattr(
        composition.browser_sessions, "synthetic", False
    ):
        raise MvpCompositionUnavailable(
            "synthetic browser sessions are forbidden in production mode"
        )
    if (composition.browser_sessions is None) != (
        composition.browser_session_audit is None
    ):
        raise MvpCompositionUnavailable(
            "browser sessions and their audit boundary must be composed together"
        )
    if composition.mode == "production" and getattr(
        composition.browser_session_audit, "synthetic", False
    ):
        raise MvpCompositionUnavailable(
            "synthetic browser session audit is forbidden in production mode"
        )
    return probes


def _require_ready(probes: dict[str, DependencyProbe]) -> None:
    if any(not probes[name].is_ready() for name in sorted(REQUIRED_DEPENDENCIES)):
        raise MvpCompositionUnavailable("a required dependency is unavailable")


def _correlation_id(request: Request) -> UUID:
    raw = request.headers.get("X-Correlation-ID", "").strip()
    if not raw:
        return uuid4()
    try:
        return UUID(raw)
    except ValueError:
        raise ValueError("correlation identifier is invalid") from None


def create_mvp_app(composition: MvpApiComposition) -> FastAPI:
    """Create the only reviewed internal MVP application composition."""

    probes = _probe_map(composition)
    _require_ready(probes)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _require_ready(probes)
        yield

    app = FastAPI(
        title="SKLegal internal MVP API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.mvp_composition = composition

    @app.middleware("http")
    async def browser_security_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            correlation_id = _correlation_id(request)
        except ValueError:
            correlation_id = uuid4()
            invalid_response = JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": {"code": "correlation_id_invalid"}},
            )
            invalid_response.headers["X-Correlation-ID"] = str(correlation_id)
            return invalid_response
        request.state.correlation_id = correlation_id

        origin = request.headers.get("Origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin is not None and origin != expected_origin:
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": {"code": "origin_denied"}},
            )
        else:
            backend = composition.browser_sessions
            session_id = request.cookies.get(SESSION_COOKIE)
            if (
                backend is not None
                and session_id
                and request.url.path != "/v1/session/bootstrap"
            ):
                try:
                    session = backend.resolve(session_id)
                except BrowserSessionBackendUnavailable:
                    session = None
                    response = JSONResponse(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        content={"detail": {"code": "session_backend_unavailable"}},
                    )
                else:
                    response = None
                if session is None and response is None:
                    response = JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content={"detail": {"code": "session_invalid"}},
                    )
                if session is not None:
                    request.state.browser_session = session
                    tenant_header = request.headers.get("X-SKLegal-Tenant")
                    if tenant_header and tenant_header != str(session.active_tenant_id):
                        response = JSONResponse(
                            status_code=status.HTTP_403_FORBIDDEN,
                            content={"detail": {"code": "tenant_context_denied"}},
                        )
                    elif (
                        request.method not in {"GET", "HEAD", "OPTIONS"}
                        and request.url.path != "/v1/session/bootstrap"
                    ):
                        try:
                            require_csrf(request, session)
                        except Exception:
                            response = JSONResponse(
                                status_code=status.HTTP_403_FORBIDDEN,
                                content={"detail": {"code": "csrf_denied"}},
                            )
            elif backend is not None and request.url.path not in {
                "/healthz",
                "/v1/session/bootstrap",
            }:
                response = JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": {"code": "authentication_required"}},
                )
            else:
                response = None

            if response is None:
                try:
                    response = await call_next(request)
                except Exception:
                    response = JSONResponse(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        content={"detail": {"code": "application_unavailable"}},
                    )

        response.headers["X-Correlation-ID"] = str(correlation_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/healthz", operation_id="mvp_health", include_in_schema=True)
    async def health() -> JSONResponse:
        started = monotonic()
        states: dict[str, str] = {}
        for name in sorted(REQUIRED_DEPENDENCIES):
            remaining_seconds = HEALTH_BUDGET_MS / 1000 - (monotonic() - started)
            if remaining_seconds <= 0:
                states[name] = "unavailable"
                continue
            try:
                ready = await asyncio.wait_for(
                    asyncio.to_thread(probes[name].is_ready),
                    timeout=remaining_seconds,
                )
            except TimeoutError:
                ready = False
            states[name] = "ready" if ready else "unavailable"
        elapsed_ms = min(int((monotonic() - started) * 1000), HEALTH_BUDGET_MS)
        ready = all(value == "ready" for value in states.values())
        return JSONResponse(
            status_code=(
                status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            content={
                "status": "ready" if ready else "unavailable",
                "components": states,
                "budgetMs": HEALTH_BUDGET_MS,
                "elapsedMs": elapsed_ms,
            },
        )

    if composition.browser_sessions is not None:
        assert composition.browser_session_audit is not None
        app.include_router(
            build_browser_session_router(
                composition.browser_sessions, composition.browser_session_audit
            )
        )

    workspace_router = build_workspace_router(
        store=composition.workspace_store,
        authorizer=composition.authorizer,
        principal_resolver=composition.principal_resolver,
        scope_resolver=composition.scope_resolver,
    )
    claims_router = build_claim_ledger_router(
        store=composition.claim_store,
        authorizer=composition.authorizer,
        principal_resolver=composition.principal_resolver,
        scope_resolver=composition.scope_resolver,
    )
    if composition.feature_routers:
        app.include_router(
            rename_operation_ids(
                workspace_router,
                {"workspace_matters_workspace": "get_workspace"},
            )
        )
        app.include_router(
            rename_operation_ids(claims_router, {"claims_ledger": "get_claim_ledger"})
        )
    else:
        app.include_router(workspace_router)
        app.include_router(claims_router)

    corpus_router = build_corpus_research_router(
        store=composition.corpus_store,
        authorizer=composition.authorizer,
        principal_resolver=composition.principal_resolver,
        scope_resolver=composition.scope_resolver,
    )
    if not composition.feature_routers:
        app.include_router(corpus_router)
    app.include_router(
        build_governance_router(
            service=composition.governance_service,
            authorizer=composition.authorizer,
            principal_resolver=composition.principal_resolver,
            scope_resolver=composition.scope_resolver,
        )
    )
    if composition.feature_routers:
        app.include_router(
            compose_v2_feature_router(
                composition.feature_routers,
                existing_operation_ids={"get_workspace", "get_claim_ledger"},
            )
        )
    return app
