"""Bounded corpus health reader backed only by the materialized registry.

The user-facing health path reads exactly one materialized registry snapshot
behind a hard time budget. It holds no corpus tree, scanner, or projection
backend port, so a full-tree scan on a health request is structurally
impossible here; deep coverage is reported separately by the scheduled
reconciliation job. Any budget breach, store outage, or invalid state fails
closed to an explicit unavailable view instead of hanging or fabricating
counts.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from .corpus_registry import (
    CorpusCountKind,
    CorpusCounts,
    CorpusRegistrySnapshot,
    CorpusRegistryStore,
    zero_corpus_counts,
)
from .models import ProjectionLag, RetrievalValue


class CorpusHealthStatus(StrEnum):
    """Closed vocabulary of corpus health outcomes."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CorpusHealthUnavailableReason(StrEnum):
    """Content-free reason a health read could not complete."""

    BUDGET_EXCEEDED = "budget_exceeded"
    STORE_UNAVAILABLE = "store_unavailable"
    INVALID_STATE = "invalid_state"


class CorpusHealthView(RetrievalValue):
    """Aggregate, content-free corpus health view for the bounded endpoint.

    Counts are aggregate-only across Tenants: the unauthenticated health
    surface never reveals per-Tenant or per-Matter detail. ``counts`` is
    ``None`` whenever the read failed closed so a caller can never mistake
    a failed read for an empty corpus.
    """

    status: CorpusHealthStatus
    budget_ms: int = Field(ge=1)
    counts: CorpusCounts | None = None
    entry_count: int = Field(ge=0)
    tenant_count: int = Field(ge=0)
    lag: ProjectionLag | None = None
    last_reconciled_at: datetime | None = None
    unavailable_reason: CorpusHealthUnavailableReason | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        available = self.status is not CorpusHealthStatus.UNAVAILABLE
        if available != (self.counts is not None):
            raise ValueError("corpus health counts shape disagrees with status")
        if available != (self.unavailable_reason is None):
            raise ValueError("corpus health unavailable reason shape disagrees")
        if self.last_reconciled_at is not None and (
            self.last_reconciled_at.tzinfo is None
        ):
            raise ValueError("reconciled time must be timezone aware")
        return self


class BoundedCorpusHealthReader:
    """Read the materialized registry snapshot inside a fixed time budget.

    The store read runs on a daemon worker thread joined for at most
    ``budget_ms``. A store that wedges never blocks the caller beyond the
    budget; the abandoned daemon thread cannot outlive the process. The
    reader accepts no inventory, filesystem, or backend dependency, so the
    health path cannot start a full-tree scan.
    """

    def __init__(
        self,
        store: CorpusRegistryStore,
        *,
        budget_ms: int,
        degraded_lag_events: int,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not callable(getattr(store, "snapshot", None)):
            raise ValueError("corpus health requires a materialized registry store")
        if not isinstance(budget_ms, int) or budget_ms < 1:
            raise ValueError("corpus health budget must be a positive integer")
        if not isinstance(degraded_lag_events, int) or degraded_lag_events < 0:
            raise ValueError("degraded lag threshold must be a non-negative integer")
        self._store = store
        self._budget_ms = budget_ms
        self._degraded_lag_events = degraded_lag_events
        self._monotonic = monotonic or time.monotonic

    @property
    def budget_ms(self) -> int:
        return self._budget_ms

    def read(self) -> CorpusHealthView:
        """Return the aggregate health view, failing closed inside the budget."""

        outcome: dict[str, object] = {}
        started = self._monotonic()

        def _load() -> None:
            try:
                outcome["snapshot"] = self._store.snapshot()
            except Exception:
                outcome["error"] = CorpusHealthUnavailableReason.STORE_UNAVAILABLE

        worker = threading.Thread(target=_load, daemon=True)
        worker.start()
        worker.join(timeout=self._budget_ms / 1000.0)
        if worker.is_alive():
            return self._unavailable(CorpusHealthUnavailableReason.BUDGET_EXCEEDED)
        if self._monotonic() - started > self._budget_ms / 1000.0:
            return self._unavailable(CorpusHealthUnavailableReason.BUDGET_EXCEEDED)
        reason = outcome.get("error")
        if isinstance(reason, CorpusHealthUnavailableReason):
            return self._unavailable(reason)
        snapshot = outcome.get("snapshot")
        if not isinstance(snapshot, CorpusRegistrySnapshot):
            return self._unavailable(CorpusHealthUnavailableReason.INVALID_STATE)
        return self._view(snapshot)

    def _view(self, snapshot: CorpusRegistrySnapshot) -> CorpusHealthView:
        totals = dict.fromkeys(CorpusCountKind, 0)
        lag_events = 0
        lag_seconds = 0.0
        reconciled: list[datetime] = []
        tenants: set[UUID] = set()
        never_reconciled = False
        for entry in snapshot.entries:
            for kind in CorpusCountKind:
                totals[kind] += entry.counts.count_for(kind)
            lag_events = max(lag_events, entry.lag.lag_events)
            lag_seconds = max(lag_seconds, entry.lag.lag_seconds)
            tenants.add(entry.tenant_id)
            if entry.last_reconciled_at is None:
                never_reconciled = True
            else:
                reconciled.append(entry.last_reconciled_at)
        counts = zero_corpus_counts()
        for kind in CorpusCountKind:
            counts = counts.with_count(kind, totals[kind])
        status = CorpusHealthStatus.HEALTHY
        if lag_events > self._degraded_lag_events or never_reconciled:
            status = CorpusHealthStatus.DEGRADED
        return CorpusHealthView(
            status=status,
            budget_ms=self._budget_ms,
            counts=counts,
            entry_count=len(snapshot.entries),
            tenant_count=len(tenants),
            lag=ProjectionLag(lag_events=lag_events, lag_seconds=lag_seconds),
            last_reconciled_at=min(reconciled) if reconciled else None,
        )

    def _unavailable(self, reason: CorpusHealthUnavailableReason) -> CorpusHealthView:
        return CorpusHealthView(
            status=CorpusHealthStatus.UNAVAILABLE,
            budget_ms=self._budget_ms,
            counts=None,
            entry_count=0,
            tenant_count=0,
            lag=None,
            last_reconciled_at=None,
            unavailable_reason=reason,
        )


__all__ = [
    "BoundedCorpusHealthReader",
    "CorpusHealthStatus",
    "CorpusHealthUnavailableReason",
    "CorpusHealthView",
]
