"""Pure worker lease and attribution checks for fleet monitoring.

This module deliberately observes and classifies.  It never releases a claim,
stops a process, or changes a lease.  A caller may use ``claim_mismatch`` and
``timed_out`` as signals for a separately fenced control operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

WORKER_STATES = frozenset(
    {"running", "stalled", "timed-out", "exited", "claim-mismatch"}
)


@dataclass(frozen=True)
class WorkerObservation:
    """One read-only worker and claim snapshot."""

    owner: str
    claim_owner: str | None
    claim_revision: str | None
    expected_claim_revision: str | None
    process_alive: bool
    session_alive: bool
    heartbeat_at: str | None
    lease_expires_at: str | None


@dataclass(frozen=True)
class WorkerClassification:
    """A bounded, auditable worker state."""

    owner: str
    state: str
    reason: str


@dataclass(frozen=True)
class GatewayRequest:
    """Value-free gateway request attribution fields."""

    request_id: str
    agent_id: str | None
    session_id: str | None
    card_id: str | None
    claim_revision: str | None
    host: str | None
    lane: str | None
    model: str | None


@dataclass(frozen=True)
class RequestAttribution:
    """Gateway request joined to a worker, without request content."""

    request_id: str
    state: str
    worker_owner: str | None
    missing: tuple[str, ...]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_worker(
    observation: WorkerObservation,
    *,
    now: datetime,
    heartbeat_timeout_s: float = 120.0,
) -> WorkerClassification:
    """Classify one worker using exact claim fencing and bounded freshness.

    Claim identity is checked before liveness.  A live process with a missing
    or changed claim is therefore ``claim-mismatch`` and is never silently
    treated as healthy.  This function has no mutation side effects.
    """
    if observation.claim_owner != observation.owner:
        return WorkerClassification(observation.owner, "claim-mismatch", "owner-mismatch")
    if (
        observation.expected_claim_revision is None
        or observation.claim_revision != observation.expected_claim_revision
    ):
        return WorkerClassification(
            observation.owner, "claim-mismatch", "claim-revision-mismatch"
        )
    if not observation.process_alive and not observation.session_alive:
        return WorkerClassification(observation.owner, "exited", "no-process-or-session")
    expiry = _parse_time(observation.lease_expires_at)
    if expiry is not None and now.astimezone(timezone.utc) >= expiry:
        return WorkerClassification(observation.owner, "timed-out", "lease-expired")
    heartbeat = _parse_time(observation.heartbeat_at)
    if heartbeat is None:
        return WorkerClassification(observation.owner, "stalled", "heartbeat-missing")
    age = (now.astimezone(timezone.utc) - heartbeat).total_seconds()
    if age > heartbeat_timeout_s:
        return WorkerClassification(observation.owner, "stalled", "heartbeat-expired")
    return WorkerClassification(observation.owner, "running", "heartbeat-fresh")


def summarize_workers(
    observations: Iterable[WorkerObservation],
    *,
    now: datetime,
    heartbeat_timeout_s: float = 120.0,
) -> dict[str, int]:
    """Return stable counts for every worker state, including zeroes."""
    counts = {state: 0 for state in sorted(WORKER_STATES)}
    for observation in observations:
        state = classify_worker(
            observation, now=now, heartbeat_timeout_s=heartbeat_timeout_s
        ).state
        counts[state] += 1
    return counts


def correlate_request(
    request: GatewayRequest,
    workers: Mapping[str, WorkerClassification],
) -> RequestAttribution:
    """Join a gateway request to its worker identity, reporting gaps.

    ``workers`` is keyed by agent ID.  Missing attribution is explicit and
    never inferred from model, host, or request activity alone.
    """
    fields = {
        "agent_id": request.agent_id,
        "session_id": request.session_id,
        "card_id": request.card_id,
        "claim_revision": request.claim_revision,
        "host": request.host,
        "lane": request.lane,
        "model": request.model,
    }
    missing = tuple(name for name, value in fields.items() if not value)
    worker = workers.get(request.agent_id or "")
    if worker is None:
        return RequestAttribution(request.request_id, "unmatched", None, missing)
    if missing:
        return RequestAttribution(request.request_id, "incomplete", worker.owner, missing)
    return RequestAttribution(request.request_id, worker.state, worker.owner, ())
