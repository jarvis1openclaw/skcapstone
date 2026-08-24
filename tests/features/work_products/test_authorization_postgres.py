from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TypeVar
from uuid import UUID

import pytest
from sklegal_persistence.features.work_products.postgres import (
    PostgresWorkProductRepository,
)
from sklegal_persistence.features.work_products.repository import (
    WorkProductRepositoryUnavailable,
)

from .helpers import (
    DIGEST,
    EMPTY_REVOCATION_REVISION,
    MATTER,
    POLICY,
    T0,
    TENANT,
    actor,
)

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "migrations"
MIGRATION_RUNNER = ROOT / "scripts/manage_migrations.py"
RUNTIME_PROVISIONER = ROOT / "scripts/provision_postgres_runtime.py"
FEATURE_MIGRATION = MIGRATIONS / "0050_work_product_feature_lane.sql"
IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126"  # pragma: allowlist secret
)
RUNTIME = "sklegal_runtime"
RUNTIME_PRINCIPAL = "10000000-0000-4000-8000-000000000013"
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
T = TypeVar("T")


def _psql(
    container: str,
    sql: str,
    *,
    user: str = "postgres",
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


def _run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip())
    return result


def _foundation_root(directory: str) -> Path:
    target = Path(directory) / "migrations"
    target.mkdir()
    manifest = json.loads((MIGRATIONS / "manifest.json").read_text(encoding="utf-8"))
    shutil.copy2(MIGRATIONS / "manifest.json", target / "manifest.json")
    for entry in manifest["migrations"]:
        filename = entry["file"]
        shutil.copy2(MIGRATIONS / filename, target / filename)
    return target


