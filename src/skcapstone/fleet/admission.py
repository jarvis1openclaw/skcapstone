"""Self-enrollment and admission (spec section 9).

A fresh box self-reports a join request; admission mints its node object.
No hand-authored fleet files anywhere on the path from bare box to
managed fleet.
"""
from __future__ import annotations

from . import store
from .paths import FleetPaths

PRESETS: dict[str, dict] = {
    "node-158": {
        "labels": {"always-on": "true", "dev-primary": "true", "control-plane": "true"},
        "taints": [],
    },
    "node-41": {
        "labels": {"heavy-build": "true"},
        "taints": [],  # travel taint applied by runbook when the box travels
    },
    "node-100": {
        "labels": {"gpu": "true"},
        "taints": [{"key": "dedicated", "value": "model-serving", "effect": "NoSchedule"}],
    },
    "node-local": {
        "labels": {"interactive": "true"},
        "taints": [{"key": "interactive", "value": "true", "effect": "PreferNoSchedule"}],
    },
}


def pending_joins(paths: FleetPaths) -> list[dict]:
    """Join requests that do not yet have a node object, sorted by name."""
    out = []
    if not paths.status.exists():
        return out
    for node_dir in sorted(p for p in paths.status.iterdir() if p.is_dir()):
        join = store.read_node_file(paths, node_dir.name, "join.json")
        if join and store.read_spec(paths, "node", node_dir.name) is None:
            out.append(join)
    return out


def admit(
    paths: FleetPaths,
    node: str,
    *,
    writer: store.Writer,
    labels: dict | None = None,
    taints: list | None = None,
    preset: bool = False,
    bootstrap: bool = False,
) -> dict:
    """Mint the node object for a joiner (idempotent).

    Args:
        preset: pull labels/taints from PRESETS for the known four nodes.
        bootstrap: allow admitting without a join request (first node,
            spec section 9 cold-start step 3).
    Raises:
        LookupError: no join request and bootstrap not set.
    """
    existing = store.read_spec(paths, "node", node)
    if existing is not None:
        return existing
    join = store.read_node_file(paths, node, "join.json")
    if join is None and not bootstrap:
        raise LookupError(f"no join request for {node!r}; is sknoded running there?")
    if preset and node in PRESETS:
        labels = labels if labels is not None else PRESETS[node]["labels"]
        taints = taints if taints is not None else PRESETS[node]["taints"]
    spec = {
        "taints": taints or [],
        "cordoned": False,
        "address": (join or {}).get("addresses", {}),
        "identity": (join or {}).get("identity", ""),
    }
    return store.write_spec(paths, "node", node, spec, writer=writer,
                            labels=labels or {})


def auto_admit(paths: FleetPaths, trusted: set[str], *, writer: store.Writer) -> list[str]:
    """Admit pending joiners whose identity is already trusted (known-key)."""
    admitted = []
    for join in pending_joins(paths):
        identity = join.get("identity", "")
        if identity and identity in trusted:
            admit(paths, join["name"], writer=writer, preset=True)
            admitted.append(join["name"])
    return admitted
