"""Shared capacity domains for the Qwen admission envelope (SKL-S3-10).

Direct and SKGateway Qwen paths share one capacity domain so the same served
model cannot be double-counted across aliases. The default envelope is four
active and four queued requests with a 30 second queue deadline. A domain is
identified by identifier, not by route: every route that names the same
``capacity_domain_id`` draws from the same slots. A ninth in-flight request
is rejected outright.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from .errors import (
    CapacityDomainNotFoundError,
    CapacityQueueTimeoutError,
    ProviderSaturationError,
)


@dataclass(frozen=True, slots=True)
class CapacityEnvelope:
    """Bounded admission envelope for one capacity domain."""

    max_active: int
    max_queued: int
    queue_wait_seconds: float


DEFAULT_QWEN_ENVELOPE = CapacityEnvelope(
    max_active=4, max_queued=4, queue_wait_seconds=30.0
)
DEFAULT_QWEN_CAPACITY_DOMAIN_ID = "qwen.chiap08.shared.v1"


@dataclass(slots=True)
class _Ticket:
    """One request's standing inside a domain."""

    state: _DomainState
    event: threading.Event = field(default_factory=threading.Event)
    holds_slot: bool = False
    in_queue: bool = False
    settled: bool = False


@dataclass(slots=True)
class _DomainState:
    envelope: CapacityEnvelope
    active: int = 0
    queue: deque[_Ticket] = field(default_factory=deque)


class CapacityDomainController:
    """Thread-safe shared admission across transport aliases of one backend.

    Slots are held by capacity domain, never by route or transport, so a
    direct Qwen call and an SKGateway-routed Qwen call contend for the same
    four active slots. ``admission`` yields a mutable evidence dict with
    ``queued`` and ``saturation`` flags so the caller can record observed
    saturation without a second synchronization pass.
    """

    def __init__(self, envelopes: dict[str, CapacityEnvelope] | None = None) -> None:
        self._lock = threading.Lock()
        self._domains: dict[str, _DomainState] = {
            domain_id: _DomainState(envelope=envelope)
            for domain_id, envelope in (envelopes or {}).items()
        }

    @property
    def domain_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._domains))

    def register(self, domain_id: str, envelope: CapacityEnvelope) -> None:
        with self._lock:
            self._domains[domain_id] = _DomainState(envelope=envelope)

    def _state_for(self, domain_id: str) -> _DomainState:
        state = self._domains.get(domain_id)
        if state is None:
            raise CapacityDomainNotFoundError(
                f"capacity domain is not registered: {domain_id}"
            )
        return state

    def stats(self, domain_id: str) -> tuple[int, int]:
        """Return (active, queued) counts for one domain."""

        with self._lock:
            state = self._state_for(domain_id)
            return state.active, len(state.queue)

    def _enter(self, domain_id: str, ticket: _Ticket, evidence: dict[str, bool]) -> None:
        with self._lock:
            state = self._state_for(domain_id)
            if state.active < state.envelope.max_active:
                state.active += 1
                ticket.holds_slot = True
                return
            if len(state.queue) < state.envelope.max_queued:
                state.queue.append(ticket)
                ticket.in_queue = True
                evidence["queued"] = True
                evidence["saturation"] = True
                return
            total = state.active + len(state.queue)
            raise ProviderSaturationError(
                f"capacity domain {domain_id} is saturated: {total} of "
                f"{state.envelope.max_active + state.envelope.max_queued} "
                "requests active or queued"
            )

    def _settle(self, ticket: _Ticket) -> None:
        """Release the ticket's standing, promoting the next queued waiter."""

        if ticket.settled:
            return
        ticket.settled = True
        state = ticket.state
        with self._lock:
            if ticket.in_queue:
                try:
                    state.queue.remove(ticket)
                except ValueError:
                    pass
                ticket.in_queue = False
                return
            if not ticket.holds_slot:
                return
            if state.queue:
                successor = state.queue.popleft()
                successor.in_queue = False
                successor.holds_slot = True
                successor.event.set()
                return
            state.active = max(0, state.active - 1)

    @contextmanager
    def admission(
        self,
        domain_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> Iterator[dict[str, bool]]:
        evidence: dict[str, bool] = {"queued": False, "saturation": False}
        state = self._state_for(domain_id)
        ticket = _Ticket(state=state)
        self._enter(domain_id, ticket, evidence)
        try:
            if ticket.in_queue:
                deadline = clock() + state.envelope.queue_wait_seconds
                while not ticket.event.wait(timeout=0.05):
                    if clock() >= deadline:
                        raise CapacityQueueTimeoutError(
                            f"capacity domain {domain_id} queue wait exceeded "
                            f"{state.envelope.queue_wait_seconds}s"
                        )
            yield evidence
        finally:
            self._settle(ticket)
