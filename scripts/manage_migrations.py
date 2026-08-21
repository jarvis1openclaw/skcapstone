#!/usr/bin/env python3
"""Apply reversible SKLegal SQL migrations through the PostgreSQL psql client."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from check_migrations import DEFAULT_ROOT, MigrationError, split_migration, validate

HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class MigrationRuntimeError(RuntimeError):
    """Raised when live migration state is unsafe or inconsistent."""


@dataclass(frozen=True)
class Migration:
    file: str
    sha256: str
    up: str
    down: str


class PsqlClient:
    """Small psql transport supporting direct and disposable Docker databases."""

    def __init__(
        self,
        *,
        direct: bool,
        docker_container: str | None,
        database: str,
        user: str,
    ) -> None:
        if direct == bool(docker_container):
            raise MigrationRuntimeError(
                "choose exactly one of --direct or --docker-container"
            )
        self.environment = os.environ.copy()
        common = [
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--field-separator=|",
        ]
        if docker_container is not None:
            self.command = [
                "docker",
                "exec",
                "-i",
                docker_container,
                *common,
                "--username",
                user,
                "--dbname",
                database,
            ]
        else:
            self.command = [
                *common,
                "--username",
                user,
                "--dbname",
                database,
            ]

    def run(self, sql: str) -> str:
        process = subprocess.run(
            self.command,
            input=sql,
            text=True,
            capture_output=True,
            env=self.environment,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or "psql failed without diagnostic output"
            raise MigrationRuntimeError(detail)
        return process.stdout.strip()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_migrations(root: Path) -> list[Migration]:
    names = validate(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entries = {entry["file"]: entry for entry in manifest["migrations"]}
    migrations: list[Migration] = []
    for name in names:
        digest = entries[name]["sha256"]
        if HEX_DIGEST.fullmatch(digest) is None:
            raise MigrationError(f"invalid migration digest: {name}")
        up, down = split_migration((root / name).read_text(encoding="utf-8"), name=name)
        migrations.append(Migration(name, digest, up, down))
    return migrations


def bootstrap(client: PsqlClient) -> None:
    client.run(
        """
        CREATE SCHEMA IF NOT EXISTS sklegal_migrations;
        CREATE TABLE IF NOT EXISTS sklegal_migrations.schema_migrations (
            file text PRIMARY KEY,
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
        );
        """
    )


def assert_safe_migration_role(client: PsqlClient) -> None:
    """Reject unsafe or ambiguously named owners before creating any object."""

    output = client.run(
        """
        SELECT role_record.rolname,
               role_record.rolcanlogin,
               role_record.rolinherit,
               role_record.rolsuper,
               role_record.rolbypassrls,
               role_record.rolcreaterole,
               role_record.rolcreatedb,
               role_record.rolreplication,
               EXISTS (
                   SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                   WHERE membership.member = role_record.oid
                      OR membership.roleid = role_record.oid
               )
        FROM pg_catalog.pg_roles AS role_record
        WHERE role_record.rolname = current_user;
        """
    )
    fields = output.split("|")
    if len(fields) != 9:
        raise MigrationRuntimeError("cannot determine migration role safety")
    (
        name,
        can_login,
        inherits,
        superuser,
        bypass_rls,
        create_role,
        create_db,
        replication,
        has_membership,
    ) = fields
    if (
        name != "sklegal_migrator"
        or can_login != "t"
        or inherits != "f"
        or superuser != "f"
        or bypass_rls != "f"
        or create_role != "f"
        or create_db != "f"
        or replication != "f"
        or has_membership != "f"
    ):
        raise MigrationRuntimeError(
            "migrations require exact sklegal_migrator LOGIN NOINHERIT "
            "NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB "
            "NOREPLICATION with no role membership edge"
        )


def assert_application_ownership(client: PsqlClient) -> None:
    """Require every SKLegal schema, relation, function, and type to use one owner."""

    output = client.run(
        """
        WITH unsafe AS (
            SELECT 'schema:' || namespace.nspname AS object_name
            FROM pg_namespace AS namespace
            JOIN pg_roles AS owner ON owner.oid = namespace.nspowner
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND owner.rolname <> 'sklegal_migrator'
            UNION ALL
            SELECT 'relation:' || namespace.nspname || '.' || relation.relname
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_roles AS owner ON owner.oid = relation.relowner
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND owner.rolname <> 'sklegal_migrator'
            UNION ALL
            SELECT 'function:' || namespace.nspname || '.' || procedure.proname
            FROM pg_proc AS procedure
            JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
            JOIN pg_roles AS owner ON owner.oid = procedure.proowner
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND owner.rolname <> 'sklegal_migrator'
            UNION ALL
            SELECT 'type:' || namespace.nspname || '.' || type_record.typname
            FROM pg_type AS type_record
            JOIN pg_namespace AS namespace ON namespace.oid = type_record.typnamespace
            JOIN pg_roles AS owner ON owner.oid = type_record.typowner
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND owner.rolname <> 'sklegal_migrator'
        )
        SELECT COALESCE(string_agg(object_name, ',' ORDER BY object_name), '')
        FROM unsafe;
        """
    )
    if output:
        raise MigrationRuntimeError(f"unsafe application object owner: {output}")


def applied_migrations(client: PsqlClient) -> list[tuple[str, str]]:
    output = client.run(
        """
        SELECT file, sha256
        FROM sklegal_migrations.schema_migrations
        ORDER BY file;
        """
    )
    if not output:
        return []
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("|", maxsplit=1)
        if len(fields) != 2:
            raise MigrationRuntimeError("cannot parse applied migration state")
        rows.append((fields[0], fields[1]))
    return rows


def validate_applied(
    migrations: list[Migration], applied: list[tuple[str, str]]
) -> None:
    declared = [(migration.file, migration.sha256) for migration in migrations]
    if applied != declared[: len(applied)]:
        raise MigrationRuntimeError(
            "applied migrations are not an exact digest-matched manifest prefix"
        )


def apply_up(client: PsqlClient, migrations: list[Migration]) -> int:
    applied = applied_migrations(client)
    validate_applied(migrations, applied)
    pending = migrations[len(applied) :]
    if not pending:
        return 0
    statements = ["BEGIN;", "SELECT pg_advisory_xact_lock(7994702210402);"]
    for migration in pending:
        file_literal = _sql_literal(migration.file)
        digest_literal = _sql_literal(migration.sha256)
        statements.extend(
            [
                "DO $guard$ BEGIN",
                "  IF EXISTS (",
                "    SELECT 1 FROM sklegal_migrations.schema_migrations",
                f"    WHERE file = {file_literal}",
                "  ) THEN",
                f"    RAISE EXCEPTION 'migration already applied: {migration.file}';",
                "  END IF;",
                "END $guard$;",
                migration.up,
                "INSERT INTO sklegal_migrations.schema_migrations (file, sha256)",
                f"VALUES ({file_literal}, {digest_literal});",
            ]
        )
    statements.append("COMMIT;")
    client.run("\n".join(statements))
    return len(pending)


def apply_down(
    client: PsqlClient, migrations: list[Migration], *, steps: int | None
) -> int:
    applied = applied_migrations(client)
    validate_applied(migrations, applied)
    count = len(applied) if steps is None else min(steps, len(applied))
    if count == 0:
        return 0
    selected = list(reversed(migrations[len(applied) - count : len(applied)]))
    statements = ["BEGIN;", "SELECT pg_advisory_xact_lock(7994702210402);"]
    for migration in selected:
        file_literal = _sql_literal(migration.file)
        digest_literal = _sql_literal(migration.sha256)
        statements.extend(
            [
                "DO $guard$ BEGIN",
                "  IF NOT EXISTS (",
                "    SELECT 1 FROM sklegal_migrations.schema_migrations",
                f"    WHERE file = {file_literal} AND sha256 = {digest_literal}",
                "  ) THEN",
                f"    RAISE EXCEPTION 'migration is not applied: {migration.file}';",
                "  END IF;",
                "END $guard$;",
                migration.down,
                "DELETE FROM sklegal_migrations.schema_migrations",
                f"WHERE file = {file_literal} AND sha256 = {digest_literal};",
            ]
        )
    statements.append("COMMIT;")
    client.run("\n".join(statements))
    return count


def parse_steps(raw: str) -> int | None:
    if raw == "all":
        return None
    try:
        steps = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "steps must be a positive integer or all"
        ) from exc
    if steps < 1:
        raise argparse.ArgumentTypeError("steps must be a positive integer or all")
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "status"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    connection = parser.add_mutually_exclusive_group(required=True)
    connection.add_argument("--direct", action="store_true")
    connection.add_argument("--docker-container")
    parser.add_argument("--database", default="sklegal")
    parser.add_argument("--user", default="sklegal_migrator")
    parser.add_argument("--steps", type=parse_steps, default=1)
    args = parser.parse_args()

    migrations = load_migrations(args.root)
    client = PsqlClient(
        direct=args.direct,
        docker_container=args.docker_container,
        database=args.database,
        user=args.user,
    )
    assert_safe_migration_role(client)
    bootstrap(client)
    if args.action == "up":
        changed = apply_up(client, migrations)
        assert_safe_migration_role(client)
        assert_application_ownership(client)
        print(f"applied {changed} migration(s)")
    elif args.action == "down":
        changed = apply_down(client, migrations, steps=args.steps)
        assert_safe_migration_role(client)
        assert_application_ownership(client)
        print(f"reverted {changed} migration(s)")
    else:
        applied = applied_migrations(client)
        validate_applied(migrations, applied)
        assert_safe_migration_role(client)
        assert_application_ownership(client)
        print(f"migration status: {len(applied)}/{len(migrations)} applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
