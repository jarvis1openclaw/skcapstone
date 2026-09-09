"""Canonical source-backed lifecycle seat profiles."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

LIFECYCLE_SEATS = frozenset({"link", "mero", "seraph", "niobe", "tank", "atlas"})


def load_lifecycle_seat_profiles() -> dict[str, Any]:
    """Load and fail closed on the shipped lifecycle seat contract."""

    path = files("skcapstone").joinpath("data/lifecycle-seat-profiles.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "skfleet.lifecycle-seat-profiles/v1":
        raise ValueError("unsupported lifecycle seat profile schema")
    if set(value.get("seats", {})) != LIFECYCLE_SEATS:
        raise ValueError("lifecycle seat set does not match the canonical six seats")
    if value.get("default_model_route") != "sk-codex-mid":
        raise ValueError("lifecycle seats must default to sk-codex-mid")
    if value.get("jarvis", {}).get("recurring_lifecycle") is not False:
        raise ValueError("Jarvis must remain outside recurring lifecycle work")
    return value
