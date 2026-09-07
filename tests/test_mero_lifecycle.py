from skcapstone.mero_lifecycle import (
    false_state_launches_zero,
    reconcile_snapshot,
    review_dispatch_decision,
)


def row(card_id, **extra):
    return {"card_id": card_id, **extra}


def test_reconcile_same_bounded_snapshot_fails_closed_on_loss_and_gain():
    health = reconcile_snapshot("rev-1", {
        "cardstore": [row("a"), row("b")],
        "pool_v2": [row("a"), row("c")],
        "lane": [row("a"), row("c")],
    })
    assert health.stage_counts == {"cardstore": 2, "pool_v2": 2, "lane": 2}
    assert health.invariants["cardstore->pool_v2"] == "BLOCKED"
    assert health.unmatched_ids["cardstore->pool_v2:lost"] == ("b",)
    assert health.unmatched_ids["cardstore->pool_v2:gained"] == ("c",)
    assert health.sha256() and len(health.sha256()) == 64


def test_review_requires_governed_label_producer_hash_and_distinct_reviewer():
    structural = {"claimable": False, "reason": "review"}
    evidence = {"review_label": True, "producer": "link", "evidence_sha256": "a" * 64}
    assert review_dispatch_decision(structural, evidence, reviewer="jarvis") == "PASS"
    assert review_dispatch_decision(structural, {**evidence, "review_label": False}, reviewer="jarvis") == "BLOCKED"
    assert review_dispatch_decision(structural, evidence, reviewer="link") == "BLOCKED"
    assert review_dispatch_decision({"claimable": False, "reason": "unknown"}, evidence, reviewer="jarvis") == "BLOCKED"


def test_false_states_other_than_review_never_launch():
    rows = [
        row("ordinary", claimable=False, reason="unknown"),
        row("stale", claimable=False, reason="stale"),
        row("terminal", claimable=False, reason="terminal"),
    ]
    assert false_state_launches_zero(rows)
