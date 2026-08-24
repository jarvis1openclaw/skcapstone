"""Central V2 route assembly for the reviewed feature lanes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import wraps
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute

MANIFEST_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs/contracts/v2-mvp/v2-surface-manifest.v1.json"
)


class MvpRouteCompositionError(RuntimeError):
    """The reviewed V2 route inventory cannot be composed safely."""


def _manifest_operations() -> tuple[dict[str, str], ...]:
    import json

    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    operations = value.get("operations")
    if not isinstance(operations, list):
        raise MvpRouteCompositionError("V2 operation manifest is invalid")
    result: list[dict[str, str]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise MvpRouteCompositionError("V2 operation manifest is invalid")
        operation_id = operation.get("operation_id")
        method = operation.get("method")
        path = operation.get("path")
        if not all(isinstance(item, str) and item for item in (operation_id, method, path)):
            raise MvpRouteCompositionError("V2 operation manifest is invalid")
        result.append({"operation_id": operation_id, "method": method, "path": path})
    return tuple(result)


_SOURCE_BINDINGS: Mapping[str, tuple[str, str, dict[str, str]]] = {
    "get_joined_analysis": ("get_joined_analysis", "same", {}),
    "create_analysis_run": ("agent_runs_start", "same", {}),
    "get_agent_run": (
        "agent_runs_get",
        "/v1/matters/{matter_id}/agent-runs/{agent_run_id}",
        {"run_id": "agent_run_id"},
    ),
    "create_challenge": (
        "agent_runs_challenge",
        "/v1/matters/{matter_id}/claims/{claim_id}/challenges",
        {"run_id": "claim_id"},
    ),
    "list_recommendations": (
        "agent_runs_list",
        "/v1/matters/{matter_id}/recommendations",
        {},
    ),
    "decide_recommendation": (
        "agent_runs_dispose",
        "/v1/matters/{matter_id}/recommendations/{recommendation_id}/decisions",
        {"run_id": "recommendation_id"},
    ),
    "create_artifact_intake": ("artifact_intake_create", "same", {}),
    "get_artifact": ("artifact_intake_get", "same", {}),
    "get_work_product": ("work_products_get", "same", {}),
    "create_work_product_version": ("work_products_create_version", "same", {}),
    "validate_work_product": (
        "work_products_validate",
        "/v1/matters/{matter_id}/work-products/{work_product_id}/versions/{version_id}/validations",
        {},
    ),
    "decide_approval": (
        "approvals_decide",
        "/v1/matters/{matter_id}/work-products/{work_product_id}/versions/{version_id}/approval-decisions",
        {"approval_id": "version_id"},
    ),
    "upsert_task": ("upsert_task", "same", {}),
    "compute_deadline": ("compute_deadline", "same", {}),
    "create_simulation_handoff": ("create_simulation_handoff", "same", {}),
    "list_activity": ("list_activity", "same", {}),
    "create_activity_export": ("create_activity_export", "same", {}),
    "search_corpus": (
        "governed_corpus_search",
        "same",
        {},
    ),
    "get_corpus_span": ("governed_corpus_span", "same", {}),
}


def canonical_operation_ids() -> tuple[str, ...]:
    """Return the ordered operation IDs from the checked-in V2 manifest."""

    return tuple(item["operation_id"] for item in _manifest_operations())


def _route_method_path(route: APIRoute) -> tuple[str, str]:
    methods = tuple(sorted(route.methods or ()))
    if len(methods) != 1:
        raise MvpRouteCompositionError(
            f"V2 route {route.name} must expose one HTTP method"
        )
    return methods[0], route.path


def _adapt_route(
    route: APIRoute,
    *,
    operation_id: str,
    path: str,
    aliases: Mapping[str, str],
) -> APIRoute:
    endpoint = route.endpoint

    @wraps(endpoint)
    async def adapted_endpoint(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None:
            request = next((item for item in args if isinstance(item, Request)), None)
        if request is None:
            raise MvpRouteCompositionError(
                f"V2 route {operation_id} does not accept a Request"
            )
        original = request.scope.get("path_params", {})
        request.scope["path_params"] = dict(original)
        try:
            for destination, source in aliases.items():
                if source in original:
                    request.path_params[destination] = original[source]
            return await endpoint(*args, **kwargs)
        finally:
            request.scope["path_params"] = original

    return APIRoute(
        path=path,
        endpoint=adapted_endpoint,
        response_model=route.response_model,
        status_code=route.status_code,
        tags=route.tags,
        dependencies=route.dependencies,
        summary=route.summary,
        description=route.description,
        response_description=route.response_description,
        responses=route.responses,
        deprecated=route.deprecated,
        name=route.name,
        methods=route.methods,
        operation_id=operation_id,
        response_model_include=route.response_model_include,
        response_model_exclude=route.response_model_exclude,
        response_model_by_alias=route.response_model_by_alias,
        response_model_exclude_unset=route.response_model_exclude_unset,
        response_model_exclude_defaults=route.response_model_exclude_defaults,
        response_model_exclude_none=route.response_model_exclude_none,
        include_in_schema=route.include_in_schema,
        response_class=route.response_class,
        callbacks=route.callbacks,
        openapi_extra=route.openapi_extra,
        strict_content_type=route.strict_content_type,
    )


def compose_v2_feature_router(
    routers: Iterable[APIRouter],
    *,
    existing_operation_ids: Iterable[str] = (),
) -> APIRouter:
    """Select and adapt the reviewed lane routes to the frozen V2 contract."""

    available: dict[str, APIRoute] = {}
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute) or route.operation_id is None:
                continue
            if route.operation_id in available:
                raise MvpRouteCompositionError(
                    f"duplicate source operation {route.operation_id}"
                )
            available[route.operation_id] = route

    selected = APIRouter()
    seen: set[tuple[str, str]] = set()
    existing = set(existing_operation_ids)
    for contract in _manifest_operations():
        operation_id = contract["operation_id"]
        if operation_id in existing:
            continue
        try:
            source_id, target_path, aliases = _SOURCE_BINDINGS[operation_id]
        except KeyError as exc:
            raise MvpRouteCompositionError(
                f"V2 operation has no compatibility binding: {operation_id}"
            ) from exc
        try:
            source = available[source_id]
        except KeyError as exc:
            raise MvpRouteCompositionError(
                f"V2 operation is not supplied by a reviewed lane: {operation_id}"
            ) from exc
        method, _ = _route_method_path(source)
        if method != contract["method"]:
            raise MvpRouteCompositionError(
                f"V2 method drift for {operation_id}: {method} != {contract['method']}"
            )
        path = contract["path"] if target_path == "same" else target_path
        key = (method, path)
        if key in seen:
            raise MvpRouteCompositionError(f"duplicate V2 route {method} {path}")
        seen.add(key)
        selected.routes.append(
            _adapt_route(
                source,
                operation_id=operation_id,
                path=path,
                aliases=aliases,
            )
        )
    return selected


def rename_operation_ids(
    router: APIRouter, mapping: Mapping[str, str]
) -> APIRouter:
    """Rename selected base routes while preserving their request contracts."""

    renamed = APIRouter()
    for route in router.routes:
        if not isinstance(route, APIRoute) or route.operation_id not in mapping:
            renamed.routes.append(route)
            continue
        method, path = _route_method_path(route)
        del method
        renamed.routes.append(
            _adapt_route(
                route,
                operation_id=mapping[route.operation_id],
                path=path,
                aliases={},
            )
        )
    return renamed


__all__ = [
    "MvpRouteCompositionError",
    "canonical_operation_ids",
    "compose_v2_feature_router",
    "rename_operation_ids",
]
