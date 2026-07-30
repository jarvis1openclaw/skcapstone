"""skos adapter: conformant to the contract, health mapped correctly."""

from __future__ import annotations

from skcapstone.operator_seat import adapter, skos_adapter as ad


def test_skos_explain_is_contract_conformant():
    assert adapter.validate_explain(ad.skos_explain()) == []


def test_skos_observe_is_contract_conformant():
    obs = ad.observe()
    assert adapter.validate_observe(obs) == []


def test_skos_healthy_all_true():
    obs = ad.skos_observe(
        probe=lambda: {
            k: True
            for k in ("upstream_serving", "pool_healthy", "scheduler_alive", "gtd_draining")
        }
    )
    assert all(c["status"] == "True" for c in obs["conditions"])


def test_skos_default_probe_fails_safe(monkeypatch):
    def _boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr("subprocess.run", _boom, raising=False)
    monkeypatch.setattr("urllib.request.urlopen", _boom, raising=False)
    st = ad._default_probe()
    assert all(v is True for v in st.values())  # fail safe
