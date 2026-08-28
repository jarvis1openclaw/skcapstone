"""Focused regression for exact frozen V2 browser capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from sklegal_api.v2_request_capabilities import V2_REQUEST_CAPABILITIES
from sklegal_capauth import CAPABILITY_RULES, Audience, Capability, CapabilityGrant

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/contracts/v2-mvp/v2-surface-manifest.v1.json"
TENANT = "11111111-1111-4111-8111-111111111111"
MATTER = "44444444-4444-4444-8444-444444444441"
RESOURCE = "55555555-5555-4555-8555-555555555555"


def test_every_frozen_operation_has_one_exact_reviewed_request_contract() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["operations"]
    reviewed = V2_REQUEST_CAPABILITIES.reviewed
    assert len(reviewed) == 21
    assert {
        (item.operation_id, item.method, item.path_template) for item in reviewed
    } == {(item["operation_id"], item["method"], item["path"]) for item in manifest}
    for item in reviewed:
        rule = CAPABILITY_RULES[item.capability]
        assert item.target == f"api:{item.operation_id}"
        assert "*" not in item.target and "*" not in item.path_template
        assert item.purpose in rule.purposes
        assert item.resource_parameter in item.path_template
        contract = next(
            contract
            for contract in manifest
            if contract["operation_id"] == item.operation_id
        )
        assert item.capability.value == contract["capability"]
        assert item.purpose.value == contract["purpose"]


def test_dynamic_resources_resolve_to_one_concrete_request_scope() -> None:
    resolved = V2_REQUEST_CAPABILITIES.resolve(
        "GET", f"/v1/matters/{MATTER}/artifacts/{RESOURCE}"
    )
    assert resolved is not None
    assert resolved.reviewed.operation_id == "get_artifact"
    assert resolved.matter_id == UUID(MATTER)
    assert resolved.resource_id == RESOURCE

    source = V2_REQUEST_CAPABILITIES.resolve(
        "GET",
        f"/v1/matters/{MATTER}/corpus/sources/public-synthetic-source/span",
    )
    assert source is not None
    assert source.resource_id == "public-synthetic-source"


def test_every_reviewed_request_forms_one_closed_matter_grant() -> None:
    for reviewed in V2_REQUEST_CAPABILITIES.reviewed:
        path = reviewed.path_template.replace("{matter_id}", MATTER)
        for parameter in reviewed.uuid_parameters:
            path = path.replace(f"{{{parameter}}}", RESOURCE)
        path = path.replace("{source_id}", "public-synthetic-source")
        resolved = V2_REQUEST_CAPABILITIES.resolve(reviewed.method, path)
        assert resolved is not None
        if reviewed.capability is Capability.WORK_PRODUCT_APPROVE:
            resolved = resolved.bind_exact_version(1, "a" * 64)
        rule = CAPABILITY_RULES[reviewed.capability]
        grant = CapabilityGrant(
            audience=Audience.API,
            target=reviewed.target,
            capability=reviewed.capability,
            tenant_id=UUID(TENANT),
            matter_id=resolved.matter_id,
            resource_type=rule.resource_type,
            resource_id=resolved.resource_id,
            resource_version=resolved.resource_version,
            resource_sha256=resolved.resource_sha256,
            operation=rule.operation,
            purpose=reviewed.purpose,
        )
        assert grant.matter_id == UUID(MATTER)
        assert grant.resource_id == resolved.resource_id


def test_approval_requires_an_exact_trusted_version_binding() -> None:
    resolved = V2_REQUEST_CAPABILITIES.resolve(
        "POST",
        f"/v1/matters/{MATTER}/work-products/{RESOURCE}/versions/{RESOURCE}/approval-decisions",
    )
    assert resolved is not None
    assert resolved.reviewed.operation_id == "decide_approval"
    assert resolved.resource_version is None
    assert resolved.resource_sha256 is None

    bound = resolved.bind_exact_version(7, "a" * 64)
    assert bound.resource_id == RESOURCE
    assert bound.resource_version == 7
    assert bound.resource_sha256 == "a" * 64


def test_unknown_or_broadened_requests_fail_closed() -> None:
    denied = (
        ("DELETE", f"/v1/matters/{MATTER}/artifacts/{RESOURCE}"),
        ("GET", f"/v1/matters/{MATTER}/artifacts"),
        ("GET", f"/v1/matters/{MATTER}/artifacts/{RESOURCE}/extra"),
        ("GET", "/v1/matters/not-a-uuid/analysis"),
        ("GET", f"/v1/matters/{MATTER}/corpus/sources/a/b/span"),
        ("GET", "/v1/matters"),
    )
    assert all(V2_REQUEST_CAPABILITIES.resolve(*request) is None for request in denied)
