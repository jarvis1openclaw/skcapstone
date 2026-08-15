"""skfleet node doctor (card 76dad234).

Two properties matter beyond the output shape. The command must write
NOTHING, because it is meant to run everywhere including on the node it is
judging. And an unbound node must be a skip, never a failure, because
"no role yet" is the normal state during rollout.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from skcapstone.fleet import store
from skcapstone.fleet.cli import fleet

WORKER_PROFILE = {
    "description": "gpu worker",
    "units": {
        "required": ["skai-beellama.service"],
        "allowed": ["skai-beellama.service"],
        "mustNot": ["skchat-daemon.service"],
    },
    "packages": {"required": [], "allowed": ["skcapstone"], "mustNot": ["skmemory"]},
    "unitsIgnore": ["gpg-agent*.socket"],
    "stateTier": "none",
    "capauthIdentityClass": "worker",
    "syncFolders": ["skfleet-control"],
}


def _env(paths) -> dict:
    return {"SKFLEET_ROOT": str(paths.root), "SKFLEET_NODE": "node-under-test"}


def _snapshot(root):
    return {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def fleet_tree(paths, operator, monkeypatch):
    """A node bound to a worker profile, with a drifted published inventory."""
    store.write_spec(paths, "profile", "worker-gpu", WORKER_PROFILE, writer=operator)
    store.write_spec(
        paths,
        "node",
        "node-under-test",
        {"role": "worker-gpu", "cordoned": False},
        writer=operator,
    )
    monkeypatch.setattr(
        "skcapstone.fleet.nodeinventory.collect",
        lambda **kw: {
            "units": {
                "user": {
                    "skchat-daemon.service": "enabled",  # forbidden
                    "gpg-agent.socket": "enabled",  # ignored
                    "extra.service": "enabled",  # unexpected
                }
            },
            "packages": {"skmemory": "1.0"},  # forbidden
            "collectedAt": "2026-08-15T00:00:00Z",
        },
    )
    return paths


# ------------------------------------------------------------------ json ---


def test_json_carries_all_six_categories(fleet_tree) -> None:
    result = CliRunner().invoke(fleet, ["node", "doctor", "--json"], env=_env(fleet_tree))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    report = payload[0]
    for category in (
        "missing_required_units",
        "forbidden_units",
        "unexpected_units",
        "missing_required_packages",
        "forbidden_packages",
        "unexpected_packages",
    ):
        assert category in report
    assert report["node"] == "node-under-test"
    assert report["role"] == "worker-gpu"
    assert report["forbidden_units"] == ["skchat-daemon.service"]
    assert report["forbidden_packages"] == ["skmemory"]
    assert report["unexpected_units"] == ["extra.service"]  # gpg-agent ignored
    assert report["severity"] == "error"


# ------------------------------------------------------------ exit codes ---


def test_exits_zero_with_drift_by_default(fleet_tree) -> None:
    """Report only. Drift is information, not a failure."""
    result = CliRunner().invoke(fleet, ["node", "doctor"], env=_env(fleet_tree))
    assert result.exit_code == 0, result.output
    assert "ERROR" in result.output
    assert "skchat-daemon.service" in result.output


def test_strict_exits_one_on_a_forbidden_finding(fleet_tree) -> None:
    result = CliRunner().invoke(fleet, ["node", "doctor", "--strict"], env=_env(fleet_tree))
    assert result.exit_code == 1


def test_strict_exits_zero_when_only_info_findings(paths, operator, monkeypatch) -> None:
    """--strict gates on error grade only, so a manifest lagging reality
    does not start failing everyone's pipeline."""
    store.write_spec(paths, "profile", "worker-gpu", WORKER_PROFILE, writer=operator)
    store.write_spec(paths, "node", "node-under-test", {"role": "worker-gpu"}, writer=operator)
    monkeypatch.setattr(
        "skcapstone.fleet.nodeinventory.collect",
        lambda **kw: {
            "units": {"user": {"skai-beellama.service": "enabled", "extra.service": "enabled"}},
            "packages": {},
            "collectedAt": "t",
        },
    )
    result = CliRunner().invoke(fleet, ["node", "doctor", "--strict"], env=_env(paths))
    assert result.exit_code == 0, result.output


