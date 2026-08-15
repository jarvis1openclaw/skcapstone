"""NodeController: derived node health and the cordon action (spec 5.1).

Runs on the control-plane node. It is the only component allowed to mark a
node schedulable or not; sknoded self-reports raw observations only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import store
from .paths import FleetPaths, valid_name

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
    # The install profile this node is bound to (epic 3bbf39ea). "" means
    # unbound, which is the correct reading before every node is backfilled
    # and must never be an error: the doctor skips it, it does not fail.
    role: str = ""


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
                role=(spec or {}).get("spec", {}).get("role", "") or "",
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


def set_role(paths: FleetPaths, name: str, role: str, *, writer: store.Writer) -> dict:
    """Bind a node to an install profile by name (operator action).

    Mirrors set_actuation exactly: read the current spec, overlay one field,
    rewrite through store.write_spec preserving labels. Every other spec
    field (taints, cordoned, address, identity, actuate) survives, and the
    generation bumps by exactly one.

    Validation here is only that the role is a safe name. Whether a profile
    object of that name actually exists is the doctor's question (card
    cd5ef08b), deliberately: binding must not require the manifest to have
    landed first, or the two cards deadlock on each other.

    Raises:
        LookupError: no node object of that name.
        ValueError: role is not a valid object name.
    """
    if not valid_name(role):
        raise ValueError(f"invalid role name: {role!r}")
    current = store.read_spec(paths, "node", name)
    if current is None:
        raise LookupError(f"no such node object: {name!r}")
    new_spec = dict(current.get("spec", {}), role=role)
    return store.write_spec(
        paths, "node", name, new_spec, writer=writer, labels=current.get("labels", {})
    )


def set_actuation(paths: FleetPaths, name: str, enabled: bool, *, writer: store.Writer) -> dict:
    """Toggle the per-node actuation opt-in (operator action, spec R4).

    Every node is born report-only; this is the single explicit lever that
    lets sknoded on that node actuate. Preserves all other spec fields.
    """
    current = store.read_spec(paths, "node", name)
    if current is None:
        raise LookupError(f"no such node object: {name!r}")
    new_spec = dict(current.get("spec", {}), actuate=enabled)
    return store.write_spec(
        paths, "node", name, new_spec, writer=writer, labels=current.get("labels", {})
    )
