"""The fleet adapter is a conformant reference implementation of the contract."""

from __future__ import annotations

from skcapstone.fleet import sknoded, store
from skcapstone.fleet.paths import FleetPaths
from skcapstone.operator_seat import adapter, fleet_adapter


def _enroll(tmp_path, monkeypatch):
    monkeypatch.setenv("SKFLEET_ROOT", str(tmp_path / "fleet"))
    monkeypatch.setenv("SKFLEET_NODE", "node-158")
    monkeypatch.setattr(
        "skcapstone.fleet.sknoded.node_capacity",
        lambda: {"cores": 8, "ram_gb": 16.0, "disk_gb": 100.0, "gpu": None, "vram_gb": None},
    )
    paths = FleetPaths(root=tmp_path / "fleet")
    operator = store.Writer(role="operator", node="node-158", identity="")
    sknoded.run_once(paths, "node-158")
    store.write_spec(paths, "node", "node-158", {"cordoned": False}, writer=operator)
    sknoded.run_once(paths, "node-158")
    return paths, operator


def test_fleet_explain_is_contract_conformant():
    assert adapter.validate_explain(fleet_adapter.fleet_explain()) == []


def test_fleet_observe_is_contract_conformant(tmp_path, monkeypatch):
    paths, _ = _enroll(tmp_path, monkeypatch)
    obs = fleet_adapter.fleet_observe(paths, "2026-07-29T00:00:00Z")
    assert adapter.validate_observe(obs) == []


def test_fleet_observe_reports_a_cronjob_missed_run(tmp_path, monkeypatch):
    paths, operator = _enroll(tmp_path, monkeypatch)
    store.write_spec(
        paths, "cronjob", "nightly", {"schedule": "@daily", "command": "echo hi"}, writer=operator
    )
    obs = fleet_adapter.fleet_observe(paths, "2026-07-29T00:00:00Z")
    missed = [
        c for c in obs["conditions"] if c["type"] == "MissedRun" and c["object"] == "nightly"
    ]
    assert len(missed) == 1
    assert missed[0]["status"] in ("True", "False")


def test_fleet_observe_writes_nothing(tmp_path, monkeypatch):
    # The observe verb is read-only: a second observe sees the same tree, and no
    # object files were created by observing.
    paths, _ = _enroll(tmp_path, monkeypatch)
    before = sorted(p.name for p in (tmp_path / "fleet").rglob("*.json"))
    fleet_adapter.fleet_observe(paths, "2026-07-29T00:00:00Z")
    after = sorted(p.name for p in (tmp_path / "fleet").rglob("*.json"))
    assert before == after


def test_fleet_act_records_signed_annotation(tmp_path, monkeypatch):
    paths, operator = _enroll(tmp_path, monkeypatch)
    store.write_spec(
        paths, "cronjob", "nightly", {"schedule": "@daily", "command": "echo hi"}, writer=operator
    )
    prop = {"action": "rerun_cronjob", "object": "nightly", "rationale": "missed run"}
    fleet_adapter.fleet_act(
        paths, prop, {"change_class": "normal"}, now_iso="2026-07-29T00:00:00Z"
    )
    spec = store.read_spec(paths, "cronjob", "nightly")["spec"]
    assert spec["operatorActions"][-1]["action"] == "rerun_cronjob"
    assert spec["operatorActions"][-1]["by"] == "atlas"


def test_fleet_act_refuses_when_frozen(tmp_path, monkeypatch):
    import pytest

    paths, operator = _enroll(tmp_path, monkeypatch)
    store.write_spec(paths, "service", "web", {"unit": "web.service"}, writer=operator)
    human = store.Writer(role="operator", node="node-158", identity="chef")
    store.set_frozen(paths, True, writer=human)
    with pytest.raises(RuntimeError):
        fleet_adapter.fleet_act(
            paths,
            {"action": "restart_service", "object": "web"},
            {"change_class": "standard"},
            now_iso="2026-07-29T00:00:00Z",
        )


def test_fleet_act_unmapped_action_raises(tmp_path, monkeypatch):
    import pytest

    paths, _ = _enroll(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        fleet_adapter.fleet_act(
            paths,
            {"action": "delete_object", "object": "x"},
            {"change_class": "major"},
            now_iso="2026-07-29T00:00:00Z",
        )


def test_fleet_act_unknown_object_raises(tmp_path, monkeypatch):
    import pytest

    paths, _ = _enroll(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        fleet_adapter.fleet_act(
            paths,
            {"action": "rerun_cronjob", "object": "ghost"},
            {"change_class": "normal"},
            now_iso="2026-07-29T00:00:00Z",
        )
