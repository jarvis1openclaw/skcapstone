"""Loader for the single authoritative SKLegal V2 MVP contract manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