def test_a_clean_node_reports_clean(paths, operator, monkeypatch) -> None:
    store.write_spec(paths, "profile", "worker-gpu", WORKER_PROFILE, writer=operator)
    store.write_spec(paths, "node", "node-under-test", {"role": "worker-gpu"}, writer=operator)
    monkeypatch.setattr(
        "skcapstone.fleet.nodeinventory.collect",
        lambda **kw: {
            "units": {"user": {"skai-beellama.service": "enabled"}},
            "packages": {},
            "collectedAt": "t",
        },
    )
    result = CliRunner().invoke(fleet, ["node", "doctor"], env=_env(paths))
    assert result.exit_code == 0
    assert "(clean)" in result.output
    assert "OK" in result.output


# ----------------------------------------------------------------- skips ---


def test_a_node_with_no_role_is_skipped_not_failed(paths, operator, monkeypatch) -> None:
    store.write_spec(paths, "node", "node-under-test", {"cordoned": False}, writer=operator)
    monkeypatch.setattr(
        "skcapstone.fleet.nodeinventory.collect",
        lambda **kw: {"units": {"user": {}}, "packages": {}, "collectedAt": "t"},
    )
    result = CliRunner().invoke(fleet, ["node", "doctor"], env=_env(paths))
    assert result.exit_code == 0, result.output
    assert "no spec.role set" in result.output


def test_a_role_with_no_profile_object_is_skipped(paths, operator, monkeypatch) -> None:
    store.write_spec(paths, "node", "node-under-test", {"role": "not-authored"}, writer=operator)
    monkeypatch.setattr(
        "skcapstone.fleet.nodeinventory.collect",
        lambda **kw: {"units": {"user": {}}, "packages": {}, "collectedAt": "t"},
    )
    result = CliRunner().invoke(fleet, ["node", "doctor"], env=_env(paths))
    assert result.exit_code == 0
    assert "no valid profile object" in result.output


def test_all_skips_the_roleless_node_and_still_exits_zero(fleet_tree, operator) -> None:
    store.write_spec(fleet_tree, "node", "node-roleless", {"cordoned": False}, writer=operator)
    result = CliRunner().invoke(fleet, ["node", "doctor", "--all"], env=_env(fleet_tree))
    assert result.exit_code == 0, result.output
    assert "node-roleless" in result.output
    assert "no spec.role set" in result.output


def test_all_reads_published_inventory_not_the_local_host(fleet_tree, operator) -> None:
    """--all must not report every node's drift using THIS node's units."""
    sknoded = store.Writer(role="sknoded", node="node-under-test", identity="")
    store.write_node_file(
        fleet_tree,
        sknoded,
        "node.json",
        {
            "kind": "Node",
            "name": "node-under-test",
            "node": "node-under-test",
            "status": {"inventory": {"units": {"user": {"skai-beellama.service": "enabled"}}}},
        },
    )
    result = CliRunner().invoke(fleet, ["node", "doctor", "--all", "--json"], env=_env(fleet_tree))
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)[0]
    assert report["forbidden_units"] == []  # published inventory is clean


# ----------------------------------------------------------- zero writes ---


def test_doctor_writes_nothing_at_all(fleet_tree) -> None:
    """It runs on the node it judges, so it must be inert."""
    before = _snapshot(fleet_tree.root)
    CliRunner().invoke(fleet, ["node", "doctor"], env=_env(fleet_tree))
    CliRunner().invoke(fleet, ["node", "doctor", "--json"], env=_env(fleet_tree))
    CliRunner().invoke(fleet, ["node", "doctor", "--all"], env=_env(fleet_tree))
    assert _snapshot(fleet_tree.root) == before


def test_doctor_never_bumps_a_generation(fleet_tree) -> None:
    before = store.read_spec(fleet_tree, "node", "node-under-test")["generation"]
    CliRunner().invoke(fleet, ["node", "doctor"], env=_env(fleet_tree))
    assert store.read_spec(fleet_tree, "node", "node-under-test")["generation"] == before
