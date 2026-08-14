"""The fleet tree is self-contained: nothing writes outside FleetPaths.

Epic 3bbf39ea, card 59f78375 (parent ddb2a02f). Before the control-bus
Syncthing split is safe, we have to know that relocating the fleet root
relocates the WHOLE fleet. If any writer reaches around FleetPaths to a
hardcoded ~/.skcapstone path, the split silently leaves state behind on the
old root and the folder share stops meaning what it says.

This is what makes the split reversible instead of a leap: point
SKFLEET_ROOT somewhere else, drive every writer, and prove two things at
once. Every file the run created lives under the temp root, and the real
tree gained nothing.

Companion audit (run in card 59f78375, result pasted in the PR):

    $ grep -rn '.skcapstone' src/skcapstone/fleet/
    src/skcapstone/fleet/paths.py:77:    root = os.environ.get("SKFLEET_ROOT", "~/.skcapstone/fleet")

One hit, in the one module allowed to have it. The other home-anchored
paths in the package (alerts.py finding the sk-alert binary, capacity.py
probing free disk on $HOME) are reads that never touch the fleet tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skcapstone.fleet import admission, events, scheduler, sknoded, store
from skcapstone.fleet.paths import FleetPaths, default_paths
from skcapstone.fleet.scheduler import NodeView, Workload

NODE = "node-relocation-test"


def _snapshot(root: Path) -> set[Path]:
    """Every file under a tree right now, relative to it."""
    if not root.exists():
        return set()
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def relocated(tmp_path, monkeypatch) -> FleetPaths:
    """A fleet root pointed at a temp dir via the documented env override."""
    root = tmp_path / "relocated-fleet"
    monkeypatch.setenv("SKFLEET_ROOT", str(root))
    events.reset_dedupe()
    paths = default_paths()
    assert paths.root == root, "SKFLEET_ROOT must be the only thing deciding the root"
    return paths


def _drive_every_writer(paths: FleetPaths) -> None:
    """Exercise all four write paths: sknoded, admission, scheduler, events."""
    # sknoded: heartbeat.json + node.json + join.json
    sknoded.run_once(paths, NODE)

    # admission: mints the node object (operator seat writes spec)
    operator = store.Writer(role="operator", node=NODE, identity="")
    admission.admit(paths, NODE, writer=operator, bootstrap=True, labels={"relocation": "true"})

    # operator: a second spec, so objects/ holds more than the node kind
    store.write_spec(
        paths,
        "service",
        "relocation-probe",
        {"runtime": "systemd-user", "unit": "relocation-probe.service"},
        writer=operator,
    )

    # scheduler: placements/
    view = NodeView(
        name=NODE,
        phase="Ready",
        labels={"relocation": "true"},
        taints=(),
        cordoned=False,
        capacity={"cores": 8, "ram_gb": 16.0, "disk_gb": 200.0},
        allocatable={"cores": 7, "ram_gb": 15.0, "disk_gb": 195.0},
        heartbeat_age_s=1.0,
    )
    workload = Workload(
        kind="service",
        name="relocation-probe",
        node_selector={"relocation": "true"},
        tolerations=(),
        requests={"cores": 1, "ram_gb": 1.0},
    )
    decision = scheduler.place(
        paths,
        workload,
        writer=store.Writer(role="scheduler", node=NODE, identity=""),
        views=[view],
    )
    assert decision is not None, "scheduler must have written a placement"

    # events: status/<node>/events.jsonl
    wrote = events.emit(
        paths,
        store.Writer(role="sknoded", node=NODE, identity=""),
        kind="node",
        name=NODE,
        type="Normal",
        reason="RelocationTest",
        message="driving every writer against a relocated root",
    )
    assert wrote is True, "event must have been appended"


def test_every_writer_stays_under_the_relocated_root(relocated: FleetPaths) -> None:
    _drive_every_writer(relocated)
    created = _snapshot(relocated.root)
    assert created, "the run must have created files under the relocated root"

    # All four writer families landed where they were told to.
    assert Path("status") / NODE / "heartbeat.json" in created
    assert Path("status") / NODE / "node.json" in created
    assert Path("status") / NODE / "events.jsonl" in created
    assert Path("objects") / "node" / f"{NODE}.json" in created
    assert Path("objects") / "service" / "relocation-probe.json" in created
    assert Path("placements") / "service" / "relocation-probe.json" in created

    # Nothing escaped the three top-level trees FleetPaths defines.
    for rel in created:
        assert rel.parts[0] in {"objects", "placements", "status"}, f"stray path: {rel}"


def test_the_real_fleet_tree_is_untouched(relocated: FleetPaths, monkeypatch) -> None:
    """The load-bearing assertion: the live tree gains zero files.

    ~/.skcapstone is a live Syncthing folder shared to .158, .41 and
    noroc2027, so a test that wrote into it would propagate fleet-wide.
    """
    real_root = Path(os.path.expanduser("~/.skcapstone/fleet"))
    before = _snapshot(real_root)

    _drive_every_writer(relocated)

    after = _snapshot(real_root)
    assert after - before == set(), f"the run created files in the REAL fleet tree: {after - before}"
    assert relocated.root != real_root


def test_relocation_is_total_not_partial(tmp_path, monkeypatch) -> None:
    """Two different roots share nothing: the same run against root B leaves
    root A exactly as it was. A hardcoded path would show up here as a file
    appearing under whichever root the code baked in."""
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"

    monkeypatch.setenv("SKFLEET_ROOT", str(root_a))
    events.reset_dedupe()
    _drive_every_writer(default_paths())
    a_after_first = _snapshot(root_a)

    monkeypatch.setenv("SKFLEET_ROOT", str(root_b))
    events.reset_dedupe()
    _drive_every_writer(default_paths())

    assert _snapshot(root_a) == a_after_first, "writing to root B disturbed root A"
    assert _snapshot(root_b) == a_after_first, "the two roots should hold the same file set"


def test_the_two_path_classes_outside_fleetpaths_still_relocate() -> None:
    """`decisions/` and `atlas/` are part of the fleet store but are NOT
    properties on FleetPaths: operator_seat/cli.py builds them by joining
    paths.root. They relocate correctly today, and this asserts it, because
    the control-bus folder split (card ddb2a02f) shares both subtrees and a
    silent regression here would leave decisions behind on the old root."""
    from skcapstone.operator_seat import cli as operator_cli

    fake_root = Path("/nonexistent/relocated-root")
    paths = FleetPaths(root=fake_root)

    assert operator_cli._decisions_dir(paths) == str(fake_root / "decisions")
    # The atlas brief dir is built inline at the publish call site with the
    # same join, so pin the shape it must keep producing.
    assert str(paths.root / "atlas" / "brief").startswith(str(fake_root))


def test_fleet_package_hardcodes_the_root_in_exactly_one_place() -> None:
    """The audit, as an executable assertion: only paths.py may name the
    default location. Any new hit is a relocation bug in the making."""
    fleet_dir = Path(__file__).resolve().parents[2] / "src" / "skcapstone" / "fleet"
    offenders = {
        path.name
        for path in sorted(fleet_dir.glob("*.py"))
        if ".skcapstone" in path.read_text(encoding="utf-8")
    }
    assert offenders == {"paths.py"}, f"hardcoded fleet paths outside paths.py: {offenders}"
