"""skchat adapter: conformant to the contract, health mapped correctly."""

from __future__ import annotations

from skcapstone.operator_seat import adapter, skchat_adapter


def test_skchat_explain_is_contract_conformant():
    assert adapter.validate_explain(skchat_adapter.skchat_explain()) == []


def test_skchat_observe_is_contract_conformant():
    obs = skchat_adapter.skchat_observe(
        probe=lambda: {"bridge_alive": True, "outbox_depth": 0, "outbox_limit": 1000}
    )
    assert adapter.validate_observe(obs) == []


def test_healthy_skchat_is_all_true():
    obs = skchat_adapter.skchat_observe(
        probe=lambda: {"bridge_alive": True, "outbox_depth": 5, "outbox_limit": 1000}
    )
    by_type = {c["type"]: c["status"] for c in obs["conditions"]}
    assert by_type["BridgeAlive"] == "True"
    assert by_type["OutboxBounded"] == "True"


def test_dead_bridge_and_overfull_outbox_fire():
    obs = skchat_adapter.skchat_observe(
        probe=lambda: {"bridge_alive": False, "outbox_depth": 5000, "outbox_limit": 1000}
    )
    by_type = {c["type"]: c["status"] for c in obs["conditions"]}
    assert by_type["BridgeAlive"] == "False"  # health condition fires when False
    assert by_type["OutboxBounded"] == "False"  # over the bound


def test_default_probe_fails_safe(monkeypatch):
    # An unreachable skchat must not raise or false-alarm.
    def _boom(*a, **k):
        raise OSError("skchat down")

    monkeypatch.setattr("subprocess.run", _boom)
    st = skchat_adapter._default_probe()
    assert st["bridge_alive"] is True  # fail safe
