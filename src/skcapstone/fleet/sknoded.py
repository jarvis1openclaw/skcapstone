"""sknoded v1: the per-node self-report loop (spec section 6, step 1).

Phase 1 is report-only: heartbeat + node.json + join request. Actuation
arrives in Phase 3 and will gate on store.actuation_allowed().
"""

from __future__ import annotations

import platform
import socket
import time
from datetime import datetime, timezone

from .. import __version__ as skcapstone_version
from . import store
from .capacity import allocatable, node_capacity
from .conditions import merge_transitions, node_conditions, probe_conditions
from .paths import FleetPaths

HEARTBEAT_INTERVAL_S = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_heartbeat(node: str, now_iso: str) -> dict:
    """The one small heartbeat file, overwritten in place (R2)."""
    return {"kind": "Node", "name": node, "node": node, "ts": now_iso}


def build_node_report(paths: FleetPaths, node: str, now_iso: str) -> dict:
    """Capacity + conditions + versions, with stable lastTransition."""
    cap = node_capacity()
    spec = store.read_spec(paths, "node", node)
    conds = node_conditions(cap, paths.root, now_iso)
    probes = (spec or {}).get("spec", {}).get("healthProbes", [])
    conds.extend(probe_conditions(probes, now_iso))
    previous = store.read_node_file(paths, node, "node.json") or {}
    conds = merge_transitions(conds, previous.get("conditions", []))
    return {
        "kind": "Node",
        "name": node,
        "node": node,
        "observedGeneration": int(spec["generation"]) if spec else 0,
        "status": {
            "capacity": cap,
            "allocatable": allocatable(cap),
            "versions": {
                "python": platform.python_version(),
                "skcapstone": skcapstone_version,
            },
        },
        "conditions": conds,
    }


def build_join_request(paths: FleetPaths, node: str, capacity: dict, now_iso: str) -> dict:
    """Join marker for admission (spec section 9)."""
    return {
        "name": node,
        "addresses": {"hostname": socket.gethostname()},
        "capacity": capacity,
        "identity": store.writer_identity(),
        "requestedAt": now_iso,
    }


def run_once(paths: FleetPaths, node: str) -> dict:
    """One self-report pass. Returns which files were actually written."""
    now_iso = _now_iso()
    writer = store.Writer(role="sknoded", node=node, identity=store.writer_identity())
    heartbeat = store.write_node_file(
        paths, writer, "heartbeat.json", build_heartbeat(node, now_iso), if_changed=False
    )
    report = build_node_report(paths, node, now_iso)
    node_written = store.write_node_file(paths, writer, "node.json", report)
    join_written = False
    unadmitted = store.read_spec(paths, "node", node) is None
    if unadmitted and store.read_node_file(paths, node, "join.json") is None:
        join = build_join_request(paths, node, report["status"]["capacity"], now_iso)
        join_written = store.write_node_file(paths, writer, "join.json", join, if_changed=False)
    return {"heartbeat": heartbeat, "node": node_written, "join": join_written}


def main_loop(
    paths: FleetPaths,
    node: str,
    *,
    interval: int = HEARTBEAT_INTERVAL_S,
    once: bool = False,
    actuation_interval: int | None = None,
) -> None:
    """The daemon loop behind sknoded.service.

    Self-report runs every `interval` seconds; the Phase 3 converge pass
    runs every `actuation_interval` seconds (default 30, spec 3.3). The
    converge pass re-reads the freeze flag and the node's actuate opt-in
    every time, so both are live level-triggered gates.
    """
    from .converge import ACTUATION_INTERVAL_S, converge_once

    act_every = ACTUATION_INTERVAL_S if actuation_interval is None else actuation_interval
    last_report = 0.0
    while True:
        now = time.time()
        if now - last_report >= interval or last_report == 0.0:
            run_once(paths, node)
            last_report = now
        converge_once(paths, node)
        if once:
            return
        time.sleep(act_every)
