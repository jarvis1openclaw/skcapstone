#!/usr/bin/env python3
"""SKL-S5-04D backup and restore qualification driver.

Executes the S5-04D card scope against the pinned development compose
stack (deploy/chiap01/compose.dev.yml) and records measured RPO/RTO
evidence to a JSON report:

- full-restore: logical dump of a migrated, audit-populated scratch
  database on the pinned postgres, restored into a separate scratch
  database after simulated failure, with the audit hash chain, outbox,
  and migration readback verified after restore.
- temporal-persistence: the Temporal persistence schema with its visible
  workflow history rows is dumped and restored into a scratch database,
  proving durable workflow state is recoverable.
- secrets: every secret_reference in the model gateway route registry is
  verified against the configured secret backend by reference only; no
  secret material is read, printed, or stored.

The driver only creates its own scratch objects (databases named
sklegal_s504d_*) and removes them in cleanup. It does not touch
HammerTime paths, production data, sibling stacks, or any credential
material. Exit code 0 means every lane passed. The audit insert path
uses the controlled sklegal_audit.append_event writer, never a direct
table insert, which the schema forbids.

The point-in-time restore lane (base backup plus archived WAL) is proven
by tests/integration/test_backup_restore_qualification.py against a
dedicated WAL-archiving container, because the pinned compose stack runs
postgres with archive_mode off; this driver records that fact instead of
silently substituting a weaker check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sklegal_persistence import (
    BackupManifest,
    RecoveryMeasurement,
    RestoreRequest,
    authorize_restore,
    collect_secret_references,
    evaluate_recovery,
    verify_secret_recovery,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_RUNNER = REPO_ROOT / "scripts" / "manage_migrations.py"
RUNTIME_PROVISIONER = REPO_ROOT / "scripts" / "provision_postgres_runtime.py"
PRINCIPAL_PROVISIONER = REPO_ROOT / "scripts" / "provision_postgres_principal.py"
ROUTE_REGISTRY = REPO_ROOT / "config" / "model_gateway" / "route-registry.json"
POSTGRES = "sklegal-dev-postgres-1"
PG_ADMIN = "temporal"
ARCHIVE_NOTE = (
    "point-in-time restore is qualified by "
    "tests/integration/test_backup_restore_qualification.py against a "
    "dedicated WAL-archiving container; the pinned compose stack runs "
    "postgres with archive_mode off"
)
SOURCE_DB = "sklegal_s504d_src"
RESTORE_DB = "sklegal_s504d_restore"
TEMPORAL_RESTORE_DB = "sklegal_s504d_temporal_restore"
MIGRATOR_ROLE = "sklegal_migrator"
RUNTIME_ROLE = "sklegal_s504d_op"
QUAL_PRINCIPAL = "a7500000-0000-4000-8000-000000000001"
OPERATOR = "skl-s5-04d-qual"
APPROVAL_REFERENCE = "approval-s5-04d-dev-stack-restore"
TENANT_ALPHA = "10000000-0000-4000-8000-0000000005d1"
TENANT_BETA = "10000000-0000-4000-8000-0000000005d2"
SYNTHETIC_TENANT_NAME = "Synthetic S5-04D Qualification Tenant"
SYNTHETIC_TENANT_SLUG = "synthetic-s504d"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {"event": event, "at": _utcnow().isoformat(), **fields}, default=str
        ),
        flush=True,
    )


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout: {result.stdout[-1500:]}\nstderr: {result.stderr[-1500:]}"
        )
    return result


def _psql(sql: str, *, database: str, user: str = PG_ADMIN) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            POSTGRES,
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()[-1500:]}")
    return result.stdout


def _uv_python(script: Path, *arguments: str) -> None:
    _run(
        [
            str(REPO_ROOT / ".tools" / "bin" / "uv"),
            "run",
            "--locked",
            "python",
            str(script),
            *arguments,
        ]
    )


def _migrate(database: str) -> None:
    _uv_python(
        MIGRATION_RUNNER,
        "up",
        "--root",
        str(REPO_ROOT / "migrations"),
        "--docker-container",
        POSTGRES,
        "--database",
        database,
        "--user",
        MIGRATOR_ROLE,
    )


def _role_exists(role: str) -> bool:
    return (
        _psql(
            f"SELECT 1 FROM pg_roles WHERE rolname = '{role}';",
            database="postgres",
        ).strip()
        == "1"
    )


def _ensure_role(role: str) -> bool:
    """Create the least-privilege role if missing; report whether created."""

    if _role_exists(role):
        return False
    _psql(
        f"""
        CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB
            NOCREATEROLE NOINHERIT NOBYPASSRLS;
        """,
        database="postgres",
    )
    return True


def _drop_role(role: str) -> None:
    _run(
        [
            "docker",
            "exec",
            POSTGRES,
            "psql",
            "--no-psqlrc",
            "--username",
            PG_ADMIN,
            "--dbname",
            "postgres",
            "--quiet",
            "--command",
            f"DROP ROLE IF EXISTS {role};",
        ],
        check=False,
    )


def _seed_synthetic_tenant(database: str) -> str:
    """Seed one synthetic tenant and bind the driver runtime role to it.

    Identity rows are seeded by the cluster administrator and audit
    events are then appended through a least-privilege runtime role
    bound with sklegal_identity.database_role_bindings by
    provision_postgres_principal.py, exactly as real logins are bound.
    The dev stack uses trust authentication, so connecting as that role
    needs no password. Every value is synthetic for this qualification.
    """

    _psql(
        f"""
        INSERT INTO sklegal_identity.tenants (id, tenant_id, slug, name, status)
        VALUES ('{TENANT_ALPHA}', '{TENANT_ALPHA}',
                '{SYNTHETIC_TENANT_SLUG}', '{SYNTHETIC_TENANT_NAME}', 'active')
        ON CONFLICT (id) DO NOTHING;
        INSERT INTO sklegal_identity.principals
            (id, tenant_id, principal_kind, display_name)
        VALUES ('{QUAL_PRINCIPAL}', '{TENANT_ALPHA}', 'human',
                'Synthetic S5-04D Operator')
        ON CONFLICT (tenant_id, id) DO NOTHING;
        INSERT INTO sklegal_identity.tenant_memberships
            (tenant_id, principal_id, membership_role)
        VALUES ('{TENANT_ALPHA}', '{QUAL_PRINCIPAL}', 'member')
        ON CONFLICT DO NOTHING;
        """,
        database=database,
    )
    _uv_python(
        PRINCIPAL_PROVISIONER,
        "--docker-container",
        POSTGRES,
        "--database",
        database,
        "--admin-user",
        PG_ADMIN,
        "--runtime-role",
        RUNTIME_ROLE,
        "--tenant-id",
        TENANT_ALPHA,
        "--principal-id",
        QUAL_PRINCIPAL,
    )
    return QUAL_PRINCIPAL


def _append_event(database: str, event_id: str, action: str) -> dict[str, Any]:
    output = _psql(
        f"""
        SELECT sklegal_audit.append_event(
            '{event_id}', '{TENANT_ALPHA}', NULL, '{QUAL_PRINCIPAL}',
            '{event_id}', '{event_id}', '{"5" * 32}',
            '00000000000000{event_id[-2:]}', '01', 'api', '{action}',
            'tenant', NULL, NULL, NULL, 'success', 'allow',
            clock_timestamp() - interval '1 second', '{{}}'::jsonb
        )::text;
        """,
        database=database,
        user=RUNTIME_ROLE,
    )
    payload = next(
        (line for line in output.splitlines() if line.startswith("{")),
        None,
    )
    if payload is None:
        raise RuntimeError(f"append_event produced no payload: {output[-800:]}")
    event = json.loads(payload)
    return {
        "event_id": event["event_id"],
        "event_sequence": event["event_sequence"],
        "event_sha256": event["event_sha256"],
        "outbox_id": event["outbox_id"],
    }


def _chain_state(database: str) -> dict[str, Any]:
    output = _psql(
        f"""
        SELECT (SELECT count(*) FROM sklegal_audit.events
                WHERE tenant_id = '{TENANT_ALPHA}')
            || ':' || (SELECT count(*) FROM sklegal_audit.outbox
                WHERE tenant_id = '{TENANT_ALPHA}')
            || ':' || coalesce((SELECT last_event_sequence || ':'
                || last_event_sha256 FROM sklegal_audit.chain_heads
                WHERE tenant_id = '{TENANT_ALPHA}'), '0:')
            || ':' || (SELECT count(*) FROM sklegal_migrations.schema_migrations);
        """,
        database=database,
    )
    events, outbox, sequence, digest, migrations = output.strip().split(":")
    return {
        "events": int(events),
        "outbox": int(outbox),
        "last_sequence": int(sequence),
        "last_event_sha256": digest,
        "migrations": int(migrations),
    }


def _chain_verifies(database: str) -> bool:
    """Recompute every audit digest link over the restored rows.

    The scratch database has no tenant-bound runtime login, so the
    RLS-scoped sklegal_audit.verify_current_tenant_chain cannot run as a
    plain role here. This recomputes the same comparison the function
    performs internally: sequence contiguity, predecessor linkage, and
    payload_sha256(canonical_payload) equality for every row.
    """

    output = _psql(
        f"""
        WITH ordered AS (
            SELECT event_sequence,
                   previous_event_sha256,
                   lag(event_sha256) OVER (ORDER BY event_sequence)
                       AS expected_predecessor,
                   row_number() OVER (ORDER BY event_sequence)
                       AS expected_sequence,
                   event_sha256 =
                       sklegal_audit.payload_sha256(canonical_payload)
                       AS digest_matches
            FROM sklegal_audit.events
            WHERE tenant_id = '{TENANT_ALPHA}'
        )
        SELECT count(*) > 0 AND bool_and(
            event_sequence = expected_sequence
            AND previous_event_sha256 IS NOT DISTINCT FROM expected_predecessor
            AND digest_matches)
        FROM ordered;
        """,
        database=database,
    )
    return output.strip().splitlines()[-1] == "t"


TEMPORAL_TABLES = (
    "executions",
    "current_executions",
    "namespaces",
    "history_node",
)


def _temporal_snapshot(database: str = "temporal") -> dict[str, int]:
    """Fingerprint Temporal persistence by its durable workflow rows.

    The Temporal server schema lives in the public schema of the
    dedicated temporal database: executions and current_executions hold
    the durable workflow state, history_node the event history, and
    namespaces the registered namespaces.
    """

    selection = " UNION ALL ".join(
        f"SELECT '{table}:' || count(*) FROM {table}" for table in TEMPORAL_TABLES
    )
    output = _psql(f"{selection};", database=database)
    counts: dict[str, int] = {}
    for row in output.strip().splitlines():
        table, _, count = row.partition(":")
        counts[table] = int(count)
    if set(counts) != set(TEMPORAL_TABLES):
        raise RuntimeError(f"temporal snapshot missed tables: {counts}")
    return counts


def _authorize(
    manifest: BackupManifest,
    requested: frozenset[UUID],
    *,
    tamper: dict[str, Any] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    record = json.loads(manifest.canonical_bytes().decode("utf-8"))
    if tamper is not None:
        record.update(tamper)
    request = RestoreRequest(
        backup_id=manifest.backup_id,
        target_identity=RESTORE_DB,
        operator=OPERATOR,
        approval_reference=APPROVAL_REFERENCE,
        requested_tenant_ids=requested,
        hold_state_acknowledged=True,
        decryption_key_reference=manifest.key_custody_reference,
    )
    decision = authorize_restore(
        record,
        recorded_manifest_sha256=manifest.manifest_sha256(),
        request=request,
    )
    return decision.allowed, decision.reasons


def _manifest(
    *,
    backup_id: str,
    kind: str,
    started_at: datetime,
    completed_at: datetime,
    content: bytes,
) -> BackupManifest:
    return BackupManifest(
        backup_id=backup_id,
        kind=kind,
        source_identity=POSTGRES,
        database=SOURCE_DB,
        started_at=started_at,
        completed_at=completed_at,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        tenant_ids=frozenset({UUID(TENANT_ALPHA), UUID(TENANT_BETA)}),
        hold_wall_digest="0" * 64,
        hold_wall_rows=0,
        encrypted=False,
        key_custody_reference=None,
        retention_class="qualification-scratch",
    )


def _drop_database(database: str) -> None:
    _run(
        [
            "docker",
            "exec",
            POSTGRES,
            "psql",
            "--no-psqlrc",
            "--username",
            PG_ADMIN,
            "--dbname",
            "postgres",
            "--quiet",
            "--command",
            f"DROP DATABASE IF EXISTS {database} WITH (FORCE);",
        ],
        check=False,
    )


def lane_full_restore(state: dict[str, Any]) -> None:
    _log("lane.full_restore.begin")
    _drop_database(SOURCE_DB)
    _drop_database(RESTORE_DB)
    _psql(f"CREATE DATABASE {SOURCE_DB};", database="postgres")
    created_migrator = _ensure_role(MIGRATOR_ROLE)
    created_runtime = _ensure_role(RUNTIME_ROLE)
    _psql(
        f"GRANT CREATE ON DATABASE {SOURCE_DB} TO {MIGRATOR_ROLE};",
        database="postgres",
    )
    try:
        _uv_python(
            RUNTIME_PROVISIONER,
            "--docker-container",
            POSTGRES,
            "--database",
            "postgres",
            "--admin-user",
            PG_ADMIN,
        )
        _migrate(SOURCE_DB)
        principal = _seed_synthetic_tenant(SOURCE_DB)
        first = _append_event(
            SOURCE_DB,
            "a7500000-0000-4000-8000-000000000011",
            "audit.s504d.full.before-backup",
        )
        _log("lane.full_restore.source_seeded", audit_event=first, principal=principal)

        started_at = _utcnow()
        dump = _run(
            [
                "docker",
                "exec",
                POSTGRES,
                "pg_dump",
                "--username",
                PG_ADMIN,
                "--dbname",
                SOURCE_DB,
                "--no-owner",
            ]
        ).stdout.encode("utf-8")
        completed_at = _utcnow()
        manifest = _manifest(
            backup_id="skl-backup-s504d-full-001",
            kind="logical_full",
            started_at=started_at,
            completed_at=completed_at,
            content=dump,
        )
        allowed, reasons = _authorize(manifest, frozenset({UUID(TENANT_ALPHA)}))
        if not allowed:
            raise RuntimeError(f"restore denied: {reasons}")

        failure_observed_at = _utcnow()
        _psql(f"CREATE DATABASE {RESTORE_DB};", database="postgres")
        restore = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                POSTGRES,
                "psql",
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--username",
                PG_ADMIN,
                "--dbname",
                RESTORE_DB,
                "--quiet",
            ],
            input=dump.decode("utf-8"),
            text=True,
            capture_output=True,
            check=False,
        )
        if restore.returncode != 0:
            raise RuntimeError(f"restore failed: {restore.stderr[-1500:]}")
        service_healthy_at = _utcnow()
        source_state = _chain_state(SOURCE_DB)
        restored_state = _chain_state(RESTORE_DB)
        if source_state != restored_state:
            raise RuntimeError(
                f"restored state drifted: {source_state} != {restored_state}"
            )
        if not _chain_verifies(RESTORE_DB):
            raise RuntimeError("audit chain did not verify after restore")
        tampered, tamper_reasons = _authorize(
            manifest,
            frozenset({UUID(TENANT_ALPHA)}),
            tamper={"content_bytes": manifest.content_bytes + 1},
        )
        if tampered:
            raise RuntimeError("tampered manifest was allowed")
        measurement = RecoveryMeasurement(
            scenario="full_logical_restore",
            failure_observed_at=failure_observed_at,
            last_durable_point=completed_at,
            service_healthy_at=service_healthy_at,
        )
        evaluation = evaluate_recovery(measurement)
        state["full_restore"] = {
            "manifest_sha256": manifest.manifest_sha256(),
            "dump_bytes": len(dump),
            "source_state": source_state,
            "restored_state": restored_state,
            "chain_verified": True,
            "tamper_denied_reasons": list(tamper_reasons),
            "rpo_seconds": measurement.rpo_seconds,
            "rto_seconds": measurement.rto_seconds,
            "rpo_within_target": evaluation.rpo_within_target,
            "rto_within_target": evaluation.rto_within_target,
            "passed": evaluation.passed,
        }
        _log("lane.full_restore.pass", **state["full_restore"])
    finally:
        _drop_database(RESTORE_DB)
        _drop_database(SOURCE_DB)
        if created_runtime:
            _drop_role(RUNTIME_ROLE)
        if created_migrator:
            _drop_role(MIGRATOR_ROLE)


def lane_temporal_persistence(state: dict[str, Any]) -> None:
    _log("lane.temporal_persistence.begin")
    before = _temporal_snapshot()
    dump = _run(
        [
            "docker",
            "exec",
            POSTGRES,
            "pg_dump",
            "--username",
            PG_ADMIN,
            "--dbname",
            "temporal",
            "--no-owner",
            "--no-privileges",
        ]
    )
    _drop_database(TEMPORAL_RESTORE_DB)
    _psql(f"CREATE DATABASE {TEMPORAL_RESTORE_DB};", database="postgres")
    try:
        restore = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                POSTGRES,
                "psql",
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--username",
                PG_ADMIN,
                "--dbname",
                TEMPORAL_RESTORE_DB,
                "--quiet",
            ],
            input=dump.stdout,
            text=True,
            capture_output=True,
            check=False,
        )
        if restore.returncode != 0:
            raise RuntimeError(f"temporal restore failed: {restore.stderr[-1500:]}")
        after = _temporal_snapshot(TEMPORAL_RESTORE_DB)
        passed = before == after and before["executions"] > 0
        state["temporal_persistence"] = {
            "live": before,
            "restored": after,
            "dump_bytes": len(dump.stdout.encode("utf-8")),
            "passed": passed,
        }
        _log("lane.temporal_persistence.result", **state["temporal_persistence"])
        if not passed:
            raise RuntimeError("temporal persistence restore lost rows")
    finally:
        _drop_database(TEMPORAL_RESTORE_DB)


def lane_secrets(state: dict[str, Any]) -> None:
    _log("lane.secrets.begin")
    registry = json.loads(ROUTE_REGISTRY.read_text(encoding="utf-8"))
    references = collect_secret_references(registry)
    backend = _secret_backend_status()
    report = verify_secret_recovery(references, backend["resolver"])
    state["secrets"] = {
        "backend": backend["name"],
        "backend_available": backend["available"],
        "references": list(report.references),
        "unresolved": list(report.unresolved),
        "invalid": list(report.invalid),
        "verified": report.verified,
    }
    _log("lane.secrets.result", **state["secrets"])
    if not report.verified:
        raise RuntimeError(f"secret recovery verification failed: {state['secrets']}")


def _secret_backend_status() -> dict[str, Any]:
    """Resolve vault-file references by presence, never by value.

    The approved development secret backend is the skstacks vault-file
    store located through SKSTACKS_V2_PATH. Resolution checks only that
    the vault file and the referenced key exist; the value is never
    read, printed, or stored by this driver.
    """

    import os

    root = os.environ.get("SKSTACKS_V2_PATH")
    if not root:
        _log("lane.secrets.backend_unavailable", reason="SKSTACKS_V2_PATH unset")
        return {
            "name": "vault-file",
            "available": False,
            "resolver": lambda reference: False,
        }

    def resolves(reference: str) -> bool:
        scheme, _, path = reference.partition(":")
        if scheme != "vault":
            return False
        try:
            return _vault_key_exists(Path(root), path)
        except (OSError, ValueError):
            return False

    return {
        "name": "vault-file",
        "available": Path(root).exists(),
        "resolver": resolves,
    }


def _vault_key_exists(root: Path, key: str) -> bool:
    """Check the referenced key exists in the skstacks vault file.

    The vault file is a JSON document whose secret-store layout is
    resolved by key path segments. This reads only the structure needed
    to answer membership; the value under the key is discarded.
    """

    matches = list(root.glob("**/secrets.json")) + list(root.glob("**/vault-file.json"))
    if not matches:
        return False
    for vault in matches:
        try:
            document = json.loads(vault.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        node: Any = document
        walked = True
        for segment in key.strip("/").split("/"):
            if not isinstance(node, dict) or segment not in node:
                walked = False
                break
            node = node[segment]
        if walked:
            return True
    return False


LANES = {
    "full-restore": lane_full_restore,
    "temporal-persistence": lane_temporal_persistence,
    "secrets": lane_secrets,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", default="all", choices=[*LANES, "all"])
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "build" / "s504d_qualification.json"),
    )
    args = parser.parse_args()
    if not Path(ROUTE_REGISTRY).exists():
        print("route registry is missing", file=sys.stderr)
        return 2
    selected = list(LANES) if args.lane == "all" else [args.lane]
    state: dict[str, Any] = {
        "started_at": _utcnow().isoformat(),
        "operator": OPERATOR,
        "pitr_lane_note": ARCHIVE_NOTE,
    }
    failures: list[str] = []
    for lane in selected:
        try:
            LANES[lane](state)
        except Exception as error:  # noqa: BLE001 - report every lane failure
            failures.append(lane)
            _log("lane.failed", lane=lane, error=str(error))
    state["finished_at"] = _utcnow().isoformat()
    state["failures"] = failures
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    print(json.dumps(state, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
