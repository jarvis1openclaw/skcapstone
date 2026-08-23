#!/usr/bin/env python3
"""Fail-closed preflight for the SKL-S3-10C chiap01 gateway path.

The preflight reads source and public deployment metadata only. It never reads
environment values, service credentials, provider keys, capability tokens, or
Matter content. A static implementation marker is evidence of compatibility,
not evidence that a live control passed. Live controls remain false unless a
separate signed qualification result supplies exact runtime evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEPLOYMENT = (
    ROOT
    / "config"
    / "model_gateway"
    / "deployment"
    / "skgateway-chiap01-qualification.json"
)
EXPECTED_SCHEMA = "sklegal-skgateway-chiap01-qualification/v1"
EXPECTED_CARD = "60cb0c9a"
EXPECTED_SERVICE_IDENTITY = "capauth:sklegal-model-gateway@chiap01.skworld"
EXPECTED_CAPABILITY = "skgateway.infer"
EXPECTED_COMPOSITION_REVISION = (
    "sklegal-skgateway-authz-production-composition/v1"
)
DEFAULT_MATRIX = (
    ROOT
    / "config"
    / "model_gateway"
    / "deployment"
    / "skgateway-chiap01-synthetic-matrix.json"
)
EXPECTED_CONTROLS = (
    "capauth_identity",
    "capauth_capability",
    "sklegal_policy_decision",
    "classification_egress",
    "body_and_system_limits",
    "secret_handling",
    "tool_budget_stripping",
    "rate_limits",
    "shared_capacity_domain",
    "attributable_audit",
    "no_prompt_or_secret_leak",
    "deterministic_denial",
)
EXPECTED_RESOURCE_FIELDS = (
    "tenant_id",
    "matter_id",
    "material_id",
    "material_version",
    "route_id",
)
EXPECTED_CONTEXT_FIELDS = (
    "purpose",
    "classification",
    "privilege",
    "ethical_wall",
)
SOURCE_FILES = (
    "src/index.mjs",
    "src/config.mjs",
    "src/policy/authz_decide.mjs",
    "src/policy/authz_gate.mjs",
    "src/policy/authz_routes.mjs",
    "src/policy/sklegal_authz_decide.mjs",
    "src/proxy/router.mjs",
)
COMPOSITION_FILES = (
    "services/api/src/sklegal_api/skgateway_authz.py",
    "config/model_gateway/deployment/skgateway-authz-adapter.json",
)
REQUIRED_SCENARIOS = frozenset(
    {
        "canonical_allow",
        "canonical_deny",
        "pdp_unavailable",
        "audit_unavailable",
        "malformed_request",
        "oversized_request",
        "exact_scope_mismatch",
        "saturation_ninth_request",
        "restart_recovery",
        "sanitizer_leakage",
        "audit_attribution",
        "direct_qwen_parity",
        "rollback_direct_qwen",
    }
)


class QualificationError(RuntimeError):
    """A deployment or source invariant failed closed."""


def _read_regular(path: Path, *, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise QualificationError(f"required file is not regular: {path.name}")
    payload = path.read_bytes()
    if len(payload) > maximum:
        raise QualificationError(f"required file exceeds size limit: {path.name}")
    return payload


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_deployment(path: Path) -> dict[str, Any]:
    raw = _read_regular(path, maximum=64 * 1024)
    if any(byte >= 128 for byte in raw):
        raise QualificationError("deployment contract must be ASCII")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError("deployment contract is malformed") from exc
    if not isinstance(value, dict):
        raise QualificationError("deployment contract must be an object")
    if value.get("schema") != EXPECTED_SCHEMA or value.get("card") != EXPECTED_CARD:
        raise QualificationError("deployment contract identity is not exact")
    if value.get("mode") != "synthetic-qualification-only":
        raise QualificationError("only synthetic qualification mode is permitted")
    if value.get("activation") is not False or value.get("protected_traffic") is not False:
        raise QualificationError("deployment contract attempts activation")

    source = value.get("source")
    runtime = value.get("runtime")
    authorization = value.get("authorization")
    composition = value.get("composition")
    live_path = value.get("live_path")
    rollback = value.get("rollback")
    if not all(
        isinstance(item, dict)
        for item in (source, composition, runtime, authorization, live_path, rollback)
    ):
        raise QualificationError("deployment contract section is missing")
    assert isinstance(source, dict)
    assert isinstance(composition, dict)
    assert isinstance(runtime, dict)
    assert isinstance(authorization, dict)
    assert isinstance(live_path, dict)
    assert isinstance(rollback, dict)

    digest_fields = ("commit", "package_sha256", "lockfile_sha256")
    for field in digest_fields:
        item = source.get(field)
        expected_length = 40 if field == "commit" else 64
        if (
            not isinstance(item, str)
            or len(item) != expected_length
            or any(character not in "0123456789abcdef" for character in item)
        ):
            raise QualificationError(f"source {field} is not a lowercase digest")
    if source.get("repository_reference") != "SKLEGAL_SKGATEWAY_SOURCE_REPOSITORY":
        raise QualificationError("repository must use its environment reference")
    if source.get("install_path_reference") != "SKLEGAL_SKGATEWAY_INSTALL_PATH":
        raise QualificationError("install path must use its environment reference")

    if composition != {
        "sklegal_commit": "c653cae62cb0dd4c51022836e231bac1b36733fa",
        "revision": EXPECTED_COMPOSITION_REVISION,
        "source_sha256": (
            "98b09722eca21e1bb0592cc3daca8aa618eb39af5b687994c16867087713e16f"
        ),
        "deployment_sha256": (
            "7f3a8b2a4b65026d765b8c4601e9b925520c251fc90be7af51c5b7e686f1a42e"
        ),
        "endpoint_path": "/v1/authz/decide",
        "bind_host": "127.0.0.1",
        "transport": "authenticated-local-http",
        "profile_enabled": False,
    }:
        raise QualificationError("SKLegal composition contract is not exact")

    if runtime != {
        "host": "chiap01",
        "service_identity": EXPECTED_SERVICE_IDENTITY,
        "proxy_bind": "loopback-only",
        "dashboard": "disabled",
        "metrics": "disabled",
        "gateway_endpoint_reference": "SKLEGAL_SKGATEWAY_ENDPOINT",
        "qwen_endpoint_reference": "SKLEGAL_QWEN_DIRECT_ENDPOINT",
        "authz_endpoint_reference": "SKLEGAL_CAPAUTH_AUTHZ_ENDPOINT",
        "authz_enforcement": True,
        "trust_internal_peers": False,
        "allow_cache": False,
        "standalone_integration": True,
    }:
        raise QualificationError("runtime contract is not the fail-closed chiap01 pin")
    if authorization != {
        "service_header": "X-SKLegal-Service-Authorization",
        "service_secret_reference": "vault:sklegal/skgateway/authz-service-token",
        "request_capauth_header": "Authorization",
        "capability": EXPECTED_CAPABILITY,
        "resource_fields": list(EXPECTED_RESOURCE_FIELDS),
        "context_fields": list(EXPECTED_CONTEXT_FIELDS),
    }:
        raise QualificationError("authorization wire contract is not exact")
    if live_path != {
        "gate_revision": "skgateway-live-path-gate/v1",
        "controls": list(EXPECTED_CONTROLS),
    }:
        raise QualificationError("live-path control contract is not exact")
    if rollback != {
        "gateway_profile": "chiap08.skgateway-chat.v1",
        "gateway_profile_required_state": "disabled",
        "direct_profile": "chiap08.direct-qwen.v1",
        "direct_profile_required_state": "enabled",
    }:
        raise QualificationError("rollback binding contract is not exact")
    return value


def _git(source_dir: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_dir), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise QualificationError("source revision could not be inspected")
    return result.stdout.strip()


def inspect_source(source_dir: Path, deployment: dict[str, Any]) -> dict[str, Any]:
    source_contract = deployment["source"]
    source_text: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        payload = _read_regular(source_dir / relative, maximum=4 * 1024 * 1024)
        try:
            source_text[relative] = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise QualificationError(f"source file is not UTF-8: {relative}") from exc
        source_hashes[relative] = _sha256(payload)

    package = _read_regular(source_dir / "package.json", maximum=1024 * 1024)
    lockfile = _read_regular(source_dir / "package-lock.json", maximum=8 * 1024 * 1024)
    commit = _git(source_dir, "rev-parse", "HEAD")
    status_lines = tuple(
        line.strip()
        for line in _git(source_dir, "status", "--short").splitlines()
        if line.strip()
    )
    unexpected_status = sorted(set(status_lines))

    decide = source_text["src/policy/authz_decide.mjs"]
    gate = source_text["src/policy/authz_gate.mjs"]
    routes = source_text["src/policy/authz_routes.mjs"]
    index = source_text["src/index.mjs"]
    config = source_text["src/config.mjs"]
    sklegal_decide = source_text["src/policy/sklegal_authz_decide.mjs"]
    router = source_text["src/proxy/router.mjs"]
    combined_authz = "\n".join(
        (decide, gate, routes, sklegal_decide, index, config, router)
    )
    normalized = combined_authz.lower()

    service_header = "x-sklegal-service-authorization" in normalized
    separate_request_capauth = service_header and (
        "request_capauth" in normalized
        or "capauth_authorization" in normalized
        or "capauthauthorization" in normalized
    )
    exact_resource_scope = all(field in combined_authz for field in EXPECTED_RESOURCE_FIELDS)
    exact_context_scope = all(field in combined_authz for field in EXPECTED_CONTEXT_FIELDS)
    exact_capability = EXPECTED_CAPABILITY in routes and EXPECTED_CAPABILITY in decide
    enforce_switch = "SKGATEWAY_AUTHZ_ENFORCE" in combined_authz
    strict_internal_switch = "SKGATEWAY_AUTHZ_TRUST_INTERNAL" in index
    response_is_sanitized = all(
        field in decide
        for field in (
            "allow",
            "reason",
            "decision_id",
            "policy_revision",
            "correlation_id",
            "obligations",
        )
    )
    allow_cache_disabled_by_contract = deployment["runtime"]["allow_cache"] is False
    governed_allow_cache_disabled = "cacheEnabled: false" in sklegal_decide
    internal_bypass_disabled = (
        "if (sklegalQualification)" in gate
        and "qualification routes returned above always consult the strict PDP client"
        in gate
    )
    dashboard_disable_honored = (
        "config.dashboard?.enabled !== false" in index
        or "config.dashboard?.enabled === true" in index
    )
    metrics_disable_honored = (
        "config.metrics?.enabled !== false" in index
        or "config.metrics?.enabled === true" in index
    )
    discovery_disable_honored = (
        "getConfig().discovery?.enabled === false" in index
        and "config.discovery?.enabled !== false" in index
        and "Model discovery is disabled" in index
    )
    credential_headers_stripped = (
        "CLIENT_CREDENTIAL_HEADERS" in router
        and "delete forwardHeaders[h]" in router
    )

    compatibility = {
        "service_header": service_header,
        "separate_request_capauth": separate_request_capauth,
        "exact_resource_scope": exact_resource_scope,
        "exact_context_scope": exact_context_scope,
        "exact_capability": exact_capability,
        "enforce_switch": enforce_switch,
        "strict_internal_switch": strict_internal_switch,
        "sanitized_response_shape": response_is_sanitized,
        "allow_cache_disabled_by_contract": allow_cache_disabled_by_contract,
        "governed_allow_cache_disabled": governed_allow_cache_disabled,
        "internal_bypass_disabled": internal_bypass_disabled,
        "dashboard_disable_honored": dashboard_disable_honored,
        "metrics_disable_honored": metrics_disable_honored,
        "discovery_disable_honored": discovery_disable_honored,
        "credential_headers_stripped": credential_headers_stripped,
    }
    checks = {
        "commit_matches": commit == source_contract["commit"],
        "package_hash_matches": _sha256(package)
        == source_contract["package_sha256"],
        "lockfile_hash_matches": _sha256(lockfile)
        == source_contract["lockfile_sha256"],
        "working_tree_has_no_unexpected_changes": not unexpected_status,
        **compatibility,
    }
    compatible = all(compatibility.values())
    qualified = all(checks.values()) and compatible
    composite_revision = _sha256(
        json.dumps(
            {
                "commit": commit,
                "package_sha256": _sha256(package),
                "lockfile_sha256": _sha256(lockfile),
                "source_hashes": source_hashes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    return {
        "schema": "sklegal-skgateway-chiap01-preflight-result/v1",
        "card": EXPECTED_CARD,
        "observed_at": datetime.now(UTC).isoformat(),
        "source_commit": commit,
        "source_composite_revision": composite_revision,
        "package_sha256": _sha256(package),
        "lockfile_sha256": _sha256(lockfile),
        "source_hashes": source_hashes,
        "checks": checks,
        "unexpected_worktree_entries": unexpected_status,
        "wire_compatible": compatible,
        "live_controls": {control: False for control in EXPECTED_CONTROLS},
        "qualified_for_protected_traffic": False,
        "activation_permitted": False,
        "result": "PASS" if qualified else "FAIL_CLOSED",
    }


def inspect_composition(
    repository_root: Path, deployment: dict[str, Any]
) -> dict[str, Any]:
    """Verify the exact SKLegal endpoint composition without runtime secrets."""

    composition = deployment["composition"]
    hashes: dict[str, str] = {}
    texts: dict[str, str] = {}
    for relative in COMPOSITION_FILES:
        payload = _read_regular(repository_root / relative, maximum=4 * 1024 * 1024)
        hashes[relative] = _sha256(payload)
        try:
            texts[relative] = payload.decode("ascii")
        except UnicodeDecodeError as exc:
            raise QualificationError(
                f"composition file is not ASCII: {relative}"
            ) from exc

    source_text = texts[COMPOSITION_FILES[0]]
    deployment_text = texts[COMPOSITION_FILES[1]]
    checks = {
        "source_hash_matches": hashes[COMPOSITION_FILES[0]]
        == composition["source_sha256"],
        "deployment_hash_matches": hashes[COMPOSITION_FILES[1]]
        == composition["deployment_sha256"],
        "revision_matches": EXPECTED_COMPOSITION_REVISION in source_text,
        "endpoint_path_matches": composition["endpoint_path"] in source_text,
        "service_identity_matches": EXPECTED_SERVICE_IDENTITY in deployment_text,
        "loopback_binding_matches": '"bind": "loopback-only"' in deployment_text,
        "profile_disabled": '"enabled": false' in deployment_text,
        "allow_cache_disabled": '"allow_cache": false' in deployment_text,
    }
    return {
        "revision": composition["revision"],
        "sklegal_commit": composition["sklegal_commit"],
        "endpoint_binding": (
            f'{composition["bind_host"]}:{composition["endpoint_path"]}'
        ),
        "transport": composition["transport"],
        "service_identity": EXPECTED_SERVICE_IDENTITY,
        "artifact_hashes": hashes,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL_CLOSED",
    }


def load_synthetic_matrix(path: Path) -> dict[str, Any]:
    """Load and validate non-activating public synthetic evidence."""

    raw = _read_regular(path, maximum=256 * 1024)
    if any(byte >= 128 for byte in raw):
        raise QualificationError("synthetic matrix must be ASCII")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError("synthetic matrix is malformed") from exc
    if not isinstance(value, dict):
        raise QualificationError("synthetic matrix must be an object")
    if (
        value.get("schema")
        != "sklegal-skgateway-chiap01-synthetic-matrix/v1"
        or value.get("card") != EXPECTED_CARD
        or value.get("fixture_only") is not True
        or value.get("activation_permitted") is not False
        or value.get("protected_traffic") is not False
    ):
        raise QualificationError("synthetic matrix identity is not fail closed")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list):
        raise QualificationError("synthetic scenarios are missing")
    scenario_ids = {
        item.get("id") for item in scenarios if isinstance(item, dict)
    }
    if scenario_ids != REQUIRED_SCENARIOS or len(scenarios) != len(REQUIRED_SCENARIOS):
        raise QualificationError("synthetic scenario set is not exact")
    return value


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="ascii",
    )
    os.replace(temporary, path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, default=DEFAULT_DEPLOYMENT)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="return nonzero unless the source is wire-compatible",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        deployment = load_deployment(args.deployment)
        report = inspect_source(args.source_dir, deployment)
        report["composition"] = inspect_composition(
            args.repository_root, deployment
        )
        matrix = load_synthetic_matrix(args.matrix)
        report["synthetic_matrix"] = {
            "sha256": _sha256(args.matrix.read_bytes()),
            "scenario_count": len(matrix["scenarios"]),
            "fixture_only": True,
        }
        if report["composition"]["result"] != "PASS":
            report["result"] = "FAIL_CLOSED"
    except QualificationError as exc:
        print(f"FAIL_CLOSED: {exc}", file=sys.stderr)
        return 2
    if args.report is not None:
        write_report(args.report, report)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.require_pass and report["result"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
