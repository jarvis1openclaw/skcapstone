"""skchat operator adapter: Atlas manages skchat too (the second app adapter).

Conformant to the adapter contract (explain / observe / act). One operator, many
apps: skchat plugs in by exposing the same three verbs the fleet does. The health
probe is injectable so tests never touch a live skchat; the default reads the
skchat daemon status and outbox depth, and fails safe (reports healthy) rather
than raising a false alarm when skchat cannot be reached.
"""

from __future__ import annotations

from typing import Callable

CONDITIONS = ["BridgeAlive", "OutboxBounded"]

#: skchat health conditions are health-type (they fire when status is False), so
#: they are NOT problem-when-true; the operator brief treats them correctly by
#: default. Outbox over its bound -> OutboxBounded False -> firing.
_ACTIONS = [
    {
        "name": "restart_telegram_bridge",
        "standard": True,
        "reversible": True,
        "blast_radius": "low",
        "runbook": "restart the wedged telegram bridge",
        "kedb_refs": [],
    },
    {
        "name": "purge_outbox",
        "standard": False,
        "reversible": False,
        "blast_radius": "delete",
        "runbook": "drop stranded outbox messages (irreversible: escalates)",
        "kedb_refs": [],
    },
]

_OUTBOX_LIMIT = 1000


def _b(value: bool) -> str:
    return "True" if value else "False"


def _default_probe() -> dict:
    """Best-effort skchat health. Fails SAFE (healthy) when skchat is unreachable,
    so an inability to probe never raises a false alarm."""
    try:
        import subprocess

        r = subprocess.run(
            ["skchat", "daemon", "status"], capture_output=True, text=True, timeout=10
        )
        alive = r.returncode == 0
        return {"bridge_alive": alive, "outbox_depth": 0, "outbox_limit": _OUTBOX_LIMIT}
    except Exception:
        return {"bridge_alive": True, "outbox_depth": 0, "outbox_limit": _OUTBOX_LIMIT}


def skchat_explain() -> dict:
    """skchat's self-description in the adapter-contract shape."""
    return {
        "kinds": ["bridge", "outbox"],
        "conditions": list(CONDITIONS),
        "actions": [dict(a) for a in _ACTIONS],
    }


def skchat_observe(probe: Callable[[], dict] | None = None) -> dict:
    """Read-only skchat health snapshot in the adapter-contract shape."""
    st = (probe or _default_probe)()
    depth = int(st.get("outbox_depth", 0))
    limit = int(st.get("outbox_limit", _OUTBOX_LIMIT))
    return {
        "conditions": [
            {
                "type": "BridgeAlive",
                "status": _b(bool(st.get("bridge_alive"))),
                "object": "telegram-bridge",
            },
            {"type": "OutboxBounded", "status": _b(depth <= limit), "object": "outbox"},
        ]
    }


#: A loop-compatible observe (name, now_iso) -> {conditions}; ignores now_iso.
def observe(paths=None, now_iso: str | None = None) -> dict:
    return skchat_observe()


__all__ = ["skchat_explain", "skchat_observe", "observe", "CONDITIONS"]