def _migration_up(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    up, _ = text.split("-- sklegal:down", 1)
    return up.removeprefix("-- sklegal:up")


@pytest.fixture(scope="module")
def postgres() -> Iterator[str]:
    available = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    if available.returncode != 0:
        pytest.skip("pinned PostgreSQL image is unavailable")
    container = f"sklegal-wp01f-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--label",
            "com.sklegal.test-card=f3271f6d",
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
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        consecutive_ready = 0
        for _ in range(240):
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
                consecutive_ready += 1
                if consecutive_ready == 8:
                    break
            else:
                consecutive_ready = 0
            time.sleep(0.1)
        else:
            raise RuntimeError("disposable PostgreSQL did not become ready")
        inspected = json.loads(
            subprocess.run(
                ["docker", "inspect", container],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )[0]
        assert inspected["Config"]["Labels"]["com.sklegal.test-card"] == "f3271f6d"
        assert inspected["HostConfig"]["NetworkMode"] == "none"
        assert inspected["Mounts"] == []
        assert "/var/lib/postgresql/data" in inspected["HostConfig"]["Tmpfs"]
        _psql(
            container,
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        _run_script(
            str(RUNTIME_PROVISIONER),
            "--docker-container",
            container,
            "--database",
            "sklegal",
        )
        with tempfile.TemporaryDirectory(prefix="sklegal-wp01f-migrations-") as temp:
            foundation = _foundation_root(temp)
            _run_script(
                str(MIGRATION_RUNNER),
                "up",
                "--root",
                str(foundation),
                "--docker-container",
                container,
                "--database",
                "sklegal",
                "--user",
                "sklegal_migrator",
            )
        _psql(
            container,
            f"""
            INSERT INTO sklegal_identity.tenants
                (id, tenant_id, slug, name, status)
            VALUES
                ('{TENANT}', '{TENANT}', 'wp01f-alpha', 'WP01F Alpha', 'active'),
                ('{OTHER_TENANT}', '{OTHER_TENANT}', 'wp01f-beta', 'WP01F Beta', 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, authentication_subject)
            VALUES
                ('{RUNTIME_PRINCIPAL}', '{TENANT}', 'service',
                 'WP01F Runtime', 'synthetic:service:wp01f-runtime');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES ('{TENANT}', '{RUNTIME_PRINCIPAL}', 'member');
            INSERT INTO sklegal_identity.database_role_bindings
                (database_role, tenant_id, principal_id)
            VALUES ('{RUNTIME}', '{TENANT}', '{RUNTIME_PRINCIPAL}');
            SET ROLE sklegal_migrator;
            {_migration_up(FEATURE_MIGRATION)}
            RESET ROLE;
            """,
        )
        yield container
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
                "--filter",
                "label=com.sklegal.test-card=f3271f6d",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert remaining.stdout.strip() == ""


@pytest.fixture(autouse=True)
def reset_revocations(postgres: str) -> Iterator[None]:
    _psql(
        postgres,
        f"""
        DELETE FROM sklegal_identity.capability_revocations
        WHERE tenant_id = '{TENANT}';
        GRANT EXECUTE ON FUNCTION
            sklegal_identity.capability_revocation_snapshot(
                uuid, sklegal_legal.sha256_digest[]
            ) TO {RUNTIME};
        """,
    )
    yield


def _literal(value: object) -> str:
    if isinstance(value, UUID):
        return f"'{value}'::uuid"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = ",".join("'" + item.replace("'", "''") + "'" for item in value)
        return f"ARRAY[{items}]::sklegal_legal.sha256_digest[]"
    raise TypeError("unsupported synthetic PostgreSQL parameter")


class SnapshotTransaction:
    def __init__(self, container: str) -> None:
        self.container = container

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        del sql, parameters
        raise AssertionError("authorization snapshot must remain read-only")

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        rendered = sql
        for parameter in parameters:
            rendered = rendered.replace("%s", _literal(parameter), 1)
        if "%s" in rendered:
            raise ValueError("incomplete synthetic SQL binding")
        result = _psql(self.container, rendered, user=RUNTIME)
        return {"snapshot": json.loads(result.stdout.strip())}

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        del sql, parameters
        raise AssertionError("authorization snapshot must return one row")


def _repository(container: str) -> PostgresWorkProductRepository:
    transaction = SnapshotTransaction(container)

    def run(call: Callable[[SnapshotTransaction], T]) -> T:
        return call(transaction)

    return PostgresWorkProductRepository(
        transaction=run,
        current_policy_revision=POLICY,
    )


def _insert_revocation(container: str, digest: str) -> str:
    _psql(
        container,
        f"""
        INSERT INTO sklegal_identity.capability_revocations
            (tenant_id, credential_digest, revoked_by, rationale)
        VALUES ('{TENANT}', '{digest}', '{RUNTIME_PRINCIPAL}',
                'Public synthetic WP01F revocation');
        """,
    )
    snapshot = json.loads(
        _psql(
            container,
            f"""
            SELECT sklegal_identity.capability_revocation_snapshot(
                '{TENANT}', ARRAY['{digest}']::sklegal_legal.sha256_digest[]
            );
            """,
            user=RUNTIME,
        ).stdout.strip()
    )
    return str(snapshot["revision"])


def _assert_no_feature_evidence(container: str) -> None:
    result = _psql(
        container,
        """
        SELECT (SELECT count(*) FROM sklegal_audit.work_product_feature_events),
               (SELECT count(*) FROM sklegal_audit.work_product_feature_outbox);
        """,
    )
    assert result.stdout.strip() == "0|0"


def test_runtime_has_snapshot_execute_without_revocation_table_select(
    postgres: str,
) -> None:
    result = _psql(
        postgres,
        f"""
        SELECT has_table_privilege(
                   '{RUNTIME}', 'sklegal_identity.capability_revocations', 'SELECT'
               ),
               has_function_privilege(
                   '{RUNTIME}',
                   'sklegal_identity.capability_revocation_snapshot(uuid, sklegal_legal.sha256_digest[])',
                   'EXECUTE'
               );
        """,
    )
    assert result.stdout.strip() == "f|t"


def test_runtime_repository_accepts_exact_current_active_snapshot(
    postgres: str,
) -> None:
    evidence = actor().evidence
    assert evidence.revocation_revision == EMPTY_REVOCATION_REVISION
    assert _repository(postgres).authorization_is_active(TENANT, MATTER, evidence, T0)
    _assert_no_feature_evidence(postgres)


@pytest.mark.parametrize("target", ["leaf", "ancestor"])
def test_runtime_repository_denies_leaf_and_ancestor_revocation(
    postgres: str, target: str
) -> None:
    ancestor = "c" * 64
    evidence = actor(
        ancestor_credential_digests=(ancestor,) if target == "ancestor" else ()
    ).evidence
    digest = ancestor if target == "ancestor" else DIGEST
    revision = _insert_revocation(postgres, digest)
    current = evidence.model_copy(update={"revocation_revision": revision})
    assert not _repository(postgres).authorization_is_active(
        TENANT, MATTER, current, T0
    )
    _assert_no_feature_evidence(postgres)


def test_runtime_repository_denies_stale_snapshot(postgres: str) -> None:
    evidence = actor().evidence.model_copy(update={"revocation_revision": "d" * 64})
    assert not _repository(postgres).authorization_is_active(
        TENANT, MATTER, evidence, T0
    )
    _assert_no_feature_evidence(postgres)


def test_runtime_repository_sanitizes_cross_tenant_snapshot_denial(
    postgres: str,
) -> None:
    evidence = actor(tenant_id=UUID(OTHER_TENANT)).evidence
    with pytest.raises(WorkProductRepositoryUnavailable) as caught:
        _repository(postgres).authorization_is_active(
            UUID(OTHER_TENANT), MATTER, evidence, T0
        )
    assert "scope is unauthorized" not in str(caught.value)
    _assert_no_feature_evidence(postgres)


def test_runtime_repository_sanitizes_snapshot_outage(postgres: str) -> None:
    _psql(
        postgres,
        f"""
        REVOKE EXECUTE ON FUNCTION
            sklegal_identity.capability_revocation_snapshot(
                uuid, sklegal_legal.sha256_digest[]
            ) FROM {RUNTIME};
        """,
    )
    with pytest.raises(WorkProductRepositoryUnavailable) as caught:
        _repository(postgres).authorization_is_active(
            TENANT, MATTER, actor().evidence, T0
        )
    assert "permission denied" not in str(caught.value)
    _assert_no_feature_evidence(postgres)
