"""Tests for the fleet-wide freeze kill-switch."""

from __future__ import annotations

import pytest

from skcapstone.fleet import store


def test_unfrozen_by_default(paths) -> None:
    assert store.is_frozen(paths) is False
    assert store.actuation_allowed(paths) is True


def test_freeze_round_trip(paths, operator) -> None:
    payload = store.set_frozen(paths, True, writer=operator, reason="incident drill")
    assert payload["frozen"] is True
    assert store.is_frozen(paths) is True
    assert store.actuation_allowed(paths) is False
    assert paths.freeze_path().exists()
    store.set_frozen(paths, False, writer=operator)
    assert store.is_frozen(paths) is False


def test_only_operator_may_toggle(paths, noded41) -> None:
    with pytest.raises(store.OwnershipError):
        store.set_frozen(paths, True, writer=noded41)


def test_garbage_freeze_file_fails_safe_frozen(paths) -> None:
    paths.freeze_path().parent.mkdir(parents=True, exist_ok=True)
    paths.freeze_path().write_text("not json")
    assert store.is_frozen(paths) is True  # unreadable flag = halt, not run


# ── actuation_ready / check_actuation_gate (coord card 3925d012 / SKW-AUTONOMY-E2) ──
#
# is_frozen's read semantics stay exactly as above: missing means not frozen,
# corrupt means frozen. These predicates answer a different question, "has a
# human proven the kill switch exists", which is what skoperator status used
# to collapse into "active" for a fresh estate.


def test_absent_freeze_file_is_not_actuation_ready(paths) -> None:
    assert store.actuation_ready(paths) is False


def test_provisioned_off_is_actuation_ready(paths, operator) -> None:
    store.set_frozen(paths, False, writer=operator, reason="initial provisioning")
    assert store.actuation_ready(paths) is True


def test_provisioned_on_is_also_actuation_ready(paths, operator) -> None:
    # Readiness and freeze are independent: a human-provisioned estate that
    # is currently frozen is still "ready" (the switch demonstrably exists),
    # it is just also off-limits right now. check_actuation_gate combines
    # both; actuation_ready alone answers only "does the switch exist".
    store.set_frozen(paths, True, writer=operator, reason="drill")
    assert store.actuation_ready(paths) is True


def test_corrupt_freeze_file_is_not_actuation_ready(paths) -> None:
    paths.freeze_path().parent.mkdir(parents=True, exist_ok=True)
    paths.freeze_path().write_text("not json")
    assert store.actuation_ready(paths) is False


def test_fabricated_freeze_file_is_not_actuation_ready(paths) -> None:
    # Valid JSON that never went through set_frozen (no writer.role, or a
    # non-operator role) does not count as provisioned: existing is not
    # the same as human-provisioned.
    paths.freeze_path().parent.mkdir(parents=True, exist_ok=True)
    paths.freeze_path().write_text('{"frozen": false}')
    assert store.actuation_ready(paths) is False


def test_gate_refuses_absent_file_with_reason_unprovisioned(paths) -> None:
    """Negative test (fails against current code, section 3.6 / card 3925d012):
    an absent freeze file must refuse actuation with reason "unprovisioned",
    not silently behave as active."""
    gate = store.check_actuation_gate(paths)
    assert gate.allowed is False
    assert gate.reason == "unprovisioned" == store.REASON_UNPROVISIONED


def test_gate_refuses_corrupt_file_with_reason_frozen(paths) -> None:
    """Negative test: a corrupt freeze file refuses with reason "frozen"
    (freeze wins first, matching is_frozen's existing fail-closed read)."""
    paths.freeze_path().parent.mkdir(parents=True, exist_ok=True)
    paths.freeze_path().write_text("not json")
    gate = store.check_actuation_gate(paths)
    assert gate.allowed is False
    assert gate.reason == "frozen" == store.REASON_FROZEN


def test_gate_allows_only_when_provisioned_off(paths, operator) -> None:
    store.set_frozen(paths, False, writer=operator, reason="initial provisioning")
    gate = store.check_actuation_gate(paths)
    assert gate.allowed is True
    assert gate.reason is None


def test_gate_refuses_provisioned_but_frozen(paths, operator) -> None:
    store.set_frozen(paths, False, writer=operator, reason="initial provisioning")
    store.set_frozen(paths, True, writer=operator, reason="drill")
    gate = store.check_actuation_gate(paths)
    assert gate.allowed is False
    assert gate.reason == store.REASON_FROZEN
