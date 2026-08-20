"""ATLAS CMDB operator-facet tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from skcapstone.operator_seat import cmdb_adapter


class _CleanManager:
    def __init__(self, _home):
        pass

    def audit_relationships(self):
        return []


def _artifact(home: Path, *, complete: bool = True, ended_at="2026-08-20T19:00:00Z"):
    directory = home / "cmdb" / "reconcile-runs"
    directory.mkdir(parents=True)
    path = directory / "run-1.json"
    payload = json.dumps(
        {"ended_at": ended_at, "completeness": {"complete": complete}},
        sort_keys=True,
    ).encode()
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_text(hashlib.sha256(payload).hexdigest())


def test_explain_keeps_apply_nonstandard_and_irreversible():
    actions = {item["name"]: item for item in cmdb_adapter.cmdb_explain()["actions"]}
    assert actions["run-cmdb-shadow"]["standard"] is True
    assert actions["apply-cmdb-reconcile"]["standard"] is False
    assert actions["apply-cmdb-reconcile"]["reversible"] is False


def test_observe_uses_only_verified_complete_fresh_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / ".skcapstone"
    _artifact(home)
    result = cmdb_adapter.observe(
        now_iso="2026-08-20T20:00:00Z", manager_factory=_CleanManager
    )
    statuses = {item["type"]: item["status"] for item in result["conditions"]}
    assert statuses == {
        "CmdbReconcileFresh": "True",
        "CmdbLastScanComplete": "True",
        "CmdbAuditClean": "True",
    }


def test_observe_rejects_bad_checksum_and_reports_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / ".skcapstone"
    _artifact(home)
    (home / "cmdb" / "reconcile-runs" / "run-1.sha256").write_text("0" * 64)
    result = cmdb_adapter.observe(manager_factory=_CleanManager)
    statuses = {item["type"]: item["status"] for item in result["conditions"]}
    assert statuses["CmdbReconcileFresh"] == "Unknown"
    assert statuses["CmdbLastScanComplete"] == "Unknown"


def test_act_freeze_wins(tmp_path):
    from skcapstone.fleet.paths import FleetPaths
    from skcapstone.fleet.store import Writer, set_frozen

    paths = FleetPaths(tmp_path)
    set_frozen(
        paths,
        True,
        writer=Writer(role="operator", node="cli", identity="chef"),
        reason="test",
    )
    called = []
    result = cmdb_adapter.cmdb_act(paths, "run-cmdb-shadow", runner=called.append)
    assert result == {"performed": False, "reason": "frozen", "action": "run-cmdb-shadow"}
    assert called == []


def test_shadow_act_starts_only_the_shadow_oneshot(tmp_path):
    from skcapstone.fleet.paths import FleetPaths

    calls = []

    def runner(argv):
        calls.append(argv)
        return True

    result = cmdb_adapter.cmdb_act(
        FleetPaths(tmp_path), "run-cmdb-shadow", runner=runner
    )
    assert result["performed"] is True
    assert calls == [
        ["systemctl", "--user", "start", "skcapstone-cmdb-reconcile-shadow.service"]
    ]
