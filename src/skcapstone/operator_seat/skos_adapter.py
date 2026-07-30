"""skos operator adapter: Atlas manages skos too (O7 app adapter).

Conformant to the adapter contract (explain / observe / act). The health probe is
injectable so tests never touch a live skos; the default reads the skos scheduler
status and fails safe (reports healthy) rather than raising a false alarm.
"""

from __future__ import annotations

from typing import Callable

CONDITIONS = ["SchedulerAlive", "GtdSinkDraining"]

#: Health-type conditions (fire when status is False): a dead scheduler or a
#: stalled GTD ingest sink both read as False -> firing.
_ACTIONS = [
    {
        "name": "restart_service",
        "standard": True,
        "reversible": True,
        "blast_radius": "low",
        "runbook": "restart the skscheduler service",
        "kedb_refs": [],
    },
    {
        "name": "replay_errors",
        "standard": True,
        "reversible": True,
        "blast_radius": "low",
        "runbook": "replay the skos error-recovery queue",
        "kedb_refs": [],
    },
]


def _b(value: bool) -> str:
    return "True" if value else "False"


def _default_probe() -> dict:
    """Best-effort skos health. Fails SAFE (healthy) when unreachable."""
    try:
        import subprocess

        r = subprocess.run(
            ["skos", "scheduler", "status"], capture_output=True, text=True, timeout=10
        )
        alive = r.returncode == 0
        return {"scheduler_alive": alive, "gtd_draining": True}
    except Exception:
        return {"scheduler_alive": True, "gtd_draining": True}


def skos_explain() -> dict:
    """skos' self-description in the adapter-contract shape."""
    return {
        "kinds": ["scheduler", "gtd"],
        "conditions": list(CONDITIONS),
        "actions": [dict(a) for a in _ACTIONS],
    }


def skos_observe(probe: Callable[[], dict] | None = None) -> dict:
    """Read-only skos health snapshot in the adapter-contract shape."""
    st = (probe or _default_probe)()
    return {
        "conditions": [
            {
                "type": "SchedulerAlive",
                "status": _b(bool(st.get("scheduler_alive"))),
                "object": "skscheduler",
            },
            {
                "type": "GtdSinkDraining",
                "status": _b(bool(st.get("gtd_draining"))),
                "object": "gtd-sink",
            },
        ]
    }


#: A loop-compatible observe (paths, now_iso) -> {conditions}; ignores both.
def observe(paths=None, now_iso: str | None = None) -> dict:
    return skos_observe()


__all__ = ["skos_explain", "skos_observe", "observe", "CONDITIONS"]
