"""Loader for the single authoritative SKLegal V2 MVP contract manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    FormatChecker,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
V2_CONTRACT_MANIFEST_PATH = (
    REPO_ROOT / "docs/contracts/v2-mvp/v2-surface-manifest.v1.json"
)


def load_v2_contract_manifest() -> dict[str, Any]:
    """Load a fresh manifest value so negative tests cannot mutate shared state."""

    return json.loads(V2_CONTRACT_MANIFEST_PATH.read_text(encoding="utf-8"))


def canonical_surface_ids() -> tuple[str, ...]:
    """Return the exact ordered React and browser section contract."""

    manifest = load_v2_contract_manifest()
    return tuple(row["section_id"] for row in manifest["surfaces"])


def legacy_fixture_surface_ids() -> tuple[str, ...]:
    """Return the reconciled 17-row legacy fixture aliases in canonical order."""

    manifest = load_v2_contract_manifest()
    return tuple(
        row["legacy_fixture_section_id"]
        for row in manifest["surfaces"]
        if row["legacy_fixture_section_id"] is not None
    )


def legacy_fixture_surface_contracts() -> dict[str, dict[str, Any]]:
    """Project every legacy fixture contract from the canonical manifest."""

    manifest = load_v2_contract_manifest()
    return {
        contract["legacy_fixture_section_id"]: {
            "component": contract["component"],
            "api_contract": contract["api_contract"],
            "expected_state": contract["legacy_expected_state"],
            "contract_state": contract["legacy_contract_state"],
        }
        for contract in manifest["surface_consumers"].values()
        if contract["legacy_fixture_section_id"] is not None
    }


def react_surface_contracts() -> dict[str, str]:
    """Project the exact React feature-state assertion from the manifest."""

    manifest = load_v2_contract_manifest()
    return {
        section_id: contract["react_feature_state"]
        for section_id, contract in manifest["surface_consumers"].items()
    }


def validate_claim_projection(value: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate schema plus equality and legacy status migration invariants."""

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    ledger = value["ledger_projection"]
    if ledger["tenant_id"] != value["tenant_id"]:
        raise ValueError("ledger projection Tenant mismatch")
    if ledger["matter_id"] != value["matter_id"]:
        raise ValueError("ledger projection Matter mismatch")
    if ledger["claim_id"] != value["claim_id"]:
        raise ValueError("ledger projection claim identity mismatch")
    if ledger["projected_claim_version"] != value["claim_version"]:
        raise ValueError("ledger projection claim version mismatch")
    if ledger["projected_claim_status"] != value["claim_status"]:
        raise ValueError("ledger projection claim status mismatch")
    migration = ledger["legacy_migration"]
    if migration is None:
        return
    status_map = {
        "proposed": "proposed",
        "under_review": "under_review",
        "supported": "accepted",
        "challenged": "challenged",
        "withdrawn": "withdrawn",
    }
    if migration["mapped_claim_status"] != status_map[migration["legacy_status"]]:
        raise ValueError("legacy ledger status mapping mismatch")
    if migration["mapped_claim_status"] != value["claim_status"]:
        raise ValueError("legacy ledger status does not map to canonical Claim status")


def validate_approval_validity(value: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate schema plus exact operative subject and scope equality."""

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    if not value["operative"]:
        return
    decision = value["capability_decision"]
    if decision["outcome"] != "allowed":
        raise ValueError("operative Approval capability decision is not allowed")
    if decision["tenant_id"] != value["tenant_id"]:
        raise ValueError("operative Approval capability Tenant mismatch")
    if decision["matter_id"] != value["matter_id"]:
        raise ValueError("operative Approval capability Matter mismatch")
    if decision["subject"] != value["subject"]:
        raise ValueError("operative Approval capability subject mismatch")
    if value["current_scope"] != {
        "tenant_id": value["tenant_id"],
        "matter_id": value["matter_id"],
    }:
        raise ValueError("operative Approval current scope mismatch")
    if value["current_subject"] != value["subject"]:
        raise ValueError("operative Approval current subject mismatch")
    if value["superseded_by_approval_id"] is not None:
        raise ValueError("operative Approval is superseded")
