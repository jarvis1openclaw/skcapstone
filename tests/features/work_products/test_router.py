from __future__ import annotations

from fastapi import FastAPI, HTTPException
from sklegal_api.features.work_products.router import (
    _path_uuid,
    _run,
    build_work_products_router,
)
from sklegal_api.features.work_products.service import WorkProductServiceError
from starlette.requests import Request

from .helpers import T0, make_service


def app() -> FastAPI:
    service, _, _, _ = make_service()
    application = FastAPI()
    application.include_router(
        build_work_products_router(
            service=service,
            authorizer=object(),  # type: ignore[arg-type]
            principal_resolver=lambda request: None,  # type: ignore[arg-type]
            scope_resolver=lambda request: None,  # type: ignore[arg-type]
            clock=lambda: T0,
        )
    )
    return application


def test_router_exposes_only_owned_work_product_operations() -> None:
    schema = app().openapi()
    paths = schema["paths"]
    assert len(paths) == 11
    operations = {
        operation["operationId"]
        for path in paths.values()
        for operation in path.values()
    }
    assert operations == {
        "work_products_create",
        "work_products_list",
        "work_products_get",
        "work_products_create_version",
        "work_products_ground_sentence",
        "work_products_compare",
        "work_products_validate",
        "approvals_request",
        "approvals_decide",
        "approvals_revoke",
        "approvals_supersede",
        "approvals_validity",
    }


def test_every_mutation_requires_idempotency_key() -> None:
    schema = app().openapi()
    for path in schema["paths"].values():
        post = path.get("post")
        if post is None:
            continue
        headers = {
            item["name"]
            for item in post.get("parameters", [])
            if item.get("in") == "header"
        }
        assert "Idempotency-Key" in headers


def test_router_never_exposes_tenant_or_principal_in_command_bodies() -> None:
    schemas = app().openapi()["components"]["schemas"]
    command_names = [name for name in schemas if name.endswith("Body")]
    assert command_names
    for name in command_names:
        properties = schemas[name].get("properties", {})
        assert "tenantId" not in properties
        assert "matterId" not in properties
        assert "principalId" not in properties
        assert "capability" not in properties


def test_service_error_mapping_is_sanitized() -> None:
    try:
        _run(
            lambda: (_ for _ in ()).throw(
                WorkProductServiceError("approval_not_current", status_code=409)
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == {"code": "approval_not_current"}
    else:
        raise AssertionError("HTTP error was not raised")


def test_invalid_path_identifier_maps_to_not_found() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "path_params": {"work_product_id": "not-a-uuid"},
        }
    )
    try:
        _path_uuid(request, "work_product_id")
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == {"code": "work_product_not_found"}
    else:
        raise AssertionError("invalid identifier was accepted")
