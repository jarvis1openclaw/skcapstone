from __future__ import annotations

import atexit
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.matter_activity.contracts import ActivityExportCommand
from sklegal_api.features.matter_activity.router import build_matter_activity_router
from sklegal_api.features.matter_activity.service import (
    ActivityAccessContext,
    MatterActivityService,
    StaticActivityPolicy,
)
from sklegal_api.features.matter_activity.store import (
    ActivityAuditFact,
    ActivityIdempotencyConflict,
    ActivityOutboxMessage,
    ActivityStoreUnavailable,
)
from sklegal_capauth import (
    BoundaryScope,
    Capability,
    PrincipalContext,
    PrincipalType,
    Purpose,
)
from sklegal_persistence.features.matter_activity.repository import (
    ActivityProjectionRecord,
    PostgresMatterActivityRepository,
)

from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "0027_matter_activity_projection.sql"
IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f85"
    "3205ce20"
    "d399df4e"
    "33a0b4c7"
    "15672f23"
    "2f4ee744"
    "0499747e"
    "6e02c126"
)
TENANT = UUID("10000000-0000-4000-8000-000000000001")
PRINCIPAL = UUID("10000000-0000-4000-8000-000000000011")
RUNTIME_PRINCIPAL = UUID("10000000-0000-4000-8000-000000000013")
SECOND_RUNTIME_PRINCIPAL = UUID("10000000-0000-4000-8000-000000000014")
OTHER_TENANT = UUID("10000000-0000-4000-8000-000000000002")
OTHER_TENANT_PRINCIPAL = UUID("10000000-0000-4000-8000-000000000015")
MATTER = UUID("10000000-0000-4000-8000-000000000301")
OTHER_MATTER = UUID("10000000-0000-4000-8000-000000000302")
CLIENT = UUID("10000000-0000-4000-8000-000000000101")
ENGAGEMENT = UUID("10000000-0000-4000-8000-000000000201")
OTHER_TENANT_CLIENT = UUID("10000000-0000-4000-8000-000000000102")
OTHER_TENANT_ENGAGEMENT = UUID("10000000-0000-4000-8000-000000000202")
OTHER_TENANT_MATTER = UUID("10000000-0000-4000-8000-000000000303")
SOURCE_ROLE = "sklegal_test_act_source"
RUNTIME_ROLE = "sklegal_runtime"
SECOND_RUNTIME_ROLE = "sklegal_test_act_runtime_2"
OTHER_TENANT_RUNTIME_ROLE = "sklegal_test_act_runtime_other_tenant"
T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _up(path: Path) -> str:
    return (
        path.read_text(encoding="utf-8")
        .split("-- sklegal:down", 1)[0]
        .replace("-- sklegal:up", "", 1)
    )


def _down(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("-- sklegal:down", 1)[1]


def _literal(name: str, value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, UUID):
        return f"'{value}'::uuid"
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'::timestamptz"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, tuple):
        if name == "event_ids":
            return (
                "ARRAY["
                + ",".join(_literal(name, item) for item in value)
                + "]::uuid[]"
            )
        if name == "event_sha256s":
            return (
                "ARRAY["
                + ",".join(_literal(name, item) for item in value)
                + "]::sklegal_legal.sha256_digest[]"
            )
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"unsupported PostgreSQL test parameter: {name}")


class DockerSession:
    def __init__(
        self, harness: type[TestMatterActivityPostgres], role: str = RUNTIME_ROLE
    ) -> None:
        self.harness = harness
        self.role = role

    def execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        rendered = statement
        for name in sorted(parameters, key=len, reverse=True):
            rendered = rendered.replace(f"%({name})s", _literal(name, parameters[name]))
        if "%(" in rendered:
            raise ValueError("PostgreSQL test parameters are incomplete")
        output = self.harness.psql(self.role, rendered).stdout.strip()
        if not output:
            return []
        if "runtime_ready() AS ready" in statement:
            return [{"ready": output == "t"}]
        if "project_event(" in statement:
            return [{"projected": output == "t"}]
        if "snapshot_is_valid(" in statement:
            return [{"valid": output == "t"}]
        if "supersession_graph_is_valid(" in statement:
            return [{"valid": output == "t"}]
        if "AS tenant_head_sequence" in statement:
            head_sequence, head_sha, projected_sequence, projected_sha = output.split(
                "|", 3
            )
            return [
                {
                    "tenant_head_sequence": int(head_sequence),
                    "tenant_head_sha256": head_sha,
                    "projected_sequence": int(projected_sequence),
                    "projected_event_sha256": projected_sha,
                }
            ]
        if "sklegal_activity.propose_export(" in statement:
            return [{"result": json.loads(output)}]
        if "FROM sklegal_activity.entries AS entry" in statement:
            keys = (
                "activity_id",
                "tenant_id",
                "matter_id",
                "event_sequence",
                "event_sha256",
                "previous_event_sha256",
                "action",
                "boundary",
                "outcome",
                "reason_code",
                "occurred_at",
                "recorded_at",
                "actor_principal_id",
                "authorization_decision_id",
                "policy_decision_id",
                "trace",
                "source",
            )
            rows: list[Mapping[str, object]] = []
            for line in output.splitlines():
                values = line.split("|", len(keys) - 1)
                row: dict[str, object] = dict(zip(keys, values, strict=True))
                for key in (
                    "activity_id",
                    "tenant_id",
                    "matter_id",
                    "actor_principal_id",
                    "authorization_decision_id",
                    "policy_decision_id",
                ):
                    row[key] = UUID(str(row[key])) if row[key] else None
                row["event_sequence"] = int(str(row["event_sequence"]))
                row["previous_event_sha256"] = row["previous_event_sha256"] or None
                row["occurred_at"] = datetime.fromisoformat(
                    str(row["occurred_at"]).replace(" ", "T")
                )
                row["recorded_at"] = datetime.fromisoformat(
                    str(row["recorded_at"]).replace(" ", "T")
                )
                row["trace"] = json.loads(str(row["trace"]))
                row["source"] = json.loads(str(row["source"]))
                rows.append(row)
            return rows
        raise AssertionError("unexpected PostgreSQL statement")


