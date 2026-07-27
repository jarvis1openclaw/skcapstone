"""Self-describing surface (spec section 8): kinds, fields, actions.

A fresh AI operator discovers the system from this registry at runtime
instead of via hardcoding. Phase 1 registers Node; each later phase adds
its kind here as part of shipping it.
"""

from __future__ import annotations

KINDS: dict[str, dict] = {
    "node": {
        "kind": "Node",
        "description": "A machine in the fleet.",
        "spec": {
            "labels": "label map used by selectors (exact match, AND)",
            "taints": "list of {key, value, effect: NoSchedule|PreferNoSchedule}",
            "cordoned": "bool; excluded from scheduling when true",
            "capacityOverrides": "optional manual capacity caps",
            "address": "LAN + tailscale addresses, ssh target",
        },
        "status": {
            "capacity": "cores, ram_gb, disk_gb, gpu, vram_gb (autoscale probe)",
            "conditions": "list of {type, status, reason, message, lastTransition}",
            "versions": "python + skcapstone versions on the node",
        },
        "conditions": {
            "Ready": "sknoded self-report is alive",
            "MemoryPressure": "free RAM below threshold",
            "DiskPressure": "free disk below threshold",
            "GPUAvailable": "a GPU is present and probed",
            "SyncConflict": "sync-conflict files under the fleet tree (ownership bug)",
        },
        "actions": [
            "skfleet nodes",
            "skfleet describe node <name>",
            "skfleet cordon <name>",
            "skfleet uncordon <name>",
            "skfleet admit <name>",
        ],
    },
}


def explain(kind: str | None = None) -> dict:
    """Describe registered kinds, or one kind in detail."""
    if kind is None:
        return {"kinds": sorted(KINDS)}
    if kind not in KINDS:
        raise KeyError(f"unknown kind: {kind!r} (known: {sorted(KINDS)})")
    return KINDS[kind]
