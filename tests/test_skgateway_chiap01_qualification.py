from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify_skgateway_chiap01.py"
DEPLOYMENT = (
    ROOT
    / "config"
    / "model_gateway"
    / "deployment"
    / "skgateway-chiap01-qualification.json"
)
SYNTHETIC_DENY_CONFIG = ROOT / "deploy" / "chiap01" / "skgateway.synthetic-deny.yaml"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("skgateway_qualification", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(source: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(source), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _source_tree(tmp_path: Path, *, compatible: bool) -> Path:
    source = tmp_path / "skgateway"
    (source / "src" / "policy").mkdir(parents=True)
    authz = """
const serviceHeader = "X-SKLegal-Service-Authorization";
const requestCapAuth = "request_capauth";
const safe = ["allow", "reason", "decision_id", "policy_revision",
              "correlation_id", "obligations"];
const resource = ["tenant_id", "matter_id", "material_id",
                  "material_version", "route_id"];
const context = ["purpose", "classification", "privilege", "ethical_wall"];
const capability = "skgateway.infer";
"""
    if not compatible:
        authz = 'const header = "Authorization";\nconst capability = "skgateway.infer";\n'
    (source / "src" / "policy" / "authz_decide.mjs").write_text(
        authz, encoding="utf-8"
    )
    (source / "src" / "policy" / "authz_gate.mjs").write_text(
        "const enforce = 'SKGATEWAY_AUTHZ_ENFORCE';\n", encoding="utf-8"
    )
    (source / "src" / "policy" / "authz_routes.mjs").write_text(
        'const capability = "skgateway.infer";\n', encoding="utf-8"
    )
    (source / "src" / "index.mjs").write_text(
        "const strict = 'SKGATEWAY_AUTHZ_TRUST_INTERNAL';\n"
        "if (config.dashboard?.enabled !== false) { startDashboard(); }\n"
        "if (config.metrics?.enabled !== false) { startMetrics(); }\n",
        encoding="utf-8",
    )
    (source / "package.json").write_text("{}\n", encoding="ascii")
    (source / "package-lock.json").write_text("{}\n", encoding="ascii")
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "synthetic@example.invalid")
    _git(source, "config", "user.name", "Synthetic Qualification")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "synthetic fixture")
    return source


def _deployment_for(module: ModuleType, source: Path, tmp_path: Path) -> Path:
    payload = json.loads(DEPLOYMENT.read_text(encoding="ascii"))
    payload["source"]["commit"] = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload["source"]["package_sha256"] = module._sha256(
        (source / "package.json").read_bytes()
    )
    payload["source"]["lockfile_sha256"] = module._sha256(
        (source / "package-lock.json").read_bytes()
    )
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="ascii"
    )
    return deployment


def test_repository_contract_is_disabled_and_exact() -> None:
    module = _module()
    payload = module.load_deployment(DEPLOYMENT)

    assert payload["activation"] is False
    assert payload["protected_traffic"] is False
    assert payload["runtime"]["proxy_bind"] == "loopback-only"
    assert payload["runtime"]["metrics"] == "disabled"
    assert payload["runtime"]["trust_internal_peers"] is False
    assert payload["runtime"]["allow_cache"] is False
    assert payload["runtime"]["standalone_integration"] is True
    assert payload["authorization"]["capability"] == "skgateway.infer"


def test_preflight_accepts_only_the_two_credential_exact_scope_contract(
    tmp_path: Path,
) -> None:
    module = _module()
    source = _source_tree(tmp_path, compatible=True)
    deployment = _deployment_for(module, source, tmp_path)

    report = module.inspect_source(source, module.load_deployment(deployment))

    assert report["result"] == "PASS"
    assert report["wire_compatible"] is True
    assert report["activation_permitted"] is False
    assert report["qualified_for_protected_traffic"] is False
    assert set(report["live_controls"].values()) == {False}


def test_preflight_fails_closed_for_the_legacy_single_credential_shape(
    tmp_path: Path,
) -> None:
    module = _module()
    source = _source_tree(tmp_path, compatible=False)
    deployment = _deployment_for(module, source, tmp_path)

    report = module.inspect_source(source, module.load_deployment(deployment))

    assert report["result"] == "FAIL_CLOSED"
    assert report["wire_compatible"] is False
    assert report["checks"]["service_header"] is False
    assert report["checks"]["separate_request_capauth"] is False
    assert report["qualified_for_protected_traffic"] is False


def test_deployment_parser_rejects_activation(tmp_path: Path) -> None:
    module = _module()
    payload = json.loads(DEPLOYMENT.read_text(encoding="ascii"))
    payload["activation"] = True
    path = tmp_path / "enabled.json"
    path.write_text(json.dumps(payload), encoding="ascii")

    with pytest.raises(module.QualificationError, match="attempts activation"):
        module.load_deployment(path)


def test_committed_contract_has_no_literal_endpoint_or_raw_secret() -> None:
    text = DEPLOYMENT.read_text(encoding="ascii")
    lowered = text.lower()

    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "bearer " not in lowered
    assert "private_key" not in lowered
    assert "capability_token" not in lowered
    assert "inbox" not in lowered


def test_synthetic_denial_config_is_loopback_only_and_nonactivating() -> None:
    text = SYNTHETIC_DENY_CONFIG.read_text(encoding="ascii")

    assert "bind: 127.0.0.1" in text
    assert "enforce: true" in text
    assert "trust_internal: false" in text
    assert "cache_ttl_ms: 0" in text
    assert "dashboard:\n  enabled: false" in text
    assert "discovery:\n  enabled: false" in text
    assert "http://127.0.0.1:9/v1" in text
    assert "api_key" not in text.lower()
    assert "bearer" not in text.lower()
    assert "token" not in text.lower()
