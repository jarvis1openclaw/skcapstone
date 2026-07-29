"""The decision layer classifies proposals and disposes auto vs escalate."""

from __future__ import annotations

from skcapstone.operator_seat import plan

_EXPLAIN = {
    "actions": [
        {"name": "restart_service", "standard": True, "reversible": True, "blast_radius": "low"},
        {"name": "rerun_cronjob", "standard": True, "reversible": True, "blast_radius": "low"},
        {
            "name": "delete_object",
            "standard": False,
            "reversible": False,
            "blast_radius": "delete",
        },
    ]
}


def _proposal(action):
    return {"action": action, "object": "x", "change_class": "normal", "rationale": "r"}


def test_ratified_standard_action_auto():
    out = plan.plan_actions([_proposal("restart_service")], _EXPLAIN)
    assert out[0]["classification"]["change_class"] == "standard"
    assert out[0]["disposition"] == "auto"


def test_reversible_operator_normal_is_auto():
    # rerun_cronjob is not in the ratified standard catalog, but it is reversible
    # and operator-authored, so it is an auto-normal.
    out = plan.plan_actions([_proposal("rerun_cronjob")], _EXPLAIN)
    assert out[0]["classification"]["change_class"] == "normal"
    assert out[0]["disposition"] == "auto"


def test_irreversible_delete_escalates():
    out = plan.plan_actions([_proposal("delete_object")], _EXPLAIN)
    assert out[0]["classification"]["change_class"] == "major"
    assert out[0]["disposition"] == "escalate"


def test_unknown_action_escalates():
    out = plan.plan_actions([_proposal("mystery")], _EXPLAIN)
    assert out[0]["disposition"] == "escalate"  # no catalog metadata -> not auto


def test_non_operator_author_escalates():
    out = plan.plan_actions([_proposal("rerun_cronjob")], _EXPLAIN, author="someone")
    assert out[0]["disposition"] == "escalate"
