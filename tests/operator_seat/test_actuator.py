"""Atlas's muscle: physical actuation via tested primitives, gated by freeze."""

from __future__ import annotations

from skcapstone.fleet import store
from skcapstone.fleet.paths import FleetPaths
from skcapstone.operator_seat import actuator


def _paths(tmp_path):
    return FleetPaths(root=tmp_path / "fleet")


def _human():
    return store.Writer(role="operator", node="n", identity="chef")


def _provision(paths):
    """Human writes the freeze store in its off position (the ceremony
    section 3.6 requires before any actuation surface may run)."""
    store.set_frozen(paths, False, writer=_human(), reason="initial provisioning")


def _ok_runner(cmd):
    import subprocess

    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_unconsumed_actions_filters_by_key():
    acts = [
        {"ts": "t1", "action": "restart_service"},
        {"ts": "t2", "action": "restart_service"},
    ]
    consumed = {actuator.action_key(acts[0])}
    out = actuator.unconsumed_actions(acts, consumed)
    assert out == [acts[1]]


def test_honor_restart_calls_actuation(tmp_path):
    paths = _paths(tmp_path)
    _provision(paths)
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return _ok_runner(cmd)

    res = actuator.honor(
        paths, {"ts": "t1", "action": "restart_service"}, "web.service", runner=runner
    )
    assert res["performed"] is True
    assert any("restart" in c and "web.service" in c for c in calls)


def test_honor_refuses_when_frozen(tmp_path):
    paths = _paths(tmp_path)
    _provision(paths)
    store.set_frozen(paths, True, writer=_human())
    calls = []

    res = actuator.honor(
        paths,
        {"ts": "t1", "action": "restart_service"},
        "web.service",
        runner=lambda c: calls.append(c) or _ok_runner(c),
    )
    assert res["performed"] is False and res["reason"] == "frozen"
    assert calls == []  # never touched actuation while frozen


def test_honor_unmapped_action_is_noop(tmp_path):
    paths = _paths(tmp_path)
    _provision(paths)
    res = actuator.honor(paths, {"ts": "t1", "action": "rerun_cronjob"}, "n/a", runner=_ok_runner)
    assert res["performed"] is False and "no physical muscle" in res["reason"]


# ── readiness gating (coord card 3925d012 / SKW-AUTONOMY-E2) ──────────────


def test_honor_refuses_when_unprovisioned(tmp_path):
    """Negative test (fails against current code before the change): an
    estate with no freeze store at all must refuse actuation with reason
    "unprovisioned", not silently perform it."""
    paths = _paths(tmp_path)  # no set_frozen call: nothing provisioned
    calls = []

    res = actuator.honor(
        paths,
        {"ts": "t1", "action": "restart_service"},
        "web.service",
        runner=lambda c: calls.append(c) or _ok_runner(c),
    )
    assert res["performed"] is False and res["reason"] == "unprovisioned"
    assert calls == []  # never touched actuation while unprovisioned


def test_honor_refuses_when_freeze_file_corrupt(tmp_path):
    paths = _paths(tmp_path)
    paths.freeze_path().parent.mkdir(parents=True, exist_ok=True)
    paths.freeze_path().write_text("not json")
    calls = []

    res = actuator.honor(
        paths,
        {"ts": "t1", "action": "restart_service"},
        "web.service",
        runner=lambda c: calls.append(c) or _ok_runner(c),
    )
    assert res["performed"] is False and res["reason"] == "frozen"
    assert calls == []
