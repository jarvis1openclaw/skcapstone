"""Executable checks for the canonical joined V2 MVP contract freeze."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    FormatChecker,
    ValidationError,
)
from sklegal_capauth import Capability, Purpose  # type: ignore[import-untyped]

from tests.support.v2_contract_manifest import (
    REPO_ROOT,
    V2_CONTRACT_MANIFEST_PATH,
    canonical_surface_ids,
    legacy_fixture_surface_ids,
    load_v2_contract_manifest,
)

CONTRACT_ROOT = REPO_ROOT / "docs/contracts/v2-mvp"
MANIFEST_SCHEMA_PATH = CONTRACT_ROOT / "v2-surface-manifest.v1.schema.json"
APPROVAL_SCHEMA_PATH = CONTRACT_ROOT / "v2-approval-validity.v1.schema.json"
CLAIM_SCHEMA_PATH = CONTRACT_ROOT / "v2-claim-projection.v1.schema.json"
AGENT_RUN_SCHEMA_PATH = CONTRACT_ROOT / "v2-agent-run-evidence.v1.schema.json"
ACCEPTANCE_MATRIX_PATH = (
    REPO_ROOT / "tests/fixtures/mvp/public-synthetic-mvp-v2-acceptance.json"
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest_invariants(manifest: dict[str, Any]) -> None:
    surfaces = manifest["surfaces"]
    operations = manifest["operations"]
    lanes = manifest["lanes"]
    closed_errors = set(manifest["closed_error_codes"])
    section_ids = [row["section_id"] for row in surfaces]
    operation_ids = [row["operation_id"] for row in operations]
    lane_ids = [row["lane_id"] for row in lanes]

    if [row["order"] for row in surfaces] != list(range(1, 19)):
        raise ValueError("surface order is not the exact 1 through 18 sequence")
    if len(section_ids) != len(set(section_ids)):
        raise ValueError("surface identifiers are not unique")
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("operation identifiers are not unique")
    if len(lane_ids) != len(set(lane_ids)) or len(lane_ids) != 7:
        raise ValueError("lane identifiers are not the exact unique seven")

    known_operations = set(operation_ids)
    known_lanes = set(lane_ids)
    for surface in surfaces:
        if surface["lane_id"] is not None and surface["lane_id"] not in known_lanes:
            raise ValueError("surface references an unknown lane")
        if not set(surface["operation_refs"]).issubset(known_operations):
            raise ValueError("surface references an unknown operation")

    capability_values = {item.value for item in Capability}
    purpose_values = {item.value for item in Purpose}
    for operation in operations:
        if operation["lane_id"] not in known_lanes:
            raise ValueError("operation references an unknown lane")
        if operation["capability"] not in capability_values:
            raise ValueError("operation capability is not canonical")
        if operation["purpose"] not in purpose_values:
            raise ValueError("operation purpose is not canonical")
        if operation["scope"] != "tenant_matter":
            raise ValueError("operation is not Tenant and Matter scoped")
        if not operation["resource_scope"]:
            raise ValueError("operation has no resource scope")
        if not operation["success_audit_event"]:
            raise ValueError("operation has no success audit event")
        if len(operation["provenance_required"]) < 3:
            raise ValueError("operation provenance contract is incomplete")
        if not set(operation["errors"]).issubset(closed_errors):
            raise ValueError("operation uses an open error code")
        if operation["method"] != "GET" and operation["operation_id"] != "search_corpus":
            if operation["idempotency"] != "required":
                raise ValueError("mutation does not require idempotency")
            if operation["mutation_authority"] in {"none", "model"}:
                raise ValueError("mutation authority is missing or delegated to a model")
            required = {"idempotency_key_required", "state_audit_outbox_atomic"}
            if not required.issubset(manifest["mutation_invariants"]):
                raise ValueError("atomic idempotent mutation contract is incomplete")


def test_manifest_schema_and_all_cross_references_are_closed() -> None:
    manifest = load_v2_contract_manifest()
    schema = load_json(MANIFEST_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    validate_manifest_invariants(manifest)
    assert manifest["base_commit"] == "8177c88c5fd371a487e2c206c53b24e3619c1855"
    assert manifest["base_tree"] == "a23e08dc6edfde8397298b3d1ee0b354434c3bcd"
    envelopes = manifest["operation_envelope_contract"]
    assert {"tenant_id", "matter_id", "resource_id", "capability", "purpose"}.issubset(
        envelopes["request_context_required"]
    )
    assert {"idempotency_key", "request_sha256", "expected_resource_version"}.issubset(
        envelopes["mutation_request_additional_required"]
    )
    assert {"tenant_id", "matter_id", "resource_id", "provenance"}.issubset(
        envelopes["response_context_required"]
    )
    assert {"audit_id", "outbox_id", "resource_version"}.issubset(
        envelopes["mutation_response_additional_required"]
    )


def test_canonical_manifest_matches_the_exact_18_react_surfaces() -> None:
    source = (REPO_ROOT / "apps/web/src/pages/MatterCockpit.tsx").read_text(
        encoding="utf-8"
    )
    block = source.split("export const v2CockpitSections = [", 1)[1].split(
        "] as const;", 1
    )[0]
    react_ids = tuple(re.findall(r'\{ id: "([a-z0-9-]+)"', block))
    assert react_ids == canonical_surface_ids()
    assert len(react_ids) == 18
    assert len(set(react_ids)) == 18


def test_legacy_fixture_is_reconciled_without_merging_canonical_surfaces() -> None:
    matrix = load_json(ACCEPTANCE_MATRIX_PATH)
    fixture_ids = tuple(row["sectionId"] for row in matrix["sections"])
    assert fixture_ids == legacy_fixture_surface_ids()
    assert len(fixture_ids) == 17
    manifest = load_v2_contract_manifest()
    agent_team = next(
        row for row in manifest["surfaces"] if row["section_id"] == "agent-team"
    )
    challenge = next(
        row for row in manifest["surfaces"] if row["section_id"] == "blind-challenge"
    )
    assert agent_team["legacy_fixture_section_id"] is None
    assert challenge["legacy_fixture_section_id"] == "challenge"


def test_current_openapi_bindings_name_real_fastapi_operations() -> None:
    manifest = load_v2_contract_manifest()
    operation_by_id = {row["operation_id"]: row for row in manifest["operations"]}
    api_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            REPO_ROOT / "services/api/src/sklegal_api/workspace.py",
            REPO_ROOT / "services/api/src/sklegal_api/claims.py",
            REPO_ROOT / "services/api/src/sklegal_api/corpus.py",
        )
    )
    for binding in manifest["current_openapi_bindings"]:
        operation = operation_by_id[binding["operation_id"]]
        assert operation["path"] in api_source
        assert binding["fastapi_operation_id"] in api_source


def test_claim_identity_has_one_lifecycle_owner_and_one_to_one_projection() -> None:
    manifest = load_v2_contract_manifest()
    identity = manifest["claim_identity"]
    assert identity == {
        "canonical_entity": "Claim",
        "canonical_id_field": "Claim.id",
        "canonical_lifecycle_owner": "Claim.status",
        "ledger_role": "one_to_one_append_only_evidence_review_projection",
        "ledger_identity_rule": "LedgerClaim.claim_id equals Claim.id",
        "required_invariants": [
            "same_tenant",
            "same_matter",
            "same_claim_id",
            "projected_claim_version_pinned",
            "no_orphan_ledger",
            "no_competing_lifecycle",
            "no_silent_statement_reconciliation",
        ],
    }
    schema = load_json(CLAIM_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    required = set(schema["required"])
    assert {"tenant_id", "matter_id", "claim_id", "claim_version", "ledger_projection"}.issubset(required)
    assert "claim_status" in required
    assert "status" not in schema["properties"]["ledger_projection"]["properties"]


def test_operative_approval_requires_complete_validity_not_hash_only() -> None:
    schema = load_json(APPROVAL_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    required = set(schema["required"])
    assert {
        "status",
        "subject",
        "reviewer_principal_id",
        "decided_at",
        "rationale",
        "validation_id",
        "validation_status",
        "policy_revision",
        "capability_decision_id",
        "revoked_at",
        "operative",
        "inoperative_reasons",
    }.issubset(required)
    operative_requirements = set(
        load_v2_contract_manifest()["approval_validity"]["operative_all_required"]
    )
    assert {"status_approved", "validation_passed", "not_revoked", "not_superseded", "capability_decision_allowed"}.issubset(operative_requirements)
    valid: dict[str, Any] = {
        "approval_id": "10000000-0000-4000-8000-000000000001",
        "tenant_id": "10000000-0000-4000-8000-000000000002",
        "matter_id": "10000000-0000-4000-8000-000000000003",
        "status": "approved",
        "subject": {
            "work_product_id": "10000000-0000-4000-8000-000000000004",
            "version_id": "10000000-0000-4000-8000-000000000005",
            "version_number": 2,
            "content_sha256": "a" * 64,
        },
        "reviewer_principal_id": "10000000-0000-4000-8000-000000000006",
        "decided_at": "2099-01-01T00:00:00Z",
        "rationale": "Exact version passed review.",
        "validation_id": "10000000-0000-4000-8000-000000000007",
        "validation_status": "passed",
        "policy_revision": "b" * 64,
        "capability_decision_id": "10000000-0000-4000-8000-000000000008",
        "capability": "work_product.approve",
        "purpose": "human_approval",
        "revoker_principal_id": None,
        "revoked_at": None,
        "revocation_rationale": None,
        "operative": True,
        "inoperative_reasons": [],
    }
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(valid)
    hash_only = copy.deepcopy(valid)
    hash_only["reviewer_principal_id"] = None
    with pytest.raises(ValidationError, match="None is not of type 'string'"):
        validator.validate(hash_only)


def test_agent_run_schema_freezes_model_attribution_and_policy_evidence() -> None:
    schema = load_json(AGENT_RUN_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    required = set(schema["required"])
    assert {
        "matter_snapshot_sha256",
        "logical_route_id",
        "transport_profile_revision",
        "gateway_revision",
        "backend",
        "requested_model_or_bucket",
        "served_model",
        "served_model_revision",
        "prompt_sha256",
        "schema_sha256",
        "request_sha256",
        "response_sha256",
        "tool_calls",
        "retrieval_traces",
        "policy_decision_id",
        "capability_decision_id",
        "source_rights_decision_id",
        "egress_decision_id",
        "retry_count",
        "failover_count",
    }.issubset(required)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["surfaces"].append(copy.deepcopy(value["surfaces"][0])), "surface order"),
        (lambda value: value["operations"][0].update(scope="tenant"), "Tenant and Matter"),
        (lambda value: value["operations"][0]["errors"].append("raw_database_error"), "open error"),
        (lambda value: value["operations"][3].update(idempotency="not_applicable"), "idempotency"),
        (lambda value: value["operations"][3].update(mutation_authority="model"), "delegated to a model"),
        (lambda value: value["surfaces"][0]["operation_refs"].append("invented"), "unknown operation"),
    ],
)
def test_negative_controls_reject_split_or_unsafe_contracts(mutation: Any, message: str) -> None:
    manifest = load_v2_contract_manifest()
    mutation(manifest)
    with pytest.raises(ValueError, match=message):
        validate_manifest_invariants(manifest)


def test_contract_artifacts_are_ascii_dash_clean_and_secret_free() -> None:
    forbidden = ("\u2013", "\u2014", "authorization: bearer", "vault:", "openrouter_api_key")
    for path in sorted(CONTRACT_ROOT.glob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert not any(marker in text for marker in forbidden), path
    manifest_bytes = V2_CONTRACT_MANIFEST_PATH.read_bytes()
    assert manifest_bytes.endswith(b"\n")
