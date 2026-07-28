"""NodeController: derived node health and the cordon action (spec 5.1).

Runs on the control-plane node. It is the only component allowed to mark a
node schedulable or not; sknoded self-reports raw observations only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import store
from .paths import FleetPaths

NOT_READY_AFTER_S = 180
DEAD_AFTER_S = 300


@dataclass
class NodeView:
    """One row of the fleet inventory (skfleet nodes)."""

    name: str
    phase: str
    cordoned: bool = False
    labels: dict = field(default_factory=dict)
    taints: list = field(default_factory=list)
    capacity: dict = field(default_factory=dict)
    allocatable: dict = field(default_factory=dict)
    heartbeat_age_s: float | None = None
    conditions: list = field(default_factory=list)


def _heartbeat_age(paths: FleetPaths, node: str, now: datetime) -> float | None:
    beat = store.read_node_file(paths, node, "heartbeat.json")
    if not beat or "ts" not in beat:
        return None
    try:
        ts = datetime.strptime(beat["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (now - ts).total_seconds()


def _phase(age: float | None) -> str:
    if age is None or age > DEAD_AFTER_S:
        return "Dead"
    if age > NOT_READY_AFTER_S:
        return "NotReady"
    return "Ready"


def node_views(paths: FleetPaths, *, now: datetime | None = None) -> list[NodeView]:
    """All known nodes: admitted (from spec) plus Pending joiners."""
    now = now or datetime.now(timezone.utc)
    admitted = {s["name"]: s for s in store.list_specs(paths, "node")}
    names = set(admitted)
    if paths.status.exists():
        for node_dir in paths.status.iterdir():
            if node_dir.is_dir() and (node_dir / "join.json").exists():
                names.add(node_dir.name)
    views = []
    for name in sorted(names):
        report = store.read_node_file(paths, name, "node.json") or {}
        spec = admitted.get(name)
        age = _heartbeat_age(paths, name, now)
        views.append(
            NodeView(
                name=name,
                phase="Pending" if spec is None else _phase(age),
                cordoned=bool((spec or {}).get("spec", {}).get("cordoned")),
                labels=(spec or {}).get("labels", {}),
                taints=(spec or {}).get("spec", {}).get("taints", []),
                capacity=report.get("status", {}).get("capacity", {}),
                allocatable=(
                    report.get("status", {}).get("allocatable")
                    or report.get("status", {}).get("capacity", {})
                ),
                heartbeat_age_s=age,
                conditions=report.get("conditions", []),
            )
        )
    return views


def cordon(paths: FleetPaths, name: str, cordoned: bool, *, writer: store.Writer) -> dict:
    """Set or clear the cordon flag on a node spec (operator action)."""
    current = store.read_spec(paths, "node", name)
    if current is None:
        raise LookupError(f"no such node object: {name!r}")
    new_spec = dict(current.get("spec", {}), cordoned=cordoned)
    return store.write_spec(
        paths, "node", name, new_spec, writer=writer, labels=current.get("labels", {})
    )
