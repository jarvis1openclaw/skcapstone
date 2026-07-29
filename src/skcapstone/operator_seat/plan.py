"""Operator decision layer: turn proposals into dispositioned, classified plans.

Pure. For each proposal the operator reasoned out, this enriches it with the
action catalog's metadata, classifies it (policy), and decides auto-apply vs
escalate-for-approval. No actuation and no I/O; the loop parks the escalations
and (only when execution is explicitly enabled) applies the auto ones.
"""

from __future__ import annotations

from typing import Any

from .policy import classify_change


def plan_actions(
    proposals: list[dict], explain: dict, *, author: str = "operator"
) -> list[dict[str, Any]]:
    """Classify and dispose each proposal.

    Returns a list of {proposal, classification, disposition} where disposition
    is 'auto' (auto_approvable) or 'escalate' (needs a human). An action not in
    the app's catalog is treated as unknown metadata and, lacking a standard
    claim or reversibility, will not be auto_approvable.
    """
    catalog = {a["name"]: a for a in explain.get("actions", [])}
    planned: list[dict[str, Any]] = []
    for p in proposals:
        meta = catalog.get(p.get("action"), {})
        reversible = bool(meta.get("reversible", False))
        action = {
            "name": p.get("action"),
            "standard": bool(meta.get("standard")),
            "blast_radius": meta.get("blast_radius"),
            "risk": "low" if reversible else "high",
            "rollback_plan": "revert via controller reconcile" if reversible else "",
            "author": author,
        }
        classification = classify_change(action)
        planned.append(
            {
                "proposal": p,
                "classification": classification,
                "disposition": "auto" if classification["auto_approvable"] else "escalate",
            }
        )
    return planned
