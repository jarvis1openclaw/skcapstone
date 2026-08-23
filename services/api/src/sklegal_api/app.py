"""Fail-closed FastAPI composition for the internal public-synthetic MVP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sklegal_capauth import CapabilityAuthorizer
from sklegal_policies import PolicyGovernanceService

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
    async def correlation_boundary(
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
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": {"code": "application_unavailable"}},
            )
        response.headers["X-Correlation-ID"] = str(correlation_id)
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

    app.include_router(
        build_workspace_router(
            store=composition.workspace_store,
            authorizer=composition.authorizer,
            principal_resolver=composition.principal_resolver,
            scope_resolver=composition.scope_resolver,
        )
    )
    app.include_router(
        build_claim_ledger_router(
            store=composition.claim_store,
            authorizer=composition.authorizer,
            principal_resolver=composition.principal_resolver,
            scope_resolver=composition.scope_resolver,
        )
    )
    app.include_router(
        build_corpus_research_router(
            store=composition.corpus_store,
            authorizer=composition.authorizer,
            principal_resolver=composition.principal_resolver,
            scope_resolver=composition.scope_resolver,
        )
    )
    app.include_router(
        build_governance_router(
            service=composition.governance_service,
            authorizer=composition.authorizer,
            principal_resolver=composition.principal_resolver,
            scope_resolver=composition.scope_resolver,
        )
    )
    return app
