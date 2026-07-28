"""Scheduler v1: filter + least-loaded placement (spec section 7).

Stateless and idempotent: every pass recomputes from Node views and a
workload's requirements, and placement writes are write-on-change. No
preference or affinity scoring in v1 (deferred, gated Card 2.1b); a
PreferNoSchedule taint is advisory only, recorded in the placement reason.
The scheduler writes ONLY placements: never status, never spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import events, store
from .node_controller import NodeView, node_views
from .paths import FleetPaths

DEFAULT_REQUESTS: dict = {"cores": 1, "ram_gb": 2.0}


@dataclass(frozen=True)
class Workload:
    """What the scheduler needs to know about one schedulable unit.

    Attributes:
        kind: Fleet kind ("job" for autopilot cards, "service" later).
        name: Object name (a card id for jobs).
        node_selector: Exact-match label AND map (autopilot --tag semantics).
        tolerations: Tuple of {"key", optional "value"}; key-only tolerates
            any value of that taint key.
        requests: Requested resources checked against Node allocatable.
    """

    kind: str
    name: str
    node_selector: dict = field(default_factory=dict)
    tolerations: tuple = ()
    requests: dict = field(default_factory=lambda: dict(DEFAULT_REQUESTS))


def _tolerated(taint: dict, tolerations: tuple) -> bool:
    for tol in tolerations:
        if tol.get("key") != taint.get("key"):
            continue
        if "value" not in tol or tol.get("value") == taint.get("value"):
            return True
    return False


def feasible(view: NodeView, workload: Workload) -> str | None:
    """None when the node passes every v1 filter, else the exclusion reason.

    Filters (spec section 7 step 1): Ready phase, not cordoned, selector
    match, all NoSchedule taints tolerated, allocatable headroom.
    """
    if view.phase != "Ready":
        return f"not Ready (phase={view.phase})"
    if view.cordoned:
        return "cordoned"
    for key, value in sorted(workload.node_selector.items()):
        if view.labels.get(key) != value:
            return f"selector mismatch ({key}={value})"
    for taint in view.taints:
        if taint.get("effect") == "NoSchedule" and not _tolerated(taint, workload.tolerations):
            return f"untolerated NoSchedule taint {taint.get('key')}={taint.get('value')}"
    need_cores = float(workload.requests.get("cores", 0))
    need_ram = float(workload.requests.get("ram_gb", 0.0))
    if (
        float(view.allocatable.get("cores", 0)) < need_cores
        or float(view.allocatable.get("ram_gb", 0.0)) < need_ram
    ):
        return f"insufficient headroom (need cores>={need_cores:g}, ram_gb>={need_ram})"
    return None


@dataclass(frozen=True)
class Decision:
    """One scheduling decision with its full audit trail.

    Attributes:
        node: Chosen node name, or None when unschedulable.
        reason: One-line human reason (surfaced by skfleet placements).
        excluded: Per-node filter reason for every excluded candidate.
    """

    node: str | None
    reason: str
    excluded: dict = field(default_factory=dict)


def _headroom_key(view: NodeView) -> tuple:
    """Sort key: most allocatable RAM, then cores, then lexicographic name."""
    return (
        -float(view.allocatable.get("ram_gb", 0.0)),
        -float(view.allocatable.get("cores", 0)),
        view.name,
    )


def select(views: list[NodeView], workload: Workload) -> Decision:
    """Filter (feasible) then pick the least-loaded survivor (spec 7 step 2).

    v1 has no preference scoring: a PreferNoSchedule taint on the chosen
    node is recorded in the reason, never acted on.
    """
    excluded: dict = {}
    candidates: list[NodeView] = []
    for view in views:
        why = feasible(view, workload)
        if why is None:
            candidates.append(view)
        else:
            excluded[view.name] = why
    if not candidates:
        detail = "; ".join(f"{n}: {w}" for n, w in sorted(excluded.items()))
        return Decision(node=None, reason=f"unschedulable ({detail})", excluded=excluded)
    chosen = sorted(candidates, key=_headroom_key)[0]
    reason = (
        f"least-loaded: {chosen.name} allocatable "
        f"ram={chosen.allocatable.get('ram_gb')}GB "
        f"cores={chosen.allocatable.get('cores')} "
        f"of {len(candidates)} candidate(s)"
    )
    for taint in chosen.taints:
        if taint.get("effect") == "PreferNoSchedule":
            reason += (
                f"; advisory: PreferNoSchedule taint "
                f"{taint.get('key')}={taint.get('value')} not scored in v1"
            )
    return Decision(node=chosen.name, reason=reason, excluded=excluded)


def place(
    paths: FleetPaths,
    workload: Workload,
    *,
    writer: store.Writer,
    views: list[NodeView] | None = None,
) -> dict | None:
    """Decide and record one placement (level-triggered, idempotent).

    Honors the freeze flag: a frozen tree gets no placement writes (spec
    section 8, guardrail 2). Emits one Placement event per CHANGED decision
    (the Card 2.3 audit trail; unchanged re-runs stay silent, R2).

    Returns:
        The placement payload as on disk, or None when frozen or
        unschedulable (nothing is written in either case).
    """
    if not store.actuation_allowed(paths):
        return None
    views = node_views(paths) if views is None else views
    decision = select(views, workload)
    if decision.node is None:
        return None
    payload, changed = store.write_placement(
        paths,
        workload.kind,
        workload.name,
        node=decision.node,
        reason=decision.reason,
        writer=writer,
    )
    if changed:
        events.emit(
            paths,
            writer,
            kind=workload.kind,
            name=workload.name,
            type="Placement",
            reason="Placed",
            message=decision.reason,
        )
    return payload
