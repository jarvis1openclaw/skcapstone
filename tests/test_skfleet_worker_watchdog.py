"""Deterministic worker lease, timeout, and attribution tests."""

from datetime import datetime, timezone

import pytest

from skcapstone.fleet.worker_watchdog import (
    GatewayRequest,
    WorkerClassification,
    WorkerObservation,
    classify_worker,
    correlate_request,
    summarize_workers,
)

NOW = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)


def _worker(**changes: object) -> WorkerObservation:
    values: dict[str, object] = {
        "owner": "pi-codex-chiap08-abcd1234",
        "claim_owner": "pi-codex-chiap08-abcd1234",
        "claim_revision": "revision-1",
        "expected_claim_revision": "revision-1",
        "process_alive": True,
        "session_alive": True,
        "heartbeat_at": "2026-09-04T13:59:30Z",
        "lease_expires_at": "2026-09-04T14:05:00Z",
    }
    values.update(changes)
    return WorkerObservation(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "state", "reason"),
    [
        ({}, "running", "heartbeat-fresh"),
        ({"heartbeat_at": None}, "stalled", "heartbeat-missing"),
        ({"heartbeat_at": "2026-09-04T13:55:00Z"}, "stalled", "heartbeat-expired"),
        ({"lease_expires_at": "2026-09-04T13:59:59Z"}, "timed-out", "lease-expired"),
        ({"process_alive": False, "session_alive": False}, "exited", "no-process-or-session"),
        ({"claim_owner": "other"}, "claim-mismatch", "owner-mismatch"),
        ({"claim_revision": "revision-2"}, "claim-mismatch", "claim-revision-mismatch"),
    ],
)
def test_classifies_each_state_without_mutation(
    changes: dict[str, object], state: str, reason: str
) -> None:
    result = classify_worker(_worker(**changes), now=NOW)
    assert (result.state, result.reason) == (state, reason)


def test_summary_contains_all_states_and_stable_counts() -> None:
    result = summarize_workers(
        [_worker(), _worker(heartbeat_at=None), _worker(process_alive=False, session_alive=False)],
        now=NOW,
    )
    assert result == {
        "claim-mismatch": 0,
        "exited": 1,
        "running": 1,
        "stalled": 1,
        "timed-out": 0,
    }


def test_request_join_is_explicit_and_value_free() -> None:
    worker = WorkerClassification("agent-a", "running", "heartbeat-fresh")
    request = GatewayRequest(
        "request-1", "agent-a", "session-1", "card-1", "revision-1", "chiap08", "codex", "model-a"
    )
    result = correlate_request(request, {"agent-a": worker})
    assert result.state == "running"
    assert result.worker_owner == "agent-a"
    assert result.missing == ()
    incomplete = correlate_request(
        GatewayRequest("request-2", None, None, None, None, "chiap08", "codex", "model-a"),
        {"agent-a": worker},
    )
    assert incomplete.state == "unmatched"
    assert "agent_id" in incomplete.missing
