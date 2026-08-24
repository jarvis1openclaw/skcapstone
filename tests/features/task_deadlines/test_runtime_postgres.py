from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.task_deadlines.contracts import UpsertTaskCommand
from sklegal_api.features.task_deadlines.router import build_task_deadline_router
from sklegal_api.features.task_deadlines.service import (
    StaticSimulationGateVerifier,
    StaticTaskDeadlinePolicy,
    TaskDeadlineService,
)
from sklegal_capauth import (
    BoundaryScope,
    Capability,
    PrincipalContext,
    PrincipalType,
    Purpose,
)
from sklegal_persistence.features.task_deadlines.postgres import (
    PostgresTaskDeadlineRepository,
)

from tests.integration.persistence_contract_support import POSTGRES_IMAGE
from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

from .helpers import HASH_D, MATTER, OTHER_MATTER, PRINCIPAL, TENANT

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "0060_task_deadlines.sql"
RUNTIME = "sklegal_runtime"
CLIENT = UUID("11000000-0000-4000-8000-000000000001")
ENGAGEMENT = UUID("12000000-0000-4000-8000-000000000001")
T = TypeVar("T")


def _up(path: Path) -> str:
    return (
        path.read_text(encoding="utf-8")
        .split("-- sklegal:down", 1)[0]
        .replace("-- sklegal:up", "", 1)
    )


def _down(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("-- sklegal:down", 1)[1]


def _psql(
    container: str,
    user: str,
    sql: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
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


def _run_script(container: str, script: str, *arguments: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / script),
            "--docker-container",
            container,
            *arguments,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip())


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, UUID):
        return f"'{value}'::uuid"
    if isinstance(value, datetime):
        return "'" + value.isoformat().replace("'", "''") + "'::timestamptz"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"unsupported PostgreSQL test parameter: {type(value).__name__}")


def _render(sql: str, parameters: tuple[object, ...]) -> str:
    rendered = sql
    for value in parameters:
        rendered = rendered.replace("%s", _literal(value), 1)
    if "%s" in rendered:
        raise ValueError("PostgreSQL test parameters are incomplete")
    return rendered


