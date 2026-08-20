"""CMDB operator facet for Atlas.

Atlas observes the CMDB through verified reconcile artifacts and the append-only
store audit.  Physical reconcile is deliberately not wired into the autonomous
HONOR catalog: the safe shadow action and the apply action remain explicit
operator-facet verbs until the rollout gate and human ratification are complete.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..fleet import actuation, store

PROBLEM_WHEN_TRUE = frozenset()
_MAX_ARTIFACT_AGE = timedelta(hours=4)
_SHADOW_UNIT = "skcapstone-cmdb-reconcile-shadow.service"
_APPLY_UNIT = "skcapstone-cmdb-reconcile.service"

_ACTIONS = [
    {
        "name": "run-cmdb-shadow",
        "standard": True,
        "reversible": True,
        "blast_radius": "low",
        "runbook": "run one credentialed, write-free CMDB network reconcile",
        "kedb_refs": ["ke-cmdb-reconcile-stale"],
    },
    {
        "name": "apply-cmdb-reconcile",
        "standard": False,
        "reversible": False,
        "blast_radius": "medium",
        "runbook": "apply CMDB reconciliation after the three-shadow-run gate",
        "kedb_refs": ["ke-cmdb-reconcile-stale"],
    },
]


def cmdb_explain() -> dict:
    """Return the CMDB operator contract."""
    return {
        "kinds": ["cmdb"],
        "conditions": ["CmdbReconcileFresh", "CmdbLastScanComplete", "CmdbAuditClean"],
        "actions": list(_ACTIONS),
    }


def _verified_latest_artifact(home: Path) -> dict | None:
    """Return the newest checksum-verified artifact, or ``None``.

    Invalid, missing, or unchecksummed artifacts are not trusted as operational
    evidence.  This reader never modifies the artifact directory.
    """
    directory = home / "cmdb" / "reconcile-runs"
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in reversed(candidates):
        try:
            payload = path.read_bytes()
            expected = path.with_suffix(".sha256").read_text().strip().split()[0]
            if hashlib.sha256(payload).hexdigest() != expected:
                continue
            value = json.loads(payload)
            if isinstance(value, dict):
                return value
        except (OSError, ValueError, IndexError, json.JSONDecodeError):
            continue
    return None


def _parse_time(value: object) -> datetime | None:
    """Parse one UTC timestamp without accepting naive values."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def observe(paths=None, now_iso: str | None = None, *, manager_factory=None) -> dict:
    """Observe reconcile freshness, completeness, and store integrity.

    Missing evidence reports ``Unknown`` rather than inventing health.  Atlas
    files Unknown conditions as stale, keeping the gap visible without turning
    an unreadable probe into a false positive.
    """
    home = Path.home() / ".skcapstone"
    artifact = _verified_latest_artifact(home)
    freshness = completeness = "Unknown"
    if artifact is not None:
        ended = _parse_time(artifact.get("ended_at"))
        now = _parse_time(now_iso) if now_iso else datetime.now(timezone.utc)
        if ended is not None and now is not None:
            freshness = "True" if now - ended <= _MAX_ARTIFACT_AGE else "False"
        completeness = (
            "True" if artifact.get("completeness", {}).get("complete") is True else "False"
        )

    audit = "Unknown"
    try:
        if manager_factory is None:
            from skcoord.cmdb import CMDBManager

            manager_factory = CMDBManager
        findings = manager_factory(home).audit_relationships()
        audit = "True" if not findings else "False"
    except Exception:
        audit = "Unknown"

    return {
        "conditions": [
            {"type": "CmdbReconcileFresh", "status": freshness},
            {"type": "CmdbLastScanComplete", "status": completeness},
            {"type": "CmdbAuditClean", "status": audit},
        ]
    }


def cmdb_act(paths, action: str, *, runner: Callable | None = None) -> dict:
    """Start one reviewed CMDB oneshot; freeze always wins.

    The apply action exists for human-governed execution but is intentionally
    non-standard and irreversible, ensuring Atlas policy classifies it MAJOR.
    """
    if store.is_frozen(paths):
        return {"performed": False, "reason": "frozen", "action": action}
    units = {"run-cmdb-shadow": _SHADOW_UNIT, "apply-cmdb-reconcile": _APPLY_UNIT}
    if action not in units:
        raise ValueError(f"unknown CMDB action: {action!r}")
    run = runner or actuation.default_runner
    ok = run(["systemctl", "--user", "start", units[action]])
    if hasattr(ok, "returncode"):
        ok = ok.returncode == 0
    return {"performed": bool(ok), "action": action, "unit": units[action]}


__all__ = ["PROBLEM_WHEN_TRUE", "cmdb_act", "cmdb_explain", "observe"]
