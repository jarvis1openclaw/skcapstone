"""Folded amendment helpers for coordination cards (card e78fd954).

Birth facts (``core.json`` priority / acceptance_criteria) are write-once.
These helpers amend them the way ``coord describe`` amends the title: an
appended, writer-attributed event that the fold applies on read, so the
edit is reversible by re-applying and the original stays visible in
``core.json``.

- ``reprioritize`` rides the existing ``set_priority`` overlay action, which
  both folds (legacy ``fold_overlay`` and the CardStore fold via
  ``load_legacy_mutations``) already understand.
- ``amend_criteria`` has no fold action upstream (the fold lives in the
  skcoord package), so the event is appended to the card's own store log and
  folded skcapstone-side by :func:`current_acceptance_criteria`. The
  upstream fold ignores the unknown action harmlessly.
"""

from __future__ import annotations

from pathlib import Path

from .card import CardEvent, CardEventLog
from .card_store import CardStore, card_store_write_enabled

VALID_PRIORITIES = ("critical", "high", "medium", "low")


def reprioritize(home: Path, task_id: str, priority: str, agent: str = "") -> None:
    """Append a folded priority amendment for a card.

    Writes the sanctioned ``set_priority`` overlay event and, when CardStore
    dual-write is enabled, mirrors it as a store ``priority`` event (the same
    belt-and-suspenders pattern as ``coord describe``).

    Args:
        home: Shared skcapstone root (``~/.skcapstone``).
        task_id: The card/task ID to amend.
        priority: New priority (one of :data:`VALID_PRIORITIES`).
        agent: Writer attribution (empty defaults to the host).

    Raises:
        ValueError: If ``priority`` is not a valid priority.
    """
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority '{priority}' (expected one of {VALID_PRIORITIES})")
    home = Path(home).expanduser()
    CardEventLog(home).append(
        CardEvent(card_id=task_id, action="set_priority", priority=priority, writer=agent)
    )
    if card_store_write_enabled():
        CardStore(home).append_event(task_id, "priority", agent or "mcp", priority=priority)


def amend_criteria(home: Path, task_id: str, criteria: list[str], agent: str = "") -> None:
    """Replace a card's folded acceptance criteria with an appended event.

    The write-once ``core.json`` keeps the original list; the fold applies
    the latest ``amend_criteria`` event on top. Reversed by amending again
    (the original list is always readable from ``core.json``).

    Args:
        home: Shared skcapstone root (``~/.skcapstone``).
        task_id: The card/task ID to amend.
        criteria: The full replacement criteria list (last event wins).
        agent: Writer attribution (empty defaults to ``mcp`` in the store log).

    Raises:
        ValueError: If ``criteria`` is empty.
    """
    if not criteria:
        raise ValueError("at least one criterion is required")
    home = Path(home).expanduser()
    CardStore(home).append_event(
        task_id, "amend_criteria", agent or "mcp", criteria=list(criteria)
    )


def _base_acceptance_criteria(home: Path, task_id: str) -> list[str]:
    """Return the birth-fact criteria from core.json, else the legacy task file."""
    core = CardStore(home)._load_core(task_id)
    if core is not None:
        return list(core.get("acceptance_criteria", []) or [])
    from .coordination import Board

    for task in Board(home).load_tasks(include_archived=True):
        if task.id == task_id:
            return list(task.acceptance_criteria)
    return []


def current_acceptance_criteria(home: Path, task_id: str) -> list[str]:
    """Fold a card's acceptance criteria: birth facts plus amendments.

    The base list comes from the immutable ``core.json`` (falling back to
    the legacy task file for cards never mirrored into the store); every
    ``amend_criteria`` event then replaces it, latest event winning.

    Args:
        home: Shared skcapstone root (``~/.skcapstone``).
        task_id: The card/task ID to fold.

    Returns:
        list[str]: The current (amended) acceptance criteria.
    """
    home = Path(home).expanduser()
    criteria = _base_acceptance_criteria(home, task_id)
    for event in CardStore(home)._read_events(task_id):
        if event.get("action") == "amend_criteria" and isinstance(event.get("criteria"), list):
            criteria = list(event["criteria"])
    return criteria
