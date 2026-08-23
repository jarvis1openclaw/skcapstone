"""Deterministic validator for the public synthetic V2 Matter cockpit matrix."""

from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from tests.support.v2_contract_manifest import (
    legacy_fixture_surface_contracts,
    legacy_fixture_surface_ids,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
V1_FIXTURE_PATH = REPO_ROOT / "tests/fixtures/mvp/public-synthetic-mvp-v1.json"
V2_FIXTURE_PATH = REPO_ROOT / "tests/fixtures/mvp/public-synthetic-mvp-v2-cockpit.json"
MATRIX_PATH = REPO_ROOT / "tests/fixtures/mvp/public-synthetic-mvp-v2-acceptance.json"

EXPECTED_SECTIONS = legacy_fixture_surface_ids()
EXPECTED_SURFACE_CONTRACTS = legacy_fixture_surface_contracts()
EXPECTED_COMPONENTS = {
    section: contract["component"]
    for section, contract in EXPECTED_SURFACE_CONTRACTS.items()
}
EXPECTED_API_CONTRACTS = {
    section: contract["api_contract"]
    for section, contract in EXPECTED_SURFACE_CONTRACTS.items()
}
EXPECTED_STATES = {
    section: contract["expected_state"]
    for section, contract in EXPECTED_SURFACE_CONTRACTS.items()
}
EXPECTED_CONTRACT_STATES = {
    section: contract["contract_state"]
    for section, contract in EXPECTED_SURFACE_CONTRACTS.items()
}

REQUIRED_EVIDENCE_ASSERTIONS = {
    "wireframe-source-bound",
    "component-contract-bound",
    "fixture-reference-bound",
    "tenant-matter-scope-bound",
    "audit-state-explicit",
    "external-effect-state-explicit",
}

EXPECTED_EXECUTABLE_PATHS = {
    "authenticated-session": ("browser:session", "authenticated_fixture"),
    "client-navigation": ("GET /v1/clients", "200_scoped_records"),
    "matter-cockpit": (
        "GET /v1/matters/{matter_id}/workspace",
        "200_scoped_workspace",
    ),
    "claim-ledger": (
        "GET /v1/matters/{matter_id}/claims",
        "200_scoped_claim_ledger",
    ),
    "cross-matter-denial": (
        "GET /v1/matters/{matter_id}/workspace",
        "403_without_record_detail",
    ),
    "cross-tenant-denial": (
        "GET /v1/matters/{matter_id}/workspace",
        "403_without_record_detail",
    ),
    "workspace-outage": (
        "GET /v1/matters/{matter_id}/workspace",
        "503_sanitized_fail_closed",
    ),
    "claim-outage": (
        "GET /v1/matters/{matter_id}/claims",
        "503_sanitized_fail_closed",
    ),
    "unauthenticated-browser": ("browser:session", "401_sanitized_fail_closed"),
}


class V2AcceptanceError(ValueError):
    """The additive V2 fixture or acceptance matrix is not exact."""


class _WireframeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[str] = []
        self.navigation: list[tuple[str, str]] = []
        self._navigation_target: str | None = None
        self._navigation_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section" and attributes.get("id"):
            self.sections.append(str(attributes["id"]))
        href = attributes.get("href")
        if tag == "a" and href and href.startswith("#"):
            self._navigation_target = href[1:]
            self._navigation_text = []

    def handle_data(self, data: str) -> None:
        if self._navigation_target is not None:
            self._navigation_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._navigation_target is not None:
            label = " ".join("".join(self._navigation_text).split())
            self.navigation.append((self._navigation_target, label))
            self._navigation_target = None
            self._navigation_text = []


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise V2AcceptanceError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
    )
    if not isinstance(value, dict):
        raise V2AcceptanceError(f"JSON root must be an object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def named_fixture_ids(value: Any) -> set[str]:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("fixtureId", "auditId", "supportId"):
                identifier = item.get(key)
                if isinstance(identifier, str):
                    if identifier in found:
                        raise V2AcceptanceError(
                            f"duplicate fixture identity: {identifier}"
                        )
                    found.add(identifier)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _wireframe_contract(matrix: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    meta = matrix["meta"]
    wireframe_path = REPO_ROOT / meta["wireframePath"]
    component_map_path = REPO_ROOT / meta["componentMapPath"]
    base_fixture_path = REPO_ROOT / meta["baseFixturePath"]
    for path, expected in (
        (wireframe_path, meta["wireframeSha256"]),
        (component_map_path, meta["componentMapSha256"]),
        (base_fixture_path, meta["baseFixtureSha256"]),
    ):
        if sha256(path) != expected:
            raise V2AcceptanceError(
                f"pinned source drift: {path.relative_to(REPO_ROOT)}"
            )
    parser = _WireframeParser()
    parser.feed(wireframe_path.read_text(encoding="utf-8"))
    return parser.sections, dict(parser.navigation)


def validate_v2_acceptance(
    *,
    v1: dict[str, Any] | None = None,
    v2: dict[str, Any] | None = None,
    matrix: dict[str, Any] | None = None,
) -> None:
    v1 = v1 or load_json(V1_FIXTURE_PATH)
    v2 = v2 or load_json(V2_FIXTURE_PATH)
    matrix = matrix or load_json(MATRIX_PATH)

    for document in (v1, v2, matrix):
        meta = document["meta"]
        if (
            meta.get("publicSynthetic") is not True
            or meta.get("classification") != "public"
        ):
            raise V2AcceptanceError("every fixture document must be public synthetic")
        if meta.get("simulationOnly") is not True:
            raise V2AcceptanceError(
                "every fixture document must remain simulation only"
            )
    for key in (
        "containsProtectedMatterContent",
        "containsCredentials",
        "allowsProviderTraffic",
        "allowsExternalEffects",
    ):
        if v2["meta"].get(key) is not False:
            raise V2AcceptanceError(f"V2 fixture safety flag differs: {key}")

    sections, navigation_labels = _wireframe_contract(matrix)
    if tuple(sections) != EXPECTED_SECTIONS:
        raise V2AcceptanceError("wireframe sections differ from the exact V2 order")
    if tuple(matrix.get("navigationOrder", ())) != EXPECTED_SECTIONS:
        raise V2AcceptanceError("matrix navigation differs from the exact V2 order")

    rows = matrix.get("sections")
    if not isinstance(rows, list) or len(rows) != len(EXPECTED_SECTIONS):
        raise V2AcceptanceError("matrix must contain one row per V2 section")
    row_by_section = {row.get("sectionId"): row for row in rows}
    if tuple(row_by_section) != EXPECTED_SECTIONS:
        raise V2AcceptanceError(
            "matrix sections are missing, extra, duplicate, or reordered"
        )

    surface_rows = v2.get("surfaceFixtures")
    if not isinstance(surface_rows, list):
        raise V2AcceptanceError("V2 surface fixtures are missing")
    surface_by_section = {row.get("sectionId"): row for row in surface_rows}
    if tuple(surface_by_section) != EXPECTED_SECTIONS:
        raise V2AcceptanceError("surface fixtures differ from the exact V2 navigation")

    known_ids = named_fixture_ids(v1) | named_fixture_ids(v2)
    referenced_ids: set[str] = set()
    executable_paths = matrix.get("executablePaths")
    if not isinstance(executable_paths, list):
        raise V2AcceptanceError(
            "executable Client, Matter, session, and API paths are missing"
        )
    path_by_id = {row.get("pathId"): row for row in executable_paths}
    if tuple(path_by_id) != tuple(EXPECTED_EXECUTABLE_PATHS):
        raise V2AcceptanceError(
            "executable path matrix is missing, extra, or reordered"
        )
    for path_id, (interface, expected) in EXPECTED_EXECUTABLE_PATHS.items():
        row = path_by_id[path_id]
        if set(row) != {"pathId", "interface", "fixtureRefs", "expected"}:
            raise V2AcceptanceError(f"executable path contract drift: {path_id}")
        if row["interface"] != interface or row["expected"] != expected:
            raise V2AcceptanceError(f"executable path expectation drift: {path_id}")
        refs = row["fixtureRefs"]
        if not isinstance(refs, list) or not refs or not set(refs).issubset(known_ids):
            raise V2AcceptanceError(f"executable path fixture drift: {path_id}")
        referenced_ids.update(refs)
    expected_row_keys = {
        "sectionId",
        "navigationLabel",
        "component",
        "apiContract",
        "contractState",
        "expectedState",
        "fixtureRefs",
        "accessibility",
        "evidenceAssertions",
    }
    for section in EXPECTED_SECTIONS:
        row = row_by_section[section]
        if set(row) != expected_row_keys:
            raise V2AcceptanceError(f"matrix contract drift: {section}")
        if row["navigationLabel"] != navigation_labels.get(section):
            raise V2AcceptanceError(f"navigation label drift: {section}")
        if row["component"] != EXPECTED_COMPONENTS[section]:
            raise V2AcceptanceError(f"component drift: {section}")
        if row["apiContract"] != EXPECTED_API_CONTRACTS[section]:
            raise V2AcceptanceError(f"API contract drift: {section}")
        if row["contractState"] != EXPECTED_CONTRACT_STATES[section]:
            raise V2AcceptanceError(f"contract availability drift: {section}")
        if row["expectedState"] != EXPECTED_STATES[section]:
            raise V2AcceptanceError(f"completion state drift: {section}")
        refs = row["fixtureRefs"]
        if not isinstance(refs, list) or not refs or not set(refs).issubset(known_ids):
            raise V2AcceptanceError(f"invented or missing fixture reference: {section}")
        referenced_ids.update(refs)
        if set(row["evidenceAssertions"]) != REQUIRED_EVIDENCE_ASSERTIONS:
            raise V2AcceptanceError(f"source or audit evidence is unbound: {section}")
        accessibility = row["accessibility"]
        expected_live = (
            "polite"
            if row["expectedState"] == "safely_unavailable" or section == "failures"
            else "off"
        )
        if accessibility != {
            "landmark": "region",
            "accessibleName": row["navigationLabel"],
            "keyboard": "navigation_and_content_reachable",
            "liveRegion": expected_live,
        }:
            raise V2AcceptanceError(f"accessibility contract drift: {section}")
        surface = surface_by_section[section]
        if surface.get("state") != row["expectedState"]:
            raise V2AcceptanceError(f"surface state differs from matrix: {section}")

    v2_named_ids = named_fixture_ids(v2)
    if not v2_named_ids.issubset(referenced_ids):
        raise V2AcceptanceError("V2 fixture contains an unbound invented record")

    scope = v2["scope"]
    tenant_id = scope["tenantId"]
    matter_id = scope["matterId"]
    for surface in surface_rows:
        if surface.get("tenantId") != tenant_id or surface.get("matterId") != matter_id:
            raise V2AcceptanceError("cross-Tenant or cross-Matter surface fixture")
    if (
        tenant_id != v1["identifiers"]["tenantId"]
        or matter_id != v1["identifiers"]["matterId"]
    ):
        raise V2AcceptanceError("V2 scope differs from the reviewed V1 fixture")

    cockpit = v2["executableCockpitBindings"]
    primary_matter_fixture = next(
        row["fixtureId"]
        for row in v1["matters"]
        if row["id"] == v1["identifiers"]["matterId"]
    )
    expected_cockpit = {
        "sessionFixtureId": v1["session"]["fixtureId"],
        "clientFixtureId": v1["clients"][0]["fixtureId"],
        "matterFixtureId": primary_matter_fixture,
        "factAssertionId": v1["workspace"]["facts"][0]["factAssertionId"],
        "evidenceItemId": v1["workspace"]["evidence"][0]["evidenceItemId"],
        "claimSupportFixtureId": v1["claimLedger"]["claims"][0]["support"][0][
            "supportId"
        ],
        "workProductId": v1["workspace"]["workProducts"][0]["workProductId"],
        "workProductVersionId": v1["workspace"]["workProducts"][0]["currentVersion"][
            "versionId"
        ],
        "approvalFixtureId": v1["approvals"][0]["fixtureId"],
        "auditFixtureIds": [row["auditId"] for row in v1["workspace"]["audit"]],
        "sourceSnapshot": v1["workspace"]["provenance"]["sourceSnapshot"],
    }
    if cockpit != expected_cockpit:
        raise V2AcceptanceError(
            "executable cockpit data differs from reviewed API fixture"
        )

    boundary = v2["externalActionBoundary"]
    if (
        boundary.get("simulationOnly") is not True
        or boundary.get("externalEffectOccurred") is not False
        or boundary.get("recommendationAcceptanceCanDispatch") is not False
        or boundary.get("directActionControls") != []
    ):
        raise V2AcceptanceError("external-action progression is forbidden")
    for action in v1.get("simulatedActions", []):
        if (
            action.get("simulationOnly") is not True
            or action.get("externalEffectOccurred") is not False
        ):
            raise V2AcceptanceError("base fixture external action escaped simulation")

    expected_failures = {
        "public-synthetic-v2-failure-unauthenticated": (
            "browser:session",
            401,
            "authentication_required",
        ),
        "public-synthetic-v2-failure-cross-matter": (
            "GET /v1/matters/{matter_id}/workspace",
            403,
            "matter_membership_denied",
        ),
        "public-synthetic-v2-failure-cross-tenant": (
            "GET /v1/matters/{matter_id}/workspace",
            403,
            "matter_membership_denied",
        ),
        "public-synthetic-v2-failure-workspace-unavailable": (
            "GET /v1/matters/{matter_id}/workspace",
            503,
            "workspace_unavailable",
        ),
        "public-synthetic-v2-failure-claim-unavailable": (
            "GET /v1/matters/{matter_id}/claims",
            503,
            "claim_ledger_unavailable",
        ),
    }
    failures = v2.get("failureStates")
    if not isinstance(failures, list) or len(failures) != len(expected_failures):
        raise V2AcceptanceError("exact cockpit failure states are missing")
    for failure in failures:
        expected = expected_failures.get(failure.get("fixtureId"))
        if (
            expected is None
            or (
                failure.get("path"),
                failure.get("expectedStatus"),
                failure.get("expectedCode"),
            )
            != expected
        ):
            raise V2AcceptanceError("cockpit failure status or code drift")
        if failure.get("recordDetailVisible") is not False:
            raise V2AcceptanceError("cockpit failure leaks record detail")

    serialized = json.dumps(v2, sort_keys=True).lower()
    for forbidden in ("authorization", "bearer ", "vault:", "inbox/"):
        if forbidden in serialized:
            raise V2AcceptanceError("V2 fixture contains prohibited protected material")
