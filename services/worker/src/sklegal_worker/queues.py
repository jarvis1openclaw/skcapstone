"""Task queue names, queue selection, and long-context admission.

Four queues separate interactive matter work, batch imports and
reconciliation, long-context model analysis, and connector dispatch so a
saturated queue never starves human-facing work.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AdmissionRejectedError
from .models import QueueKind

TASK_QUEUE_NAMES: dict[QueueKind, str] = {
    QueueKind.INTERACTIVE: "sklegal-interactive",
    QueueKind.BATCH: "sklegal-batch",
    QueueKind.LONG_CONTEXT: "sklegal-long-context",
    QueueKind.CONNECTOR: "sklegal-connector",
}

# Interactive work above this context budget must move to the long-context
# queue so large corpus bundles cannot block human-facing turns.
INTERACTIVE_CONTEXT_TOKEN_LIMIT = 16_000

# Bounded concurrency for long-context runs; derived stores stay within
# approved capacity budgets.
LONG_CONTEXT_MAX_CONCURRENT = 4


def task_queue_name(kind: QueueKind) -> str:
    return TASK_QUEUE_NAMES[kind]


def select_queue(kind: QueueKind, *, context_tokens: int) -> QueueKind:
    """Route a run to the queue that fits its context budget."""
    if context_tokens < 0:
        raise ValueError("context_tokens cannot be negative")
    if (
        kind is QueueKind.INTERACTIVE
        and context_tokens > INTERACTIVE_CONTEXT_TOKEN_LIMIT
    ):
        return QueueKind.LONG_CONTEXT
    return kind


@dataclass(frozen=True, slots=True)
class AdmissionTicket:
    """One held long-context slot."""

    run_key: str
    slot: int
    context_tokens: int


class LongContextAdmission:
    """Bounded, idempotent admission gate for the long-context queue.

    Acquiring twice with the same run key returns the original ticket so a
    retried admission never consumes a second slot. Distinct runs beyond
    capacity are rejected explicitly instead of queueing without bound.
    """

    def __init__(self, *, max_concurrent: int = LONG_CONTEXT_MAX_CONCURRENT) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least one")
        self._max_concurrent = max_concurrent
        self._tickets: dict[str, AdmissionTicket] = {}

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_count(self) -> int:
        return len(self._tickets)

    def acquire(self, run_key: str, *, context_tokens: int) -> AdmissionTicket:
        if not run_key:
            raise ValueError("run_key is required")
        if context_tokens < 0:
            raise ValueError("context_tokens cannot be negative")
        existing = self._tickets.get(run_key)
        if existing is not None:
            return existing
        if len(self._tickets) >= self._max_concurrent:
            raise AdmissionRejectedError(
                f"long-context admission full: {self._max_concurrent} runs active"
            )
        used_slots = {ticket.slot for ticket in self._tickets.values()}
        slot = next(
            candidate
            for candidate in range(self._max_concurrent)
            if candidate not in used_slots
        )
        ticket = AdmissionTicket(
            run_key=run_key,
            slot=slot,
            context_tokens=context_tokens,
        )
        self._tickets[run_key] = ticket
        return ticket

    def release(self, ticket: AdmissionTicket) -> bool:
        """Release a held ticket; returns False for unknown or stale tickets."""
        current = self._tickets.get(ticket.run_key)
        if current != ticket:
            return False
        del self._tickets[ticket.run_key]
        return True
