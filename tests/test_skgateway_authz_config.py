from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sklegal_api.skgateway_authz import load_skgateway_authz_deployment

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "model_gateway" / "deployment" / "skgateway-authz-adapter.json"


def test_skgateway_authz_adapter_is_disabled_and_fail_closed() -> None:
    payload = load_skgateway_authz_deployment(CONFIG)
    assert payload.enabled is False
    assert payload.fail_closed is True
    assert payload.bind == "loopback-only"
    assert payload.capability == "skgateway.infer"
    assert payload.service_header == "X-SKLegal-Service-Authorization"
    assert payload.service_secret_reference.startswith("vault:")
    assert payload.protected_traffic == "denied-until-live-path-report-and-human-approval"


def test_skgateway_authz_config_has_no_literal_endpoint_or_secret() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert "http://" not in text
    assert "https://" not in text
    assert "Bearer " not in text
    assert "token-value" not in text
    assert all(ord(char) < 128 for char in text)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", True),
        ("endpoint_reference", "http://127.0.0.1:9000"),
        ("service_secret_reference", "literal-secret"),
        ("capability", "matter.manage"),
        ("allow_cache", True),
    ],
)
def test_loader_rejects_unsafe_deployment_mutations(field: str, value: object) -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload[field] = value
    with TemporaryDirectory(prefix="sklegal-authz-config-") as tempdir:
        path = Path(tempdir) / "config.json"
        path.write_text(json.dumps(payload), encoding="ascii")
        with pytest.raises(ValueError):
            load_skgateway_authz_deployment(path)
