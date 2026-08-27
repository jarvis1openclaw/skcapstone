"""Static fail-closed contract for the durable public-synthetic composition."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/chiap01/compose.mvp.yml"
CORE_SQL = ROOT / "deploy/chiap01/mvp/core/core.sql.tmpl"
RETRIEVAL_SQL = ROOT / "deploy/chiap01/mvp/retrieval/retrieval.sql.tmpl"
RUNTIME = ROOT / "services/api/src/sklegal_api/durable_public_synthetic.py"
QUALIFICATION = ROOT / "scripts/qualify_durable_mvp.py"


def _composition() -> dict[str, object]:
    environment = {
        **os.environ,
        "SKLEGAL_MVP_CORE_VOLUME": "public-synthetic-core-volume",
        "SKLEGAL_MVP_RETRIEVAL_VOLUME": "public-synthetic-retrieval-volume",
    }
    for role in ("CORE_ADMIN", "CORE_APP", "RETRIEVAL_ADMIN", "RETRIEVAL_APP"):
        environment[f"SKLEGAL_MVP_{role}_PASSWORD"] = "x"
    result = subprocess.run(
        ["docker", "compose", "--file", str(COMPOSE), "config", "--format", "json"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_dual_postgres_has_distinct_pinned_failure_domains() -> None:
    composition = _composition()
    services = composition["services"]
    core = services["core"]
    retrieval = services["retrieval"]
    assert core["image"].startswith("postgres:17.7-alpine@sha256:")
    assert retrieval["image"].startswith("sklegal-pgvector:17.7-0.8.0-")
    assert core["environment"]["POSTGRES_USER"] == "sklegal_core_admin"
    assert retrieval["environment"]["POSTGRES_USER"] == "sklegal_retrieval_admin"
    assert set(core["networks"]) == {"core-private"}
    assert set(retrieval["networks"]) == {"retrieval-private"}
    assert core["volumes"][0]["source"] != retrieval["volumes"][0]["source"]
    assert core["ports"][0]["host_ip"] == "127.0.0.1"
    assert retrieval["ports"][0]["host_ip"] == "127.0.0.1"


def test_schema_forces_rls_and_keeps_retrieval_derived() -> None:
    core = CORE_SQL.read_text(encoding="utf-8")
    retrieval = RETRIEVAL_SQL.read_text(encoding="utf-8")
    assert "NOBYPASSRLS" in core
    assert "FORCE ROW LEVEL SECURITY" in core
    assert "audit_events" in core and "outbox" in core
    assert "projection_registry" in core and "policy_state" in core
    assert "CREATE EXTENSION vector" in retrieval
    assert "FORCE ROW LEVEL SECURITY" in retrieval
    assert "outbox_sequence" in retrieval and "idempotency_key" in retrieval
    assert "audit_events" not in retrieval and "browser_sessions" not in retrieval


def test_production_runtime_contains_no_in_memory_dependency() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert 'mode="production"' in runtime
    assert "InMemory" not in runtime
    assert "self._capability_lock = Lock()" in runtime
    assert "with self._capability_lock" in runtime
    assert "capabilities_by_request" not in CORE_SQL.read_text(encoding="utf-8")
    assert "FileTrustedIssuerBackend" in runtime
    assert "DurableAuthorizationAuditSink" in runtime


def test_qualification_covers_recovery_and_exact_cleanup() -> None:
    qualification = QUALIFICATION.read_text(encoding="utf-8")
    for required in (
        "session_reload_csp_qualification.mjs",
        "_rebuild_projection",
        "_backup_restore",
        "_reset",
        '"stop", "core"',
        '"start", "core"',
        '"stop", "retrieval"',
        '"down", "--volumes", "--remove-orphans"',
        "cross_tenant",
        "cross_matter",
        "stale_policy_failed_closed",
    ):
        assert required in qualification
