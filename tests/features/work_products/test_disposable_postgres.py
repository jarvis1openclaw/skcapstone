from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

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
MIGRATION = Path("migrations/0050_work_product_feature_lane.sql")
TENANT = "10000000-0000-4000-8000-000000000001"
OTHER_TENANT = "10000000-0000-4000-8000-000000000002"
MATTER = "20000000-0000-4000-8000-000000000001"
PRINCIPAL = "30000000-0000-4000-8000-000000000001"
PRODUCT = "50000000-0000-4000-8000-000000000001"
VERSION = "50000000-0000-4000-8000-000000000002"
EVENT = "50000000-0000-4000-8000-000000000003"


def psql(
    container: str, sql: str, *, check: bool = True
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
            "postgres",
            "--dbname",
            "postgres",
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


def migration_parts() -> tuple[str, str]:
    text = MIGRATION.read_text(encoding="utf-8")
    up, down = text.split("-- sklegal:down", 1)
    return up.removeprefix("-- sklegal:up"), down


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
    container = f"sklegal-wp01-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--label",
            "com.sklegal.test-card=0d1d81ce",
            "--network",
            "none",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,noexec,nosuid,size=128m",
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
        for _ in range(300):
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "--username", "postgres"],
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
        psql(
            container,
            f"""
            CREATE SCHEMA sklegal_identity;
            CREATE SCHEMA sklegal_legal;
            CREATE SCHEMA sklegal_audit;
            CREATE DOMAIN sklegal_legal.record_version AS bigint CHECK (VALUE >= 1);
            CREATE DOMAIN sklegal_legal.sha256_digest AS text
                CHECK (VALUE ~ '^[0-9a-f]{{64}}$');
            CREATE TABLE sklegal_identity.tenants (id uuid PRIMARY KEY);
            CREATE TABLE sklegal_identity.principals (
                tenant_id uuid NOT NULL,
                id uuid NOT NULL,
                PRIMARY KEY (tenant_id, id)
            );
            CREATE TABLE sklegal_legal.matters (
                tenant_id uuid NOT NULL,
                matter_id uuid NOT NULL,
                PRIMARY KEY (tenant_id, matter_id)
            );
            CREATE FUNCTION sklegal_legal.reject_record_change()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'record change denied' USING ERRCODE = '55000';
            END;
            $function$;
            CREATE FUNCTION sklegal_identity.record_is_authorized(uuid, uuid)
            RETURNS boolean LANGUAGE sql STABLE AS $function$
            SELECT current_setting('sklegal.tenant_id', true) = $1::text;
            $function$;
            CREATE ROLE wp_runtime LOGIN NOSUPERUSER NOBYPASSRLS;
            GRANT USAGE ON SCHEMA sklegal_legal, sklegal_audit, sklegal_identity
                TO wp_runtime;
            INSERT INTO sklegal_identity.tenants VALUES ('{TENANT}'), ('{OTHER_TENANT}');
            INSERT INTO sklegal_identity.principals VALUES ('{TENANT}', '{PRINCIPAL}');
            INSERT INTO sklegal_legal.matters VALUES ('{TENANT}', '{MATTER}');
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


def test_apply_forced_rls_and_exact_tables(postgres: str) -> None:
    up, _ = migration_parts()
    psql(postgres, up)
    result = psql(
        postgres,
        """
        SELECT count(*), bool_and(class.relrowsecurity), bool_and(class.relforcerowsecurity)
        FROM pg_class AS class
        JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
        WHERE (namespace.nspname, class.relname) IN (
          ('sklegal_legal', 'work_product_feature_identities'),
          ('sklegal_legal', 'work_product_feature_versions'),
          ('sklegal_legal', 'work_product_feature_idempotency'),
          ('sklegal_audit', 'work_product_feature_events'),
          ('sklegal_audit', 'work_product_feature_outbox')
        );
        """,
    )
    assert result.stdout.strip() == "5|t|t"
    psql(
        postgres,
        """
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON sklegal_legal.work_product_feature_identities,
           sklegal_legal.work_product_feature_versions,
           sklegal_legal.work_product_feature_idempotency,
           sklegal_audit.work_product_feature_events,
           sklegal_audit.work_product_feature_outbox
        TO wp_runtime;
        """,
    )


def test_atomic_fixture_and_append_only_controls(postgres: str) -> None:
    payload = (
        '{"schemaVersion":"sklegal.work-product-aggregate/v1",'
        f'"tenantId":"{TENANT}","matterId":"{MATTER}",'
        f'"workProductId":"{PRODUCT}","aggregateVersion":1,'
        f'"currentVersionId":"{VERSION}","status":"draft"}}'
    )
    psql(
        postgres,
        f"""
        BEGIN;
        INSERT INTO sklegal_legal.work_product_feature_identities
        VALUES ('{TENANT}', '{MATTER}', '{PRODUCT}', 1, clock_timestamp(), clock_timestamp());
        INSERT INTO sklegal_legal.work_product_feature_versions VALUES (
            '{TENANT}', '{MATTER}', '{PRODUCT}', 1, '{VERSION}', 1,
            '{"a" * 64}', 'draft', '{"b" * 64}', '{payload}'::jsonb, clock_timestamp()
        );
        INSERT INTO sklegal_legal.work_product_feature_idempotency VALUES (
            '{TENANT}', '{MATTER}', '50000000-0000-4000-8000-000000000004',
            '{"c" * 64}', '{PRODUCT}', 1, '{payload}'::jsonb, clock_timestamp()
        );
        INSERT INTO sklegal_audit.work_product_feature_events VALUES (
            '{TENANT}', '{MATTER}', '{EVENT}', '{PRODUCT}', 1,
            '50000000-0000-4000-8000-000000000005', '{PRINCIPAL}',
            'work_product.created', 'completed', '{"b" * 64}', clock_timestamp()
        );
        INSERT INTO sklegal_audit.work_product_feature_outbox VALUES (
            '{TENANT}', '{MATTER}', '{EVENT}', '{EVENT}', '{PRODUCT}', 1,
            'sklegal.work_product.changed', '{"b" * 64}', clock_timestamp()
        );
        COMMIT;
        """,
    )
    denied = psql(
        postgres,
        f"""
        UPDATE sklegal_legal.work_product_feature_versions
        SET status = 'withdrawn'
        WHERE tenant_id = '{TENANT}' AND matter_id = '{MATTER}'
          AND work_product_id = '{PRODUCT}' AND aggregate_version = 1;
        """,
        check=False,
    )
    assert denied.returncode != 0
    assert "record change denied" in denied.stderr
    missing = psql(
        postgres,
        f"""
        UPDATE sklegal_legal.work_product_feature_identities
        SET current_aggregate_version = 2, updated_at = clock_timestamp()
        WHERE tenant_id = '{TENANT}' AND matter_id = '{MATTER}'
          AND work_product_id = '{PRODUCT}';
        """,
        check=False,
    )
    assert missing.returncode != 0
    assert "current Work Product aggregate version is missing" in missing.stderr


def test_runtime_rls_hides_other_tenant(postgres: str) -> None:
    result = psql(
        postgres,
        f"""
        SET ROLE wp_runtime;
        SELECT set_config('sklegal.tenant_id', '{OTHER_TENANT}', false);
        SELECT count(*) FROM sklegal_legal.work_product_feature_identities;
        RESET ROLE;
        """,
    )
    assert "0" in result.stdout.strip().splitlines()


def test_down_and_reapply_are_clean(postgres: str) -> None:
    up, down = migration_parts()
    psql(postgres, down)
    absent = psql(
        postgres,
        "SELECT to_regclass('sklegal_legal.work_product_feature_identities') IS NULL;",
    )
    assert absent.stdout.strip() == "t"
    psql(postgres, up)
    present = psql(
        postgres,
        "SELECT to_regclass('sklegal_legal.work_product_feature_identities') IS NOT NULL;",
    )
    assert present.stdout.strip() == "t"