class TestMatterActivityPostgres:
    container: str
    source_events: tuple[dict[str, Any], ...]

    @classmethod
    def psql(
        cls, user: str, sql: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                cls.container,
                "psql",
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                "--field-separator=|",
                "--username",
                user,
                "--dbname",
                "sklegal",
            ],
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(result.stderr.strip())
        return result

    @classmethod
    def remove_container(cls) -> None:
        if not getattr(cls, "container", ""):
            return
        subprocess.run(
            ["docker", "rm", "--force", cls.container],
            capture_output=True,
            text=True,
            check=False,
        )

    @classmethod
    def setup_class(cls) -> None:
        cls.container = f"sklegal-act01-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        started = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                cls.container,
                "--label",
                "com.sklegal.test-card=ec0763b9",
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
                "--env",
                "POSTGRES_DB=sklegal",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                IMAGE,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if started.returncode != 0:
            raise AssertionError(started.stderr.strip())
        atexit.register(cls.remove_container)
        for attempt in range(160):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    cls.container,
                    "psql",
                    "--no-psqlrc",
                    "--username",
                    "postgres",
                    "--dbname",
                    "sklegal",
                    "--command",
                    "SELECT 1;",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            if attempt == 159:
                raise RuntimeError("disposable PostgreSQL readiness timeout")
            time.sleep(0.1)
        cls.psql(
            "postgres",
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        provisioned = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "provision_postgres_runtime.py"),
                "--docker-container",
                cls.container,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if provisioned.returncode != 0:
            raise AssertionError(provisioned.stderr.strip())
        cls.psql("sklegal_migrator", "CREATE SCHEMA sklegal_migrations;")
        manifest = json.loads(
            (ROOT / "migrations" / "manifest.json").read_text(encoding="utf-8")
        )
        deferred_v2_migrations: list[dict[str, str]] = []
        for entry in manifest["migrations"]:
            if entry["file"].split("_", 1)[0] >= "0020":
                if entry["file"] == MIGRATION.name:
                    break
                if entry["file"].startswith("0020_"):
                    cls.psql(
                        "sklegal_migrator",
                        _up(ROOT / "migrations" / str(entry["file"])),
                    )
                    continue
                deferred_v2_migrations.append(entry)
                continue
            cls.psql(
                "sklegal_migrator",
                _up(ROOT / "migrations" / str(entry["file"])),
            )
        cls.psql("postgres", cls.seed_sql())
        for role in (SECOND_RUNTIME_ROLE, OTHER_TENANT_RUNTIME_ROLE):
            cls.psql(
                "postgres",
                f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;",
            )
        for role, tenant_id, principal_id in (
            (RUNTIME_ROLE, TENANT, RUNTIME_PRINCIPAL),
            (SECOND_RUNTIME_ROLE, TENANT, SECOND_RUNTIME_PRINCIPAL),
            (
                OTHER_TENANT_RUNTIME_ROLE,
                OTHER_TENANT,
                OTHER_TENANT_PRINCIPAL,
            ),
        ):
            runtime_granted = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "provision_postgres_principal.py"),
                    "--docker-container",
                    cls.container,
                    "--runtime-role",
                    role,
                    "--tenant-id",
                    str(tenant_id),
                    "--principal-id",
                    str(principal_id),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if runtime_granted.returncode != 0:
                raise AssertionError(runtime_granted.stderr.strip())
        cls.psql(
            "postgres",
            f"CREATE ROLE {SOURCE_ROLE} LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOBYPASSRLS;",
        )
        source_provisioned = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "provision_postgres_principal.py"),
                "--docker-container",
                cls.container,
                "--runtime-role",
                SOURCE_ROLE,
                "--tenant-id",
                str(TENANT),
                "--principal-id",
                str(PRINCIPAL),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if source_provisioned.returncode != 0:
            raise AssertionError(source_provisioned.stderr.strip())
        for entry in deferred_v2_migrations:
            cls.psql(
                "sklegal_migrator",
                _up(ROOT / "migrations" / str(entry["file"])),
            )
        cls.psql("sklegal_migrator", _up(MIGRATION))
        for role in (SECOND_RUNTIME_ROLE, OTHER_TENANT_RUNTIME_ROLE):
            cls.psql(
                "sklegal_migrator",
                f"""
            GRANT USAGE ON SCHEMA sklegal_activity TO {role};
            GRANT SELECT ON sklegal_activity.entries,
                sklegal_activity.export_proposals TO {role};
            GRANT EXECUTE ON FUNCTION sklegal_activity.runtime_ready()
                TO {role};
            GRANT EXECUTE ON FUNCTION sklegal_activity.snapshot_is_valid(
                uuid, bigint, sklegal_legal.sha256_digest
            ) TO {role};
            GRANT EXECUTE ON FUNCTION sklegal_activity.supersession_graph_is_valid(
                uuid, uuid
            ) TO {role};
            GRANT EXECUTE ON FUNCTION sklegal_activity.propose_export(
                uuid, uuid, uuid, text, bigint, bigint, integer, uuid[],
                sklegal_legal.sha256_digest[], sklegal_legal.sha256_digest,
                sklegal_legal.sha256_digest, bigint,
                sklegal_legal.sha256_digest, bigint,
                sklegal_legal.sha256_digest, uuid, uuid, uuid,
                sklegal_legal.sha256_digest, sklegal_legal.sha256_digest,
                sklegal_legal.sha256_digest, uuid, uuid, timestamptz
            ) TO {role};
            """,
            )
        cls.source_events = (
            cls.append_source_event(1, "matter_event", "a" * 64),
            cls.append_source_event(2, "workflow_reference", "b" * 64),
        )

    @classmethod
    def teardown_class(cls) -> None:
        try:
            shared_before = cls.psql(
                "postgres",
                """
                SELECT has_schema_privilege(
                           'sklegal_runtime', 'sklegal_audit', 'USAGE'),
                       has_table_privilege(
                           'sklegal_runtime', 'sklegal_audit.chain_heads', 'SELECT'),
                       has_table_privilege(
                           'sklegal_runtime',
                           'sklegal_audit.projection_watermarks', 'SELECT');
                """,
            ).stdout.strip()
            cls.psql("sklegal_migrator", _down(MIGRATION))
            assert (
                cls.psql(
                    "postgres",
                    "SELECT to_regnamespace('sklegal_activity') IS NULL;",
                ).stdout.strip()
                == "t"
            )
            shared_after = cls.psql(
                "postgres",
                """
                SELECT has_schema_privilege(
                           'sklegal_runtime', 'sklegal_audit', 'USAGE'),
                       has_table_privilege(
                           'sklegal_runtime', 'sklegal_audit.chain_heads', 'SELECT'),
                       has_table_privilege(
                           'sklegal_runtime',
                           'sklegal_audit.projection_watermarks', 'SELECT');
                """,
            ).stdout.strip()
            assert shared_before == shared_after == "t|t|t"
            cls.psql("sklegal_migrator", _up(MIGRATION))
            assert (
                cls.psql(
                    "postgres",
                    "SELECT to_regnamespace('sklegal_activity') IS NOT NULL;",
                ).stdout.strip()
                == "t"
            )
        finally:
            cls.remove_container()
            remaining = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name=^{cls.container}$",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert not remaining.stdout.strip()

    @classmethod
    def seed_sql(cls) -> str:
        return f"""
        INSERT INTO sklegal_identity.tenants (id, tenant_id, slug, name, status)
        VALUES ('{TENANT}', '{TENANT}', 'act-alpha', 'Activity Alpha', 'active'),
               ('{OTHER_TENANT}', '{OTHER_TENANT}', 'act-beta',
                'Activity Beta', 'active');
        INSERT INTO sklegal_identity.principals
            (id, tenant_id, principal_kind, display_name)
        VALUES ('{PRINCIPAL}', '{TENANT}', 'human', 'Activity Source');
        INSERT INTO sklegal_identity.principals
            (id, tenant_id, principal_kind, display_name, authentication_subject)
        VALUES ('{RUNTIME_PRINCIPAL}', '{TENANT}', 'service', 'Activity Runtime',
                'synthetic:service:activity-runtime'),
               ('{SECOND_RUNTIME_PRINCIPAL}', '{TENANT}', 'service',
                'Activity Runtime Two', 'synthetic:service:activity-runtime-two'),
               ('{OTHER_TENANT_PRINCIPAL}', '{OTHER_TENANT}', 'service',
                'Activity Runtime Other Tenant',
                'synthetic:service:activity-runtime-other-tenant');
        INSERT INTO sklegal_identity.tenant_memberships
            (tenant_id, principal_id, membership_role)
        VALUES ('{TENANT}', '{PRINCIPAL}', 'member'),
               ('{TENANT}', '{RUNTIME_PRINCIPAL}', 'member'),
               ('{TENANT}', '{SECOND_RUNTIME_PRINCIPAL}', 'member'),
               ('{OTHER_TENANT}', '{OTHER_TENANT_PRINCIPAL}', 'member');
        INSERT INTO sklegal_legal.clients
            (id, tenant_id, display_name, client_kind, status)
        VALUES ('{CLIENT}', '{TENANT}', 'Activity Client', 'person', 'active'),
               ('{OTHER_TENANT_CLIENT}', '{OTHER_TENANT}',
                'Activity Other Tenant Client', 'person', 'active');
        INSERT INTO sklegal_legal.engagements
            (id, tenant_id, client_id, title, scope, status, valid_from)
        VALUES ('{ENGAGEMENT}', '{TENANT}', '{CLIENT}', 'Activity Engagement',
                'Public synthetic activity scope', 'active',
                '2026-01-01T00:00:00Z'),
               ('{OTHER_TENANT_ENGAGEMENT}', '{OTHER_TENANT}',
                '{OTHER_TENANT_CLIENT}', 'Activity Other Tenant Engagement',
                'Public synthetic other Tenant scope', 'active',
                '2026-01-01T00:00:00Z');
        INSERT INTO sklegal_legal.matters
            (id, tenant_id, matter_id, client_id, engagement_id, title,
             summary, status, opened_at)
        VALUES ('{MATTER}', '{TENANT}', '{MATTER}', '{CLIENT}', '{ENGAGEMENT}',
                'Activity Matter', 'Public synthetic only.', 'open',
                '2026-01-02T00:00:00Z'),
               ('{OTHER_MATTER}', '{TENANT}', '{OTHER_MATTER}', '{CLIENT}',
                '{ENGAGEMENT}', 'Other Matter', 'Isolation control.', 'open',
                '2026-01-03T00:00:00Z'),
               ('{OTHER_TENANT_MATTER}', '{OTHER_TENANT}',
                '{OTHER_TENANT_MATTER}', '{OTHER_TENANT_CLIENT}',
                '{OTHER_TENANT_ENGAGEMENT}', 'Other Tenant Matter',
                'Tenant isolation control.', 'open', '2026-01-03T00:00:00Z');
        INSERT INTO sklegal_legal.matter_memberships
            (tenant_id, matter_id, principal_id, membership_role)
        VALUES ('{TENANT}', '{MATTER}', '{PRINCIPAL}', 'member'),
               ('{TENANT}', '{MATTER}', '{RUNTIME_PRINCIPAL}', 'member'),
               ('{TENANT}', '{MATTER}', '{SECOND_RUNTIME_PRINCIPAL}', 'member'),
               ('{TENANT}', '{OTHER_MATTER}', '{PRINCIPAL}', 'member'),
               ('{TENANT}', '{OTHER_MATTER}', '{RUNTIME_PRINCIPAL}', 'member'),
               ('{TENANT}', '{OTHER_MATTER}', '{SECOND_RUNTIME_PRINCIPAL}', 'member'),
               ('{OTHER_TENANT}', '{OTHER_TENANT_MATTER}',
                '{OTHER_TENANT_PRINCIPAL}', 'member');
        """

    @classmethod
    def append_source_event(
        cls,
        sequence: int,
        source_kind: str,
        source_sha256: str,
        *,
        source_version: int = 1,
        matter_id: UUID = MATTER,
    ) -> dict[str, Any]:
        event_id = UUID(f"81000000-0000-4000-8000-{sequence:012d}")
        source_id = UUID(f"82000000-0000-4000-8000-{sequence:012d}")
        result = cls.psql(
            SOURCE_ROLE,
            f"""
            SELECT sklegal_audit.append_event(
                '{event_id}', '{TENANT}', '{matter_id}', '{PRINCIPAL}',
                '83000000-0000-4000-8000-000000000001',
                '84000000-0000-4000-8000-000000000001',
                '85000000000040008000000000000001',
                '{sequence:016x}', '01', 'workflow',
                'matter_activity.source.recorded', '{source_kind}', '{source_id}',
                '86000000-0000-4000-8000-000000000001',
                '87000000-0000-4000-8000-000000000001',
                'success', 'recorded', '2026-08-23T12:0{sequence}:00Z',
                '{{"operation":"read","resource_version":{source_version},'
                '"resource_sha256":"{source_sha256}"'
                '}}'::jsonb
            )::text;
            """,
        )
        return json.loads(result.stdout)

    @classmethod
    @contextmanager
    def session(cls, role: str = RUNTIME_ROLE) -> Iterator[DockerSession]:
        yield DockerSession(cls, role)

    @classmethod
    def record(cls, index: int) -> ActivityProjectionRecord:
        event = cls.source_events[index]
        sequence = index + 1
        return ActivityProjectionRecord(
            tenant_id=TENANT,
            matter_id=MATTER,
            audit_event_id=UUID(str(event["event_id"])),
            event_sequence=int(event["event_sequence"]),
            event_sha256=str(event["event_sha256"]),
            source_kind=("matter_event" if sequence == 1 else "workflow_reference"),
            source_id=UUID(f"82000000-0000-4000-8000-{sequence:012d}"),
            source_version=1,
            source_sha256=("a" if sequence == 1 else "b") * 64,
            source_status="recorded",
            source_recorded_at=T0 + timedelta(minutes=sequence),
            corrects_source_id=None,
            superseded_by_source_id=None,
            superseded_by_source_version=None,
            causation_id=(
                UUID(str(cls.source_events[0]["event_id"])) if sequence == 2 else None
            ),
            workflow_reference_id=(
                UUID(f"82000000-0000-4000-8000-{sequence:012d}")
                if sequence == 2
                else None
            ),
            agent_run_id=None,
            tool_call_id=None,
            idempotency_key_sha256=("c" if sequence == 1 else "d") * 64,
            request_sha256=("e" if sequence == 1 else "f") * 64,
            projection_audit_event_id=UUID(f"88000000-0000-4000-8000-{sequence:012d}"),
            projected_at=datetime.now(UTC),
        )

    def test_durable_projection_pagination_export_and_exact_grants(self) -> None:
        repository = PostgresMatterActivityRepository(self.session)
        assert repository.project(self.record(0))
        assert repository.project(self.record(1))
        assert not repository.project(self.record(0))
        watermark_probe = self.psql(
            RUNTIME_ROLE,
            f"""
            SELECT head.last_event_sequence, head.last_event_sha256,
                   watermark.event_sequence, watermark.event_sha256
            FROM sklegal_audit.chain_heads AS head
            JOIN sklegal_audit.projection_watermarks AS watermark
              ON watermark.tenant_id = head.tenant_id
             AND watermark.projection = 'matter_activity.v1'
            WHERE head.tenant_id = '{TENANT}';
            """,
            check=False,
        )
        assert watermark_probe.returncode == 0, watermark_probe.stderr

        now = datetime.now(UTC)
        policy = StaticActivityPolicy(
            memberships={(TENANT, MATTER, RUNTIME_PRINCIPAL)},
            revision="9" * 64,
            valid_until=now + timedelta(hours=1),
        )
        service = MatterActivityService(
            store=repository, policy=policy, clock=lambda: now
        )
        read = ActivityAccessContext(
            tenant_id=TENANT,
            matter_id=MATTER,
            resource_id=MATTER,
            principal_id=RUNTIME_PRINCIPAL,
            capability="audit.read",
            purpose="audit_review",
            authorization_decision_id=UUID("89000000-0000-4000-8000-000000000001"),
            correlation_id=UUID("8a000000-0000-4000-8000-000000000001"),
            credential_expires_at=now + timedelta(minutes=30),
        )
        first = service.list_activity(
            context=read, matter_id=MATTER, cursor=None, limit=1
        )
        assert [item.event_sequence for item in first.items] == [1]
        assert first.next_cursor is not None
        second = service.list_activity(
            context=read, matter_id=MATTER, cursor=first.next_cursor, limit=1
        )
        assert [item.event_sequence for item in second.items] == [2]
        assert second.watermark.tenant_head_sequence == 4
        assert second.watermark.lag_events == 2

        export_context = read
        command = ActivityExportCommand(
            title="Durable public synthetic activity",
            first_event_sequence=1,
            last_event_sequence=2,
            expected_projected_sequence=second.snapshot_sequence,
            expected_projected_sha256=second.snapshot_sha256,
            expected_resource_version=second.snapshot_sequence,
        )
        proposal = service.propose_export(
            context=export_context,
            matter_id=MATTER,
            idempotency_key="durable-activity-export-001",
            command=command,
        )
        replay = service.propose_export(
            context=export_context,
            matter_id=MATTER,
            idempotency_key="durable-activity-export-001",
            command=command,
        )
        assert replay == proposal
        assert proposal.proposal.status == "proposed"
        assert proposal.proposal.approval_id is None
        assert proposal.proposal.dispatch_state == "not_requested"

        grants = self.psql(
            "postgres",
            """
            SELECT has_table_privilege('sklegal_runtime',
                       'sklegal_activity.entries', 'SELECT'),
                   has_table_privilege('sklegal_runtime',
                       'sklegal_activity.entries', 'INSERT'),
                   has_table_privilege('sklegal_runtime',
                       'sklegal_activity.entries', 'UPDATE'),
                   has_table_privilege('sklegal_runtime',
                       'sklegal_activity.entries', 'DELETE');
            """,
        ).stdout.strip()
        assert grants == "t|f|f|f"
        assert (
            self.psql(
                RUNTIME_ROLE,
                f"SELECT count(*) FROM sklegal_activity.entries "
                f"WHERE matter_id = '{OTHER_MATTER}';",
            ).stdout.strip()
            == "0"
        )
        update = self.psql(
            "postgres",
            "UPDATE sklegal_activity.entries SET source_version = 2;",
            check=False,
        )
        assert update.returncode != 0
        assert "append only" in update.stderr
        evidence = self.psql(
            "postgres",
            """
            SELECT count(*) FILTER (WHERE action = 'matter_activity.projected'),
                   count(*) FILTER (WHERE action = 'matter.activity_export.created'),
                   (SELECT count(*) FROM sklegal_audit.outbox
                    WHERE event_id IN (
                        SELECT id FROM sklegal_audit.events
                        WHERE action IN (
                            'matter_activity.projected',
                            'matter.activity_export.created'
                        )
                    ))
            FROM sklegal_audit.events;
            """,
        ).stdout.strip()
        assert evidence == "2|1|3"
        self.psql(
            "postgres",
            "REVOKE SELECT ON sklegal_audit.chain_heads FROM sklegal_runtime;",
        )
        try:
            with pytest.raises(ActivityStoreUnavailable):
                repository.ensure_ready()
        finally:
            self.psql(
                "postgres",
                "GRANT SELECT ON sklegal_audit.chain_heads TO sklegal_runtime;",
            )
        repository.ensure_ready()

    @classmethod
    def state_counts(cls) -> str:
        return cls.psql(
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_activity.export_proposals),
                   (SELECT count(*) FROM sklegal_activity.projection_receipts),
                   (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox),
                   (SELECT count(*) FROM sklegal_audit.projection_watermarks);
            """,
        ).stdout.strip()

    def test_durable_export_replay_is_bound_to_complete_context(self) -> None:
        repository = PostgresMatterActivityRepository(self.session)
        repository.project(self.record(0))
        repository.project(self.record(1))
        now = datetime.now(UTC)
        policy_revision = "9" * 64
        policy = StaticActivityPolicy(
            memberships={(TENANT, MATTER, RUNTIME_PRINCIPAL)},
            revision=policy_revision,
            valid_until=now + timedelta(hours=1),
        )
        service = MatterActivityService(
            store=repository, policy=policy, clock=lambda: now
        )
        context = ActivityAccessContext(
            tenant_id=TENANT,
            matter_id=MATTER,
            resource_id=MATTER,
            principal_id=RUNTIME_PRINCIPAL,
            capability="audit.read",
            purpose="audit_review",
            authorization_decision_id=UUID("8b000000-0000-4000-8000-000000000001"),
            correlation_id=UUID("8c000000-0000-4000-8000-000000000001"),
            credential_expires_at=now + timedelta(minutes=30),
        )
        page = service.list_activity(
            context=context, matter_id=MATTER, cursor=None, limit=100
        )
        command = ActivityExportCommand(
            title="Context-bound durable activity",
            first_event_sequence=1,
            last_event_sequence=2,
            expected_projected_sequence=page.snapshot_sequence,
            expected_projected_sha256=page.snapshot_sha256,
            expected_resource_version=page.snapshot_sequence,
        )
        response = service.propose_export(
            context=context,
            matter_id=MATTER,
            idempotency_key="durable-replay-context-001",
            command=command,
        )
        proposal = response.proposal
        receipt = self.psql(
            "postgres",
            f"""
            SELECT tenant_id, matter_id, operation, principal_id,
                   authorization_decision_id, policy_decision_id,
                   policy_revision, request_sha256
            FROM sklegal_activity.projection_receipts
            WHERE idempotency_key_sha256 =
                  '{proposal.idempotency_key_sha256}';
            """,
        ).stdout.strip()
        assert receipt == "|".join(
            (
                str(TENANT),
                str(MATTER),
                "create_activity_export",
                str(RUNTIME_PRINCIPAL),
                str(context.authorization_decision_id),
                str(proposal.policy_decision_id),
                policy_revision,
                response.request_sha256,
            )
        )

        audit = ActivityAuditFact(
            fact_id=response.audit_id,
            tenant_id=TENANT,
            matter_id=MATTER,
            principal_id=RUNTIME_PRINCIPAL,
            action="matter.activity_export.created",
            proposal_id=proposal.proposal_id,
            selection_sha256=proposal.selection_sha256,
            occurred_at=proposal.created_at,
        )
        outbox = ActivityOutboxMessage(
            message_id=response.outbox_id,
            tenant_id=TENANT,
            matter_id=MATTER,
            audit_fact_id=response.audit_id,
            proposal_id=proposal.proposal_id,
            selection_sha256=proposal.selection_sha256,
        )
        before = self.state_counts()
        stored, replayed = repository.append_export(
            proposal=proposal,
            policy_revision=policy_revision,
            request_sha256=response.request_sha256,
            audit_fact=audit,
            outbox=outbox,
        )
        assert replayed
        assert stored == proposal
        assert self.state_counts() == before

        second_repository = PostgresMatterActivityRepository(
            lambda: self.session(SECOND_RUNTIME_ROLE)
        )
        other_tenant_repository = PostgresMatterActivityRepository(
            lambda: self.session(OTHER_TENANT_RUNTIME_ROLE)
        )
        variants = (
            (
                second_repository,
                proposal.model_copy(
                    update={"proposed_by_principal_id": SECOND_RUNTIME_PRINCIPAL}
                ),
                policy_revision,
                response.request_sha256,
            ),
            (
                repository,
                proposal.model_copy(
                    update={
                        "authorization_decision_id": UUID(
                            "8b000000-0000-4000-8000-000000000002"
                        )
                    }
                ),
                policy_revision,
                response.request_sha256,
            ),
            (
                repository,
                proposal.model_copy(
                    update={
                        "policy_decision_id": UUID(
                            "8d000000-0000-4000-8000-000000000002"
                        )
                    }
                ),
                policy_revision,
                response.request_sha256,
            ),
            (repository, proposal, "8" * 64, response.request_sha256),
            (
                repository,
                proposal.model_copy(update={"matter_id": OTHER_MATTER}),
                policy_revision,
                response.request_sha256,
            ),
            (
                other_tenant_repository,
                proposal.model_copy(
                    update={
                        "tenant_id": OTHER_TENANT,
                        "matter_id": OTHER_TENANT_MATTER,
                        "proposed_by_principal_id": OTHER_TENANT_PRINCIPAL,
                    }
                ),
                policy_revision,
                response.request_sha256,
            ),
            (repository, proposal, policy_revision, "7" * 64),
            (
                repository,
                proposal.model_copy(
                    update={
                        "idempotency_key_sha256": self.record(0).idempotency_key_sha256
                    }
                ),
                policy_revision,
                response.request_sha256,
            ),
        )
        for selected_repository, changed, revision, request_sha256 in variants:
            with pytest.raises(ActivityIdempotencyConflict) as conflict:
                selected_repository.append_export(
                    proposal=changed,
                    policy_revision=revision,
                    request_sha256=request_sha256,
                    audit_fact=audit,
                    outbox=outbox,
                )
            assert str(conflict.value) == "activity export key conflict"
            assert self.state_counts() == before

    def test_real_capauth_api_denies_cross_principal_replay_without_leakage(
        self,
    ) -> None:
        repository = PostgresMatterActivityRepository(self.session)
        repository.project(self.record(0))
        repository.project(self.record(1))
        second_repository = PostgresMatterActivityRepository(
            lambda: self.session(SECOND_RUNTIME_ROLE)
        )
        rig = CapabilityTestRig()
        principal_one = PrincipalContext(
            principal_id=RUNTIME_PRINCIPAL,
            principal_type=PrincipalType.SERVICE,
            subject="synthetic:service:activity-runtime",
            tenant_id=TENANT,
        )
        principal_two = PrincipalContext(
            principal_id=SECOND_RUNTIME_PRINCIPAL,
            principal_type=PrincipalType.SERVICE,
            subject="synthetic:service:activity-runtime-two",
            tenant_id=TENANT,
        )
        rig.principals.set(principal_one, active=True)
        rig.principals.set(principal_two, active=True)

        def client_for(
            principal: PrincipalContext,
            selected_repository: PostgresMatterActivityRepository,
        ) -> TestClient:
            policy = StaticActivityPolicy(
                memberships={(TENANT, MATTER, principal.principal_id)},
                revision="6" * 64,
                valid_until=rig.clock() + timedelta(hours=1),
            )
            service = MatterActivityService(
                store=selected_repository, policy=policy, clock=rig.clock
            )

            def principal_resolver(_: Request) -> PrincipalContext:
                return principal

            def scope_resolver(request: Request) -> BoundaryScope:
                matter_id = UUID(str(request.path_params["matter_id"]))
                return BoundaryScope(
                    tenant_id=TENANT,
                    matter_id=matter_id,
                    resource_id=str(matter_id),
                )

            app = FastAPI()
            app.include_router(
                build_matter_activity_router(
                    service=service,
                    authorizer=rig.authorizer,
                    principal_resolver=principal_resolver,
                    scope_resolver=scope_resolver,
                )
            )
            return TestClient(app)

        def token(principal: PrincipalContext, route: str) -> str:
            grant = rig.grant(
                capability=Capability.AUDIT_READ,
                purpose=Purpose.AUDIT_REVIEW,
                target=f"api:matter_activity.{route}",
                tenant_id=TENANT,
                matter_id=MATTER,
                resource_id=str(MATTER),
            )
            return raw_leaf(rig.issue(principal, grant))

        first_client = client_for(principal_one, repository)
        second_client = client_for(principal_two, second_repository)
        try:
            listed = first_client.get(
                f"/v1/matters/{MATTER}/activity?limit=100",
                headers={
                    "Authorization": "Bearer " + token(principal_one, "list_activity")
                },
            )
            assert listed.status_code == 200, listed.text
            page = listed.json()
            body = {
                "title": "Real CapAuth context-bound activity",
                "firstEventSequence": 1,
                "lastEventSequence": 2,
                "expectedProjectedSequence": page["snapshotSequence"],
                "expectedProjectedSha256": page["snapshotSha256"],
                "expectedResourceVersion": page["snapshotSequence"],
            }
            headers = {
                "Authorization": "Bearer "
                + token(principal_one, "create_activity_export"),
                "Idempotency-Key": "capauth-replay-context-001",
            }
            created = first_client.post(
                f"/v1/matters/{MATTER}/activity-exports",
                headers=headers,
                json=body,
            )
            assert created.status_code == 201, created.text
            prior = created.json()
            before = self.state_counts()
            denied = second_client.post(
                f"/v1/matters/{MATTER}/activity-exports",
                headers={
                    "Authorization": "Bearer "
                    + token(principal_two, "create_activity_export"),
                    "Idempotency-Key": "capauth-replay-context-001",
                },
                json=body,
            )
            assert denied.status_code == 409, denied.text
            assert denied.json()["detail"]["code"] == "idempotency_conflict"
            for protected in (
                prior["proposal"]["proposalId"],
                prior["proposal"]["authorizationDecisionId"],
                prior["proposal"]["policyDecisionId"],
                prior["proposal"]["contentSha256"],
            ):
                assert protected not in denied.text
            assert self.state_counts() == before
        finally:
            first_client.close()
            second_client.close()
            rig.close()

    def test_hash_scope_and_transaction_failures_leave_no_partial_projection(
        self,
    ) -> None:
        event = self.append_source_event(3, "source_reference", "1" * 64)
        repository = PostgresMatterActivityRepository(self.session)
        record = ActivityProjectionRecord(
            tenant_id=TENANT,
            matter_id=MATTER,
            audit_event_id=UUID(str(event["event_id"])),
            event_sequence=int(event["event_sequence"]),
            event_sha256=str(event["event_sha256"]),
            source_kind="source_reference",
            source_id=UUID("82000000-0000-4000-8000-000000000003"),
            source_version=1,
            source_sha256="2" * 64,
            source_status="recorded",
            source_recorded_at=T0 + timedelta(minutes=3),
            corrects_source_id=None,
            superseded_by_source_id=None,
            superseded_by_source_version=None,
            causation_id=None,
            workflow_reference_id=None,
            agent_run_id=None,
            tool_call_id=None,
            idempotency_key_sha256="3" * 64,
            request_sha256="4" * 64,
            projection_audit_event_id=UUID("88000000-0000-4000-8000-000000000003"),
            projected_at=datetime.now(UTC),
        )
        before = self.psql(
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_activity.entries),
                   (SELECT count(*) FROM sklegal_activity.projection_receipts),
                   (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox);
            """,
        ).stdout.strip()
        with pytest.raises(ActivityStoreUnavailable):
            repository.project(record)
        correctly_hashed = replace(record, source_sha256="1" * 64)
        with pytest.raises(ActivityStoreUnavailable):
            repository.project(replace(correctly_hashed, source_kind="approval"))
        with pytest.raises(ActivityStoreUnavailable):
            repository.project(
                replace(
                    correctly_hashed,
                    causation_id=UUID("81000000-0000-4000-8000-000000000099"),
                )
            )
        with pytest.raises(ActivityStoreUnavailable):
            repository.project(
                replace(
                    correctly_hashed,
                    workflow_reference_id=correctly_hashed.source_id,
                )
            )
        with pytest.raises(ActivityStoreUnavailable):
            repository.project(replace(correctly_hashed, matter_id=OTHER_MATTER))
        with pytest.raises(ActivityStoreUnavailable):
            repository.project(
                replace(
                    correctly_hashed,
                    tenant_id=UUID("10000000-0000-4000-8000-000000000002"),
                )
            )
        after = self.psql(
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_activity.entries),
                   (SELECT count(*) FROM sklegal_activity.projection_receipts),
                   (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox);
            """,
        ).stdout.strip()
        assert after == before

    def test_durable_supersession_requires_resolvable_same_scope_lineage(
        self,
    ) -> None:
        predecessor_event = self.append_source_event(
            4, "execution_event", "5" * 64, source_version=2
        )
        successor_event = self.append_source_event(
            5, "execution_event", "6" * 64, source_version=3
        )
        self.append_source_event(
            6,
            "execution_event",
            "7" * 64,
            source_version=3,
            matter_id=OTHER_MATTER,
        )
        predecessor_id = UUID("82000000-0000-4000-8000-000000000004")
        successor_id = UUID("82000000-0000-4000-8000-000000000005")
        base = ActivityProjectionRecord(
            tenant_id=TENANT,
            matter_id=MATTER,
            audit_event_id=UUID(str(predecessor_event["event_id"])),
            event_sequence=int(predecessor_event["event_sequence"]),
            event_sha256=str(predecessor_event["event_sha256"]),
            source_kind="execution_event",
            source_id=predecessor_id,
            source_version=2,
            source_sha256="5" * 64,
            source_status="superseded",
            source_recorded_at=T0 + timedelta(minutes=4),
            corrects_source_id=None,
            superseded_by_source_id=successor_id,
            superseded_by_source_version=3,
            causation_id=None,
            workflow_reference_id=None,
            agent_run_id=None,
            tool_call_id=None,
            idempotency_key_sha256="8" * 64,
            request_sha256="9" * 64,
            projection_audit_event_id=UUID("88000000-0000-4000-8000-000000000004"),
            projected_at=datetime.now(UTC),
        )
        repository = PostgresMatterActivityRepository(self.session)
        before = self.psql(
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_activity.entries),
                   (SELECT count(*) FROM sklegal_activity.projection_receipts),
                   (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox);
            """,
        ).stdout.strip()
        invalid = (
            replace(
                base,
                superseded_by_source_id=UUID("82000000-0000-4000-8000-000000000099"),
            ),
            replace(
                base,
                superseded_by_source_id=UUID("82000000-0000-4000-8000-000000000002"),
            ),
            replace(
                base,
                superseded_by_source_id=UUID("82000000-0000-4000-8000-000000000006"),
            ),
            replace(base, superseded_by_source_version=2),
        )
        for record in invalid:
            with pytest.raises(ActivityStoreUnavailable):
                repository.project(record)
        assert (
            self.psql(
                "postgres",
                """
                SELECT (SELECT count(*) FROM sklegal_activity.entries),
                       (SELECT count(*) FROM sklegal_activity.projection_receipts),
                       (SELECT count(*) FROM sklegal_audit.events),
                       (SELECT count(*) FROM sklegal_audit.outbox);
                """,
            ).stdout.strip()
            == before
        )

        assert repository.project(base)
        graph_before_successor = self.psql(
            RUNTIME_ROLE,
            f"SELECT sklegal_activity.supersession_graph_is_valid("
            f"'{TENANT}', '{MATTER}');",
        ).stdout.strip()
        assert graph_before_successor == "f"
        successor = replace(
            base,
            audit_event_id=UUID(str(successor_event["event_id"])),
            event_sequence=int(successor_event["event_sequence"]),
            event_sha256=str(successor_event["event_sha256"]),
            source_id=successor_id,
            source_version=3,
            source_sha256="6" * 64,
            source_status="recorded",
            source_recorded_at=T0 + timedelta(minutes=5),
            superseded_by_source_id=None,
            superseded_by_source_version=None,
            idempotency_key_sha256="a" * 64,
            request_sha256="b" * 64,
            projection_audit_event_id=UUID("88000000-0000-4000-8000-000000000005"),
            projected_at=datetime.now(UTC),
        )
        assert repository.project(successor)
        assert (
            self.psql(
                RUNTIME_ROLE,
                f"SELECT sklegal_activity.supersession_graph_is_valid("
                f"'{TENANT}', '{MATTER}');",
            ).stdout.strip()
            == "t"
        )
