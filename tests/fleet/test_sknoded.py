"""Tests for sknoded v1: self-report + join request."""

from __future__ import annotations

import pytest

from skcapstone.fleet import sknoded, store

CAP = {"cores": 4, "ram_gb": 8.0, "disk_gb": 50.0, "gpu": None, "vram_gb": None}


@pytest.fixture(autouse=True)
def _fixed_capacity(monkeypatch):
    monkeypatch.setattr("skcapstone.fleet.sknoded.node_capacity", lambda: dict(CAP))


def test_first_run_writes_all_three(paths) -> None:
    result = sknoded.run_once(paths, "node-41")
    assert result == {"heartbeat": True, "node": True, "join": True}
    hb = store.read_node_file(paths, "node-41", "heartbeat.json")
    assert hb["name"] == "node-41" and "ts" in hb
    report = store.read_node_file(paths, "node-41", "node.json")
    assert report["status"]["capacity"]["cores"] == 4
    assert report["observedGeneration"] == 0  # unadmitted
    join = store.read_node_file(paths, "node-41", "join.json")
    assert join["name"] == "node-41" and join["capacity"]["ram_gb"] == 8.0


def test_second_run_is_write_on_change(paths) -> None:
    sknoded.run_once(paths, "node-41")
    result = sknoded.run_once(paths, "node-41")
    assert result["heartbeat"] is True  # heartbeat always beats
    assert result["node"] is False  # unchanged report skipped
    assert result["join"] is False  # join written once


def test_admitted_node_reports_generation_and_stops_joining(paths, operator) -> None:
    sknoded.run_once(paths, "node-41")
    store.write_spec(paths, "node", "node-41", {"cordoned": False}, writer=operator)
    result = sknoded.run_once(paths, "node-41")
    assert result["node"] is True  # observedGeneration 0 -> 1 changed
    assert store.read_node_file(paths, "node-41", "node.json")["observedGeneration"] == 1


def test_never_writes_outside_own_subtree(paths) -> None:
    sknoded.run_once(paths, "node-41")
    written = [p for p in paths.root.rglob("*") if p.is_file()]
    assert written and all(
        str(p).startswith(str(paths.node_status_dir("node-41"))) for p in written
    )


def test_main_loop_once_runs_a_single_pass_without_sleeping(paths, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sknoded, "run_once", lambda p, n: calls.append(n))
    monkeypatch.setattr(sknoded.time, "sleep", lambda s: pytest.fail("once=True must not sleep"))
    sknoded.main_loop(paths, "node-41", once=True)
    assert calls == ["node-41"]


def test_main_loop_repeats_and_sleeps_the_given_interval(paths, monkeypatch) -> None:
    calls = []
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        raise RuntimeError("stop after first cycle")

    monkeypatch.setattr(sknoded, "run_once", lambda p, n: calls.append(n))
    monkeypatch.setattr(sknoded.time, "sleep", fake_sleep)
    with pytest.raises(RuntimeError, match="stop after first cycle"):
        sknoded.main_loop(paths, "node-41", interval=5)
    assert calls == ["node-41"]
    assert sleeps == [5]
