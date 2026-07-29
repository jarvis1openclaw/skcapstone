"""ITIL change-record intent builder for the operator seat (Seat O1b).

Pure mapping from a proposed action plus its O1a classification into an ITIL
change payload dict. No ITIL or database writes; a reviewed card wires the
actual create. PURE, hands-off.
"""

from __future__ import annotations

from typing import Any, Optional


def build_change_record(
    action: dict[str, Any],
    classification: dict[str, Any],
    dry_run: str,
    rollback_plan: Optional[str],
) -> dict[str, Any]:
    """Build an ITIL change-record payload for an operator action.

    Args:
        action: Proposed action metadata, as passed to
            :func:`skcapstone.operator_seat.policy.classify_change`.
            Recognized key: ``name`` (str), used in the title and
            description.
        classification: The O1a classification result, with
            ``change_class``, ``risk``, and ``auto_approvable`` keys.
        dry_run: Dry run indicator carried through into the payload
            unmodified.
        rollback_plan: Rollback plan text carried through into the payload
            unmodified, or None if no rollback plan is attached.

    Returns:
        A dict with ``title``, ``description``, ``change_class``, ``risk``,
        ``dry_run``, ``rollback_plan``, ``tags``, ``author``, and
        ``requires_human`` keys.
    """
    name = action.get("name", "unspecified")
    change_class = classification["change_class"]
    risk = classification["risk"]
    auto_approvable = classification["auto_approvable"]

    tags = ["operator"]
    if change_class == "normal" and auto_approvable:
        tags.append("auto-normal")

    return {
        "title": f"Operator change: {name}",
        "description": (
            f"Operator-initiated {change_class} change '{name}' "
            f"(risk: {risk}, dry_run: {dry_run})."
        ),
        "change_class": change_class,
        "risk": risk,
        "dry_run": dry_run,
        "rollback_plan": rollback_plan,
        "tags": tags,
        "author": "operator",
        "requires_human": not auto_approvable,
    }
