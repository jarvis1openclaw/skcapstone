"""Real networkless PostgreSQL parity and RLS qualification for JOIN-01."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sklegal_api.features.joined_analysis.contract import (
    JoinedAnalysisSnapshotProjection,
)
from sklegal_api.features.joined_analysis.service import (
    projection_provenance_sha256,
    projection_sha256,
)
from sklegal_api.features.joined_analysis.stores import (
    seal_public_synthetic_projection,
)
from sklegal_persistence.features.joined_analysis import (
    CURRENT_PROJECTION_SQL,
    MEMBERSHIP_SQL,
    PostgresJoinedAnalysisRepository,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/mvp/fragments/joined_analysis/public-synthetic-v1.json"
MIGRATION = ROOT / "migrations/0022_joined_analysis_snapshots.sql"
IMAGE = "postgres:17.7-alpine@sha256:" + "".join(
    (
        "a6d31f85",
        "3205ce20",
        "d399df4e",
        "33a0b4c7",
        "15672f23",
        "2f4ee744",
        "0499747e",
        "6e02c126",
    )
)
TENANT_ID = UUID("91000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("91000000-0000-4000-8000-000000000002")
OTHER_MATTER_ID = UUID("91000000-0000-4000-8000-000000000099")
FOREIGN_TENANT_ID = UUID("92000000-0000-4000-8000-000000000001")
FOREIGN_MATTER_ID = UUID("92000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("91000000-0000-4000-8000-000000000004")
CLIENT_ID = UUID("91000000-0000-4000-8000-000000000005")
ENGAGEMENT_ID = UUID("91000000-0000-4000-8000-000000000006")
FOREIGN_CLIENT_ID = UUID("92000000-0000-4000-8000-000000000005")
FOREIGN_ENGAGEMENT_ID = UUID("92000000-0000-4000-8000-000000000006")
RUNTIME_ROLE = "sklegal_joined_analysis_test"


def _sql_literal(value: object) -> str:
    if isinstance(value, UUID):
        return f"'{value}'::uuid"
    raise TypeError("disposable executor accepts UUID parameters only")


def _compile_scoped_projection(
    *, tenant_id: UUID, matter_id: UUID, snapshot_id: UUID
) -> dict[str, object]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    value["tenant_id"] = str(tenant_id)
    value["matter_id"] = str(matter_id)
    value["snapshot"]["snapshot_id"] = str(snapshot_id)
    value["theories"][0]["ledger_projection"]["tenant_id"] = str(tenant_id)
    value["theories"][0]["ledger_projection"]["matter_id"] = str(matter_id)
    unsealed = JoinedAnalysisSnapshotProjection.model_validate(value)
    snapshot = unsealed.snapshot.model_copy(
        update=projection_provenance_sha256(unsealed)
    )
    compiled = unsealed.model_copy(update={"snapshot": snapshot})
    snapshot = snapshot.model_copy(
        update={"projection_sha256": projection_sha256(compiled)}
    )
    return compiled.model_copy(update={"snapshot": snapshot}).model_dump(
        mode="json", by_alias=False
    )


@dataclass
class DisposablePostgres:
    container: str

    def psql(
        self, user: str, sql: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                self.container,
                "psql",
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--username",
                user,
                "--dbname",
                "sklegal",
                "--tuples-only",
                "--no-align",
            ],
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(result.stderr.strip())
        return result

    def execute(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        rendered = sql
        for parameter in parameters:
            rendered = rendered.replace("%s", _sql_literal(parameter), 1)
        if "%s" in rendered:
            raise AssertionError("SQL parameter count drift")
        if sql == MEMBERSHIP_SQL:
            output = self.psql(RUNTIME_ROLE, rendered).stdout.strip()
            return [{"member": output == "t"}]
        if sql == CURRENT_PROJECTION_SQL:
            rendered = rendered.replace(
                "SELECT projection, projection_sha256",
                "SELECT json_build_object('projection', projection, "
                "'projection_sha256', projection_sha256)::text",
            )
            output = self.psql(RUNTIME_ROLE, rendered).stdout.strip()
            return [] if not output else [json.loads(output)]
        raise AssertionError("unexpected repository statement")


@pytest.fixture(scope="module")
def postgres() -> Iterator[DisposablePostgres]:
    if shutil.which("docker") is None:
        pytest.fail("Docker is required for JOIN-01 durable parity")
    container = f"sklegal-join-929c6ada-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--label",
            "com.sklegal.test-card=929c6ada",
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
        pytest.fail(f"disposable PostgreSQL start failed: {started.stderr.strip()}")
    database = DisposablePostgres(container)
    try:
        for attempt in range(120):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "--username",
                    "postgres",
                    "--dbname",
                    "sklegal",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            if attempt == 119:
                pytest.fail("disposable PostgreSQL readiness timed out")
            time.sleep(0.25)
        database.psql(
            "postgres",
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_runtime LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        with tempfile.TemporaryDirectory(prefix="sklegal-join-migrations-") as temp:
            root = Path(temp)
            manifest = json.loads((ROOT / "migrations/manifest.json").read_text())
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            for entry in manifest["migrations"]:
                shutil.copy2(ROOT / "migrations" / entry["file"], root / entry["file"])
            migrated = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts/manage_migrations.py"),
                    "up",
                    "--root",
                    str(root),
                    "--docker-container",
                    container,
                    "--user",
                    "sklegal_migrator",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if migrated.returncode != 0:
                pytest.fail(f"base migrations failed: {migrated.stderr.strip()}")
        migration_text = MIGRATION.read_text(encoding="utf-8")
        up_sql, down_sql = migration_text.split("-- sklegal:down", 1)
        database.psql("sklegal_migrator", down_sql)
        absent = database.psql(
            "sklegal_migrator",
            "SELECT to_regclass('sklegal_legal.joined_analysis_snapshots') IS NULL;",
        )
        if absent.stdout.strip() != "t":
            pytest.fail("joined analysis migration rollback left its table behind")
        database.psql("sklegal_migrator", up_sql)
        database.psql(
            "postgres",
            f"""
            INSERT INTO sklegal_identity.tenants (id, tenant_id, slug, name, status)
            VALUES
                ('{TENANT_ID}', '{TENANT_ID}', 'joined-analysis-test',
                 'Joined analysis test', 'active'),
                ('{FOREIGN_TENANT_ID}', '{FOREIGN_TENANT_ID}',
                 'joined-analysis-foreign', 'Foreign joined analysis test', 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name)
            VALUES ('{PRINCIPAL_ID}', '{TENANT_ID}', 'human', 'Synthetic reviewer');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES ('{TENANT_ID}', '{PRINCIPAL_ID}', 'member');
            INSERT INTO sklegal_legal.clients
                (id, tenant_id, display_name, client_kind, status)
            VALUES
                ('{CLIENT_ID}', '{TENANT_ID}', 'Synthetic Client', 'person', 'active'),
                ('{FOREIGN_CLIENT_ID}', '{FOREIGN_TENANT_ID}', 'Foreign Client',
                 'person', 'active');
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, valid_from, status)
            VALUES
                ('{ENGAGEMENT_ID}', '{TENANT_ID}', '{CLIENT_ID}',
                 'Synthetic Engagement', 'Public fixture only', clock_timestamp(), 'active'),
                ('{FOREIGN_ENGAGEMENT_ID}', '{FOREIGN_TENANT_ID}',
                 '{FOREIGN_CLIENT_ID}', 'Foreign Engagement', 'RLS negative only',
                 clock_timestamp(), 'active');
            INSERT INTO sklegal_legal.matters
                (id, tenant_id, matter_id, client_id, engagement_id, title, summary,
                 status, opened_at)
            VALUES
                ('{MATTER_ID}', '{TENANT_ID}', '{MATTER_ID}', '{CLIENT_ID}',
                 '{ENGAGEMENT_ID}', 'Synthetic Matter', 'Public fixture only',
                 'open', clock_timestamp()),
                ('{OTHER_MATTER_ID}', '{TENANT_ID}', '{OTHER_MATTER_ID}', '{CLIENT_ID}',
                 '{ENGAGEMENT_ID}', 'Other Synthetic Matter', 'RLS negative fixture',
                 'open', clock_timestamp()),
                ('{FOREIGN_MATTER_ID}', '{FOREIGN_TENANT_ID}',
                 '{FOREIGN_MATTER_ID}', '{FOREIGN_CLIENT_ID}',
                 '{FOREIGN_ENGAGEMENT_ID}', 'Foreign Synthetic Matter',
                 'Tenant RLS negative fixture',
                 'open', clock_timestamp());
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{TENANT_ID}', '{MATTER_ID}', '{PRINCIPAL_ID}', 'reviewer');
            CREATE ROLE {RUNTIME_ROLE} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            """,
        )
        provisioned = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/provision_postgres_principal.py"),
                "--docker-container",
                container,
                "--runtime-role",
                RUNTIME_ROLE,
                "--tenant-id",
                str(TENANT_ID),
                "--principal-id",
                str(PRINCIPAL_ID),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if provisioned.returncode != 0:
            pytest.fail(f"runtime provisioning failed: {provisioned.stderr.strip()}")

        first = seal_public_synthetic_projection(
            json.loads(FIXTURE.read_text(encoding="utf-8"))
        ).model_dump(mode="json", by_alias=False)
        second = _compile_scoped_projection(
            tenant_id=TENANT_ID,
            matter_id=OTHER_MATTER_ID,
            snapshot_id=OTHER_MATTER_ID,
        )
        foreign = _compile_scoped_projection(
            tenant_id=FOREIGN_TENANT_ID,
            matter_id=FOREIGN_MATTER_ID,
            snapshot_id=FOREIGN_MATTER_ID,
        )

        def insert_projection(value: dict[str, object]) -> None:
            snapshot = value["snapshot"]
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
            database.psql(
                "postgres",
                f"""
                INSERT INTO sklegal_legal.joined_analysis_snapshots
                    (tenant_id, matter_id, snapshot_id, version, observed_at,
                     matter_snapshot_sha256, claim_projection_revision,
                     authority_snapshot, projection_revision, projection_sha256,
                     projection)
                VALUES (
                    '{value["tenant_id"]}', '{value["matter_id"]}',
                    '{snapshot["snapshot_id"]}', {snapshot["version"]},
                    '{snapshot["observed_at"]}', '{snapshot["matter_snapshot_sha256"]}',
                    '{snapshot["claim_projection_revision"]}',
                    '{snapshot["authority_snapshot"]}',
                    '{snapshot["projection_revision"]}',
                    '{snapshot["projection_sha256"]}', $json${encoded}$json$::jsonb
                );
                """,
            )

        insert_projection(first)
        insert_projection(second)
        insert_projection(foreign)
        yield database
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            capture_output=True,
            text=True,
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
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        if remaining.stdout.strip():
            raise AssertionError("JOIN-01 disposable PostgreSQL container leaked")


def test_real_postgres_matches_public_projection_and_enforces_rls(
    postgres: DisposablePostgres,
) -> None:
    repository = PostgresJoinedAnalysisRepository(postgres.execute)
    assert repository.is_matter_member(TENANT_ID, MATTER_ID, PRINCIPAL_ID) is True
    stored = repository.load_current(TENANT_ID, MATTER_ID)
    assert stored is not None
    expected = seal_public_synthetic_projection(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    ).model_dump(mode="json", by_alias=False)
    assert stored.projection == expected
    assert (
        repository.is_matter_member(TENANT_ID, OTHER_MATTER_ID, PRINCIPAL_ID) is False
    )
    assert repository.load_current(TENANT_ID, OTHER_MATTER_ID) is None
    assert (
        repository.is_matter_member(FOREIGN_TENANT_ID, FOREIGN_MATTER_ID, PRINCIPAL_ID)
        is False
    )
    assert repository.load_current(FOREIGN_TENANT_ID, FOREIGN_MATTER_ID) is None


def test_real_postgres_append_only_and_transactional_rollback(
    postgres: DisposablePostgres,
) -> None:
    denied = postgres.psql(
        "postgres",
        f"""
        UPDATE sklegal_legal.joined_analysis_snapshots
        SET projection_revision = '{"8" * 64}'
        WHERE tenant_id = '{TENANT_ID}' AND matter_id = '{MATTER_ID}';
        """,
        check=False,
    )
    assert denied.returncode != 0
    assert "append-only" in denied.stderr

    down_sql = MIGRATION.read_text(encoding="utf-8").split("-- sklegal:down", 1)[1]
    rollback = postgres.psql(
        "sklegal_migrator",
        f"""
        BEGIN;
        {down_sql}
        SELECT to_regclass('sklegal_legal.joined_analysis_snapshots') IS NULL;
        ROLLBACK;
        SELECT to_regclass('sklegal_legal.joined_analysis_snapshots') IS NOT NULL;
        """,
    )
    assert [line for line in rollback.stdout.strip().splitlines() if line == "t"] == [
        "t",
        "t",
    ]
