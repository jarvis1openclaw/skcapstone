from __future__ import annotations

from typing import cast

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sklegal_api.capauth import ProtectedRouteDependency
from sklegal_api.mvp_integration import (
    MvpRouteCompositionError,
    canonical_operation_ids,
    compose_v2_feature_router,
)
from sklegal_capauth import (
    ApiCapabilityBoundary,
    AuthorizedContext,
    BoundaryScope,
    Capability,
    CapabilityAuthorizer,
    PrincipalContext,
    PrincipalType,
    Purpose,
)


def _source_router(operation_id: str, method: str = "GET") -> APIRouter:
    router = APIRouter()

    async def endpoint(request: Request) -> dict[str, str]:
        return {"path": request.url.path}

    router.add_api_route(
        f"/source/{operation_id}",
        endpoint,
        methods=[method],
        operation_id=operation_id,
    )
    return router


def _all_source_routers() -> tuple[APIRouter, ...]:
    from sklegal_api.mvp_integration import _SOURCE_BINDINGS

    methods = {
        "create_analysis_run": "POST",
        "create_challenge": "POST",
        "decide_recommendation": "POST",
        "create_artifact_intake": "POST",
        "get_artifact": "GET",
        "create_work_product_version": "POST",
        "validate_work_product": "POST",
        "decide_approval": "POST",
        "upsert_task": "POST",
        "compute_deadline": "POST",
        "create_simulation_handoff": "POST",
        "create_activity_export": "POST",
        "search_corpus": "POST",
    }
    return tuple(
        _source_router(source_id, methods.get(operation_id, "GET"))
        for operation_id, (source_id, _path, _aliases) in _SOURCE_BINDINGS.items()
    )


def test_v2_adapter_exposes_the_exact_reviewed_operation_inventory() -> None:
    router = compose_v2_feature_router(
        _all_source_routers(),
        existing_operation_ids={"get_workspace", "get_claim_ledger"},
    )
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert tuple(route.operation_id for route in routes) == tuple(
        item
        for item in canonical_operation_ids()
        if item not in {"get_workspace", "get_claim_ledger"}
    )
    assert len(routes) == 19
    assert len({(next(iter(route.methods)), route.path) for route in routes}) == 19


def test_v2_adapter_rejects_duplicate_source_operations() -> None:
    routers = _all_source_routers()
    with pytest.raises(MvpRouteCompositionError, match="duplicate source operation"):
        compose_v2_feature_router((*routers, routers[0]))


def test_v2_adapter_rejects_method_drift() -> None:
    from sklegal_api.mvp_integration import _SOURCE_BINDINGS

    routers = list(_all_source_routers())
    target = next(
        index
        for index, (operation_id, _value) in enumerate(_SOURCE_BINDINGS.items())
        if operation_id == "create_analysis_run"
    )
    routers[target] = _source_router("agent_runs_start", "GET")
    with pytest.raises(MvpRouteCompositionError, match="V2 method drift"):
        compose_v2_feature_router(
            tuple(routers),
            existing_operation_ids={"get_workspace", "get_claim_ledger"},
        )


def test_v2_adapter_maps_canonical_agent_run_path_parameter() -> None:
    router = APIRouter()

    async def endpoint(request: Request) -> dict[str, str]:
        return {"run_id": request.path_params["run_id"]}

    router.add_api_route(
        "/source/agent-runs/{run_id}",
        endpoint,
        methods=["GET"],
        operation_id="agent_runs_get",
    )
    app = FastAPI()
    app.include_router(
        compose_v2_feature_router(
            (router,),
            existing_operation_ids=set(canonical_operation_ids()) - {"get_agent_run"},
        )
    )
    with TestClient(app) as client:
        response = client.get(
            "/v1/matters/11111111-1111-4111-8111-111111111111/agent-runs/"
            "22222222-2222-4222-8222-222222222222"
        )
    assert response.status_code == 200
    assert response.json()["run_id"].endswith("2222")


def test_v2_adapter_rebinds_protected_route_to_reviewed_contract() -> None:
    legacy = ApiCapabilityBoundary(
        authorizer=cast(CapabilityAuthorizer, object()),
        route_name="agent_runs.get",
        capability=Capability.MATTER_READ,
        purpose=Purpose.MATTER_MANAGEMENT,
    )

    async def principal(_request: Request) -> PrincipalContext:
        return PrincipalContext(
            tenant_id="11111111-1111-4111-8111-111111111111",
            principal_id="22222222-2222-4222-8222-222222222222",
            principal_type=PrincipalType.HUMAN,
        )

    async def scope(_request: Request) -> BoundaryScope:
        return BoundaryScope(tenant_id="11111111-1111-4111-8111-111111111111")

    dependency = ProtectedRouteDependency(
        boundary=legacy,
        principal_resolver=principal,
        scope_resolver=scope,
    )
    router = APIRouter()

    async def endpoint(
        request: Request,
        _authorized: AuthorizedContext = Depends(dependency),
    ) -> dict[str, str]:
        return {"path": request.url.path}

    router.get("/source/{matter_id}/{run_id}", operation_id="agent_runs_get")(endpoint)
    composed = compose_v2_feature_router(
        (router,),
        existing_operation_ids=set(canonical_operation_ids()) - {"get_agent_run"},
    )
    route = cast(APIRoute, composed.routes[0])
    rebound = cast(ProtectedRouteDependency, route.dependant.dependencies[0].call)
    requirement = rebound._boundary.requirement
    assert requirement.target == "api:get_agent_run"
    assert requirement.capability is Capability.CLAIM_REVIEW
    assert requirement.purpose is Purpose.CLAIM_REVIEW
