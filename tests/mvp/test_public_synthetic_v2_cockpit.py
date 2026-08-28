"""Executable acceptance checks for the public synthetic V2 Matter cockpit."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.support.mvp_v2_cockpit import (
    EXPECTED_SECTIONS,
    MATRIX_PATH,
    V1_FIXTURE_PATH,
    V2_FIXTURE_PATH,
    V2AcceptanceError,
    load_json,
    validate_v2_acceptance,
)


@pytest.fixture
def documents() -> tuple[dict, dict, dict]:
    return (
        load_json(V1_FIXTURE_PATH),
        load_json(V2_FIXTURE_PATH),
        load_json(MATRIX_PATH),
    )


def test_exact_v2_fixture_and_executable_acceptance_matrix_pass() -> None:
    validate_v2_acceptance()


def test_every_navigation_section_has_component_fixture_state_and_accessibility(
    documents: tuple[dict, dict, dict],
) -> None:
    _, _, matrix = documents
    assert tuple(row["sectionId"] for row in matrix["sections"]) == EXPECTED_SECTIONS
    for row in matrix["sections"]:
        assert row["component"]
        assert row["fixtureRefs"]
        assert row["expectedState"] in {"complete", "safely_unavailable"}
        assert row["accessibility"]["landmark"] == "region"
        assert row["accessibility"]["accessibleName"] == row["navigationLabel"]
        assert row["accessibility"]["keyboard"] == "navigation_and_content_reachable"


def test_client_matter_session_and_api_paths_are_executable_not_visual_only(
    documents: tuple[dict, dict, dict],
) -> None:
    v1, v2, matrix = documents
    path_ids = {row["pathId"] for row in matrix["executablePaths"]}
    assert {
        "authenticated-session",
        "client-navigation",
        "matter-cockpit",
        "claim-ledger",
        "cross-matter-denial",
        "cross-tenant-denial",
        "workspace-outage",
        "claim-outage",
        "unauthenticated-browser",
    } == path_ids
    bindings = v2["executableCockpitBindings"]
    assert bindings["sessionFixtureId"] == v1["session"]["fixtureId"]
    assert bindings["clientFixtureId"] == v1["clients"][0]["fixtureId"]
    assert bindings["matterFixtureId"] == "public-synthetic-matter-primary"
    assert bindings["factAssertionId"] == v1["workspace"]["facts"][0]["factAssertionId"]
    assert (
        bindings["evidenceItemId"] == v1["workspace"]["evidence"][0]["evidenceItemId"]
    )
    assert bindings["sourceSnapshot"] == v1["workspace"]["provenance"]["sourceSnapshot"]


@pytest.mark.parametrize("operation", ("missing", "extra", "reordered"))
def test_navigation_section_drift_fails_closed(
    documents: tuple[dict, dict, dict], operation: str
) -> None:
    v1, v2, matrix = copy.deepcopy(documents)
    if operation == "missing":
        matrix["sections"].pop()
    elif operation == "extra":
        row = copy.deepcopy(matrix["sections"][-1])
        row["sectionId"] = "invented-section"
        matrix["sections"].append(row)
    else:
        matrix["sections"][0], matrix["sections"][1] = (
            matrix["sections"][1],
            matrix["sections"][0],
        )
    with pytest.raises(V2AcceptanceError):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("component", "InventedComponent"),
        ("apiContract", "GET /v1/invented"),
        ("contractState", "implemented_without_contract"),
        ("expectedState", "complete"),
    ),
)
def test_component_api_and_completion_contract_drift_fails_closed(
    documents: tuple[dict, dict, dict], field: str, value: str
) -> None:
    v1, v2, matrix = copy.deepcopy(documents)
    row = next(row for row in matrix["sections"] if row["sectionId"] == "intake")
    row[field] = value
    with pytest.raises(V2AcceptanceError):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_invented_and_unbound_fixture_records_fail_closed(
    documents: tuple[dict, dict, dict],
) -> None:
    v1, v2, matrix = copy.deepcopy(documents)
    matrix["sections"][0]["fixtureRefs"] = ["public-synthetic-invented-record"]
    with pytest.raises(V2AcceptanceError, match="invented or missing"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)

    v1, v2, matrix = copy.deepcopy(documents)
    v2["unbound"] = {"fixtureId": "public-synthetic-v2-unbound-record"}
    with pytest.raises(V2AcceptanceError, match="unbound invented"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


@pytest.mark.parametrize("scope_field", ("tenantId", "matterId"))
def test_cross_tenant_and_cross_matter_surface_leakage_fails_closed(
    documents: tuple[dict, dict, dict], scope_field: str
) -> None:
    v1, v2, matrix = copy.deepcopy(documents)
    v2["surfaceFixtures"][0][scope_field] = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(V2AcceptanceError, match="cross-Tenant or cross-Matter"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("simulationOnly", False),
        ("externalEffectOccurred", True),
        ("recommendationAcceptanceCanDispatch", True),
        ("directActionControls", ["dispatch"]),
    ),
)
def test_external_action_progression_fails_closed(
    documents: tuple[dict, dict, dict], field: str, value: object
) -> None:
    v1, v2, matrix = copy.deepcopy(documents)
    v2["externalActionBoundary"][field] = value
    with pytest.raises(V2AcceptanceError, match="external-action progression"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_unbound_source_audit_and_accessibility_states_fail_closed(
    documents: tuple[dict, dict, dict],
) -> None:
    for mutation in ("evidence", "accessibility"):
        v1, v2, matrix = copy.deepcopy(documents)
        row = matrix["sections"][0]
        if mutation == "evidence":
            row["evidenceAssertions"].remove("audit-state-explicit")
        else:
            row["accessibility"]["accessibleName"] = "Wrong label"
        with pytest.raises(V2AcceptanceError):
            validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_executable_path_and_exact_cockpit_data_drift_fail_closed(
    documents: tuple[dict, dict, dict],
) -> None:
    v1, v2, matrix = copy.deepcopy(documents)
    matrix["executablePaths"].pop()
    with pytest.raises(V2AcceptanceError, match="executable path"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)

    v1, v2, matrix = copy.deepcopy(documents)
    v2["executableCockpitBindings"]["evidenceItemId"] = (
        "00000000-0000-4000-8000-000000000000"
    )
    with pytest.raises(V2AcceptanceError, match="cockpit data"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_failure_state_status_code_path_and_detail_are_exact(
    documents: tuple[dict, dict, dict],
) -> None:
    for field, value in (
        ("expectedStatus", 200),
        ("expectedCode", "record_found"),
        ("path", "GET /v1/invented"),
        ("recordDetailVisible", True),
    ):
        v1, v2, matrix = copy.deepcopy(documents)
        v2["failureStates"][0][field] = value
        with pytest.raises(V2AcceptanceError):
            validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_pinned_wireframe_component_map_and_base_fixture_drift_fail_closed(
    documents: tuple[dict, dict, dict],
) -> None:
    for field in ("wireframeSha256", "componentMapSha256", "baseFixtureSha256"):
        v1, v2, matrix = copy.deepcopy(documents)
        matrix["meta"][field] = "0" * 64
        with pytest.raises(V2AcceptanceError, match="pinned source drift"):
            validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_duplicate_json_and_protected_material_fail_closed(
    tmp_path: Path, documents: tuple[dict, dict, dict]
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"meta":{},"meta":{}}', encoding="utf-8")
    with pytest.raises(V2AcceptanceError, match="duplicate JSON field"):
        load_json(duplicate)

    v1, v2, matrix = copy.deepcopy(documents)
    v2["surfaceFixtures"][0]["summary"] = "Bea" + "rer public-synthetic-placeholder"
    with pytest.raises(V2AcceptanceError, match="protected material"):
        validate_v2_acceptance(v1=v1, v2=v2, matrix=matrix)


def test_additive_fixture_artifacts_follow_ascii_dash_rule() -> None:
    for path in (V2_FIXTURE_PATH, MATRIX_PATH, Path(__file__)):
        text = path.read_text(encoding="utf-8")
        assert "\u2013" not in text
        assert "\u2014" not in text
        if path.suffix == ".json":
            json.loads(text)
