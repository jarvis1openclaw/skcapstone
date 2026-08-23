from __future__ import annotations

import hashlib
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
SYNTHETIC_MATRIX = (
    ROOT
    / "config"
    / "model_gateway"
    / "deployment"
    / "skgateway-chiap01-synthetic-matrix.json"
)
PARITY_FIXTURE = ROOT / "tests" / "fixtures" / "skgateway" / "direct-qwen-parity.json"


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
    (source / "src" / "policy" / "sklegal_authz_decide.mjs").write_text(
        authz + "\nconst stats = { cacheEnabled: false };\n", encoding="utf-8"
    )
    (source / "src" / "config.mjs").write_text(
        "const qualification = 'sklegal_qualification';\n", encoding="utf-8"
    )
    (source / "src" / "proxy").mkdir()
    (source / "src" / "proxy" / "router.mjs").write_text(
        "const CLIENT_CREDENTIAL_HEADERS = [];\n"
        "for (const h of CLIENT_CREDENTIAL_HEADERS) delete forwardHeaders[h];\n",
        encoding="utf-8",
    )
    (source / "src" / "index.mjs").write_text(
        "const strict = 'SKGATEWAY_AUTHZ_TRUST_INTERNAL';\n"
        "if (config.dashboard?.enabled !== false) { startDashboard(); }\n"
        "if (config.metrics?.enabled !== false) { startMetrics(); }\n"
        "if (getConfig().discovery?.enabled === false) return;\n"
        "if (config.discovery?.enabled !== false) refresh();\n"
        "const message = 'Model discovery is disabled';\n",
        encoding="utf-8",
    )
    with (source / "src" / "policy" / "authz_gate.mjs").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(
            "if (sklegalQualification) return strictPdp();\n"
            "// qualification routes returned above always consult the strict PDP client\n"
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
    assert payload["source"]["commit"] == (
        "3cf16fe6ca1a6e5ec92e5f090fc798dfd6404596"
    )
    assert payload["composition"]["revision"] == (
        "sklegal-skgateway-authz-production-composition/v1"
    )
    assert payload["composition"]["profile_enabled"] is False


def test_repository_composition_hashes_and_binding_are_exact() -> None:
    module = _module()
    payload = module.load_deployment(DEPLOYMENT)

    result = module.inspect_composition(ROOT, payload)

    assert result["result"] == "PASS"
    assert result["endpoint_binding"] == "127.0.0.1:/v1/authz/decide"
    assert result["service_identity"] == (
        "capauth:sklegal-model-gateway@chiap01.skworld"
    )
    assert set(result["checks"].values()) == {True}


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


def test_synthetic_matrix_is_exact_nonactivating_and_complete() -> None:
    module = _module()
    matrix = module.load_synthetic_matrix(SYNTHETIC_MATRIX)
    scenarios = {item["id"]: item for item in matrix["scenarios"]}

    assert matrix["fixture_only"] is True
    assert matrix["activation_permitted"] is False
    assert matrix["protected_traffic"] is False
    assert set(scenarios) == module.REQUIRED_SCENARIOS
    assert scenarios["canonical_allow"]["decision_id"].startswith("synthetic-")
    assert scenarios["canonical_deny"]["expected_status"] == 403
    assert scenarios["pdp_unavailable"]["expected_allow"] is False
    assert scenarios["audit_unavailable"]["expected_status"] == 503
    assert scenarios["malformed_request"]["expected_status"] == 422
    assert scenarios["oversized_request"]["expected_status"] == 422
    assert scenarios["exact_scope_mismatch"]["expected_allow"] is False
    assert scenarios["saturation_ninth_request"]["expected_status"] == 503
    assert scenarios["restart_recovery"]["expected_status"] == 200
    assert scenarios["sanitizer_leakage"]["prohibited_fields"] == [
        "prompt",
        "matter_content",
        "source_span",
        "bearer_token",
        "private_key",
        "raw_capability",
    ]
    assert scenarios["audit_attribution"]["required_fields"] == [
        "decision_id",
        "policy_revision",
        "correlation_id",
        "service_identity",
    ]
    assert scenarios["rollback_direct_qwen"]["gateway_profile_state"] == "disabled"
    assert scenarios["rollback_direct_qwen"]["direct_profile_state"] == "enabled"


def test_direct_qwen_parity_fixture_hash_is_exact() -> None:
    matrix = json.loads(SYNTHETIC_MATRIX.read_text(encoding="ascii"))
    parity = next(
        item for item in matrix["scenarios"] if item["id"] == "direct_qwen_parity"
    )

    assert hashlib.sha256(PARITY_FIXTURE.read_bytes()).hexdigest() == parity[
        "proposal_fixture_sha256"
    ]
    fixture = json.loads(PARITY_FIXTURE.read_text(encoding="ascii"))
    assert fixture["payload"]["uncertainty"] == "fixture-only"


def test_synthetic_matrix_controls_are_explicit_and_safe() -> None:
    matrix = json.loads(SYNTHETIC_MATRIX.read_text(encoding="ascii"))

    assert matrix["controls"] == {
        "dashboard": "disabled",
        "metrics": "disabled",
        "discovery": "disabled-no-force-refresh",
        "internal_bypass": "disabled",
        "allow_cache": "disabled",
    }
    text = SYNTHETIC_MATRIX.read_text(encoding="ascii").lower()
    assert "bearer " not in text
    assert "http://" not in text
    assert "https://" not in text
    assert "inbox" not in text


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