class PersistentPsqlTransaction:
    def __init__(self, container: str) -> None:
        self.process = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                container,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set",
                "ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                "--username",
                RUNTIME,
                "--dbname",
                "sklegal",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("persistent psql pipes are unavailable")

    def _command(self, sql: str, *, rows: bool) -> list[Mapping[str, object]]:
        marker = f"sklegal_marker_{uuid.uuid4().hex}"
        statement = (
            f"SELECT row_to_json(sklegal_row)::text FROM ({sql}) AS sklegal_row;"
            if rows
            else sql
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(statement.rstrip().rstrip(";") + ";\n")
        self.process.stdin.write(f"SELECT '{marker}';\n")
        self.process.stdin.flush()
        output: list[str] = []
        while True:
            line = self.process.stdout.readline()
            if line == "":
                error = ""
                if self.process.stderr is not None:
                    error = self.process.stderr.read().strip()
                raise RuntimeError("persistent psql failed: " + error)
            value = line.rstrip("\n")
            if value == marker:
                break
            if value:
                output.append(value)
        if not rows:
            return []
        return [json.loads(value) for value in output]

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self._command(_render(sql, parameters), rows=False)

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        rows = self._command(_render(sql, parameters), rows=True)
        return rows[0] if rows else None

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        return self._command(_render(sql, parameters), rows=True)

    def close(self) -> None:
        if self.process.stdin is not None and self.process.poll() is None:
            self.process.stdin.write("\\q\n")
            self.process.stdin.flush()
        self.process.wait(timeout=10)


def _runner(container: str) -> Callable[[Callable[[PersistentPsqlTransaction], T]], T]:
    def run(callback: Callable[[PersistentPsqlTransaction], T]) -> T:
        transaction = PersistentPsqlTransaction(container)
        try:
            transaction._command("BEGIN", rows=False)
            result = callback(transaction)
            transaction._command("COMMIT", rows=False)
            return result
        except Exception:
            if transaction.process.poll() is None:
                transaction._command("ROLLBACK", rows=False)
            raise
        finally:
            transaction.close()

    return run


@pytest.fixture(scope="module")
def postgres() -> Iterator[str]:
    available = subprocess.run(
        ["docker", "image", "inspect", POSTGRES_IMAGE],
        text=True,
        capture_output=True,
        check=False,
    )
    if available.returncode != 0:
        pytest.skip("pinned PostgreSQL image is unavailable")
    container = f"sklegal-task01f-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--label",
            "com.sklegal.test-card=217a9704",
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
            POSTGRES_IMAGE,
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    try:
        for attempt in range(160):
            ready = _psql(container, "postgres", "SELECT 1;", check=False)
            if ready.returncode == 0:
                break
            if attempt == 159:
                raise RuntimeError("disposable PostgreSQL readiness timeout")
            time.sleep(0.1)
        _psql(
            container,
            "postgres",
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        _run_script(container, "provision_postgres_runtime.py")
        _psql(container, "sklegal_migrator", "CREATE SCHEMA sklegal_migrations;")
        manifest = json.loads(
            (ROOT / "migrations" / "manifest.json").read_text(encoding="utf-8")
        )
        for entry in manifest["migrations"]:
            _psql(
                container,
                "sklegal_migrator",
                _up(ROOT / "migrations" / str(entry["file"])),
            )
        _psql(
            container,
            "postgres",
            f"""
            INSERT INTO sklegal_identity.tenants (id, tenant_id, slug, name, status)
            VALUES ('{TENANT}', '{TENANT}', 'task-alpha', 'Task Alpha', 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, authentication_subject)
            VALUES ('{PRINCIPAL}', '{TENANT}', 'service', 'Task Runtime',
                    'synthetic:service:task-runtime');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES ('{TENANT}', '{PRINCIPAL}', 'member');
            INSERT INTO sklegal_legal.clients
                (id, tenant_id, display_name, client_kind, status)
            VALUES ('{CLIENT}', '{TENANT}', 'Task Client', 'person', 'active');
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, status, valid_from)
            VALUES ('{ENGAGEMENT}', '{TENANT}', '{CLIENT}', 'Task Engagement',
                    'Public synthetic task scope', 'active',
                    '2026-01-01T00:00:00Z');
            INSERT INTO sklegal_legal.matters
                (id, tenant_id, matter_id, client_id, engagement_id, title,
                 summary, status, opened_at)
            VALUES ('{MATTER}', '{TENANT}', '{MATTER}', '{CLIENT}', '{ENGAGEMENT}',
                    'Task Matter', 'Public synthetic only.', 'open',
                    '2026-01-02T00:00:00Z'),
                   ('{OTHER_MATTER}', '{TENANT}', '{OTHER_MATTER}', '{CLIENT}',
                    '{ENGAGEMENT}', 'Other Matter', 'Isolation control.', 'open',
                    '2026-01-03T00:00:00Z');
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{TENANT}', '{MATTER}', '{PRINCIPAL}', 'member');
            """,
        )
        _run_script(
            container,
            "provision_postgres_principal.py",
            "--runtime-role",
            RUNTIME,
            "--tenant-id",
            str(TENANT),
            "--principal-id",
            str(PRINCIPAL),
        )
        _psql(container, "sklegal_migrator", _up(MIGRATION))
        yield container
        _psql(container, "sklegal_migrator", _down(MIGRATION))
        assert (
            _psql(
                container,
                "postgres",
                "SELECT to_regnamespace('sklegal_task_deadline') IS NULL;",
            ).stdout.strip()
            == "t"
        )
        _psql(container, "sklegal_migrator", _up(MIGRATION))
        assert (
            _psql(
                container,
                "postgres",
                "SELECT to_regnamespace('sklegal_task_deadline') IS NOT NULL;",
            ).stdout.strip()
            == "t"
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            text=True,
            capture_output=True,
            check=False,
        )
        remaining = subprocess.run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"name=^{container}$",
                "--filter",
                "label=com.sklegal.test-card=217a9704",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert remaining.stdout.strip() == ""


def test_runtime_grants_are_exact_and_update_delete_remain_denied(
    postgres: str,
) -> None:
    result = _psql(
        postgres,
        "postgres",
        f"""
        SELECT has_schema_privilege('{RUNTIME}', 'sklegal_task_deadline', 'USAGE'),
               has_table_privilege(
                   '{RUNTIME}', 'sklegal_task_deadline.task_versions', 'SELECT'),
               has_table_privilege(
                   '{RUNTIME}', 'sklegal_task_deadline.task_versions', 'INSERT'),
               has_table_privilege(
                   '{RUNTIME}', 'sklegal_task_deadline.task_versions', 'UPDATE'),
               has_table_privilege(
                   '{RUNTIME}', 'sklegal_task_deadline.task_versions', 'DELETE');
        """,
    )
    assert result.stdout.strip() == "t|t|t|f|f"


def test_real_capauth_route_uses_runtime_postgres_repository(postgres: str) -> None:
    rig = CapabilityTestRig()
    principal = PrincipalContext(
        principal_id=PRINCIPAL,
        principal_type=PrincipalType.SERVICE,
        subject="synthetic:service:task-runtime",
        tenant_id=TENANT,
    )
    rig.principals.set(principal, active=True)
    repository = PostgresTaskDeadlineRepository(_runner(postgres))
    assert _runner(postgres)(
        lambda transaction: transaction.fetch_one("SELECT 1 AS value", ())
    ) == {"value": 1}
    assert repository.is_matter_member(TENANT, MATTER, PRINCIPAL) is True
    policy = StaticTaskDeadlinePolicy(
        grants={
            (
                TENANT,
                MATTER,
                PRINCIPAL,
                "matter.manage",
                "matter_management",
            ),
            (TENANT, MATTER, PRINCIPAL, "matter.read", "matter_management"),
        },
        revision=HASH_D,
        valid_until=rig.clock() + timedelta(hours=1),
    )
    service = TaskDeadlineService(
        repository=repository,
        policy=policy,
        simulation_gate=StaticSimulationGateVerifier(set()),
        clock=rig.clock,
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
        build_task_deadline_router(
            service=service,
            authorizer=rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
    )
    client = TestClient(app)

    def token(capability: Capability, route: str, matter_id: UUID = MATTER) -> str:
        grant = rig.grant(
            capability=capability,
            purpose=Purpose.MATTER_MANAGEMENT,
            target=f"api:task_deadlines.{route}",
            tenant_id=TENANT,
            matter_id=matter_id,
            resource_id=str(matter_id),
        )
        return raw_leaf(rig.issue(principal, grant))

    try:
        command = UpsertTaskCommand(
            title="Durable router Task",
            description="CapAuth to runtime PostgreSQL proof.",
        )
        created = client.post(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": (
                    "Bearer " + token(Capability.MATTER_MANAGE, "manage")
                ),
                "Idempotency-Key": "runtime-router-task",
            },
            json=command.model_dump(mode="json", by_alias=True),
        )
        assert created.status_code == 201, created.text
        listed = client.get(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": "Bearer " + token(Capability.MATTER_READ, "read")
            },
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["tasks"][0]["title"] == "Durable router Task"
        counts = _psql(
            postgres,
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_task_deadline.task_versions),
                   (SELECT count(*) FROM sklegal_task_deadline.audit_events),
                   (SELECT count(*) FROM sklegal_task_deadline.outbox),
                   (SELECT count(*) FROM sklegal_task_deadline.idempotency_receipts);
            """,
        )
        assert counts.stdout.strip() == "1|1|1|1"

        denied = client.post(
            f"/v1/matters/{OTHER_MATTER}/tasks",
            headers={
                "Authorization": (
                    "Bearer " + token(Capability.MATTER_MANAGE, "manage", OTHER_MATTER)
                ),
                "Idempotency-Key": "cross-matter-runtime-task",
            },
            json=command.model_dump(mode="json", by_alias=True),
        )
        assert denied.status_code == 403, denied.text

        _psql(
            postgres,
            "postgres",
            "REVOKE INSERT ON sklegal_task_deadline.outbox FROM sklegal_runtime;",
        )
        outage = client.post(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": (
                    "Bearer " + token(Capability.MATTER_MANAGE, "manage")
                ),
                "Idempotency-Key": "runtime-outbox-outage",
            },
            json=command.model_copy(
                update={"title": "Atomic outage control"}
            ).model_dump(mode="json", by_alias=True),
        )
        assert outage.status_code == 503, outage.text
        after_outage = _psql(
            postgres,
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_task_deadline.task_versions),
                   (SELECT count(*) FROM sklegal_task_deadline.audit_events),
                   (SELECT count(*) FROM sklegal_task_deadline.outbox),
                   (SELECT count(*) FROM sklegal_task_deadline.idempotency_receipts);
            """,
        )
        assert after_outage.stdout.strip() == "1|1|1|1"
        _psql(
            postgres,
            "postgres",
            "GRANT INSERT ON sklegal_task_deadline.outbox TO sklegal_runtime;",
        )
    finally:
        client.close()
        rig.close()
