"""Regression tests for exact fleet worker lane affinity."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROTATE = ROOT / "scripts" / "fleet" / "skfleet-rotate.py"


def _load_lane_helpers() -> dict[str, object]:
    tree = ast.parse(ROTATE.read_text(encoding="utf-8"))
    names = {
        "_dependency_value",
        "_fold_claimability",
        "lane_compatibility",
        "select_compatible_lane",
    }
    body = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_LANE_ONLY_LABELS"
                for target in node.targets
            )
        )
        or (isinstance(node, ast.FunctionDef) and node.name in names)
    ]
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(ROTATE), "exec"), namespace)
    assert names <= namespace.keys()
    return namespace


def _core(card_id: str, labels: list[str]) -> dict[str, object]:
    return {
        "id": card_id,
        "kind": "task",
        "title": f"Synthetic lane fixture {card_id}",
        "initial_labels": labels,
        "dependencies": [],
    }


@pytest.mark.parametrize(
    ("labels", "escalation_required", "expected"),
    [
        (["codex-only"], False, (("codex",), "required-lane:codex")),
        (["glm-only"], False, (("glm",), "required-lane:glm")),
        (["escalation-only"], False, (("escalate",), "required-lane:escalate")),
        ([], True, (("escalate",), "required-lane:escalate")),
        ([], False, (("glm", "codex"), "ordinary")),
    ],
)
def test_exact_lane_compatibility(
    labels: list[str], escalation_required: bool, expected: object
) -> None:
    namespace = _load_lane_helpers()
    assert namespace["lane_compatibility"](labels, escalation_required) == expected


@pytest.mark.parametrize(
    ("labels", "escalation_required", "reason"),
    [
        (["codex-only", "glm-only"], False, "conflicting-lane-only:codex,glm"),
        (["codex-only"], True, "conflicting-lane-only:codex,escalate"),
        (["glm-only", "escalation-only"], False, "conflicting-lane-only:escalate,glm"),
    ],
)
def test_conflicting_lane_requirements_fail_closed(
    labels: list[str], escalation_required: bool, reason: str
) -> None:
    namespace = _load_lane_helpers()
    compatible, actual = namespace["lane_compatibility"](labels, escalation_required)
    assert compatible == ()
    assert actual == reason


@pytest.mark.parametrize("host", ["chiap01", "chiap02", "chiap03", "chiap04", "chiap08"])
def test_lane_affinity_is_identical_across_all_five_host_partitions(host: str) -> None:
    namespace = _load_lane_helpers()
    del host
    selected, reason = namespace["select_compatible_lane"](
        ["codex-only"],
        False,
        ["glm", "codex", "escalate"],
        {"codex": 1, "glm": 3, "escalate": 2},
    )
    assert (selected, reason) == ("codex", "compatible")


def test_no_compatible_slot_does_not_consume_another_lane() -> None:
    namespace = _load_lane_helpers()
    remaining = {"codex": 0, "glm": 3, "escalate": 2}
    selected, reason = namespace["select_compatible_lane"](
        ["codex-only"], False, ["glm", "codex", "escalate"], remaining
    )
    assert selected is None
    assert reason == "no-free-lane:codex"
    assert remaining == {"codex": 0, "glm": 3, "escalate": 2}


def test_simultaneous_workers_respect_the_same_remaining_capacity() -> None:
    namespace = _load_lane_helpers()
    remaining = {"codex": 1, "glm": 3, "escalate": 2}
    first, _ = namespace["select_compatible_lane"](
        ["codex-only"], False, ["glm", "codex", "escalate"], remaining
    )
    remaining[first] -= 1
    second, reason = namespace["select_compatible_lane"](
        ["codex-only"], False, ["glm", "codex", "escalate"], remaining
    )
    assert first == "codex"
    assert second is None
    assert reason == "no-free-lane:codex"
    assert remaining["glm"] == 3


def test_folded_add_and_remove_label_events_drive_routing() -> None:
    namespace = _load_lane_helpers()
    core = _core("fold0001", ["codex-only"])
    events = [
        {
            "ts": "2026-08-29T10:00:00Z",
            "writer": "a",
            "seq": 0,
            "action": "remove_label",
            "label": "codex-only",
        },
        {
            "ts": "2026-08-29T10:01:00Z",
            "writer": "a",
            "seq": 1,
            "action": "add_label",
            "label": "glm-only",
        },
    ]
    state = namespace["_fold_claimability"](core, list(reversed(events)))
    assert state["labels"] == ["glm-only"]
    selected, reason = namespace["select_compatible_lane"](
        state["labels"],
        False,
        ["glm", "codex", "escalate"],
        {"codex": 1, "glm": 1, "escalate": 1},
    )
    assert (selected, reason) == ("glm", "compatible")


@pytest.mark.parametrize(
    ("card_id", "owner", "claim_revision", "launch_record"),
    [
        (
            "12eaed95",
            "pi-glm-chiap01-12eaed95",
            "1fbb41e91fd2477898245f368a431b00",  # pragma: allowlist secret
            "LAUNCHED|chiap01|glm-auto-12eaed95|12eaed95|lane=glm|model=glm-4.6",
        ),
        (
            "ac8592fc",
            "pi-glm-chiap08-ac8592fc",
            "c4963eeb02d643d0a5761ababa2b98f9",  # pragma: allowlist secret
            "LAUNCHED|chiap08|glm-auto-ac8592fc|ac8592fc|lane=glm|model=glm-4.6",
        ),
    ],
)
def test_observed_codex_only_glm_misroutes_are_rejected(
    card_id: str, owner: str, claim_revision: str, launch_record: str
) -> None:
    namespace = _load_lane_helpers()
    state = namespace["_fold_claimability"](_core(card_id, ["codex-only"]), [])
    selected, reason = namespace["select_compatible_lane"](
        state["labels"],
        False,
        ["glm", "codex", "escalate"],
        {"codex": 1, "glm": 1, "escalate": 1},
    )
    assert owner.startswith("pi-glm-") and len(claim_revision) == 32
    assert "|lane=glm|" in launch_record
    assert (selected, reason) == ("codex", "compatible")


def test_pool_and_immediate_preclaim_use_the_same_affinity_predicate() -> None:
    source = ROTATE.read_text(encoding="utf-8")
    assert "_lane_name,_defer=select_compatible_lane(" in source
    assert "compatible,affinity_reason=lane_compatibility(" in source
    assert 'fresh_claimability["labels"]' in source
    assert "SKIPPED_LANE_RACE|" in source
    assert "LANE_DEFER|" in source
