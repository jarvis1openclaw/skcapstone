#!/usr/bin/env python3
"""Create and verify the shared sklegal_runtime grantee role (S1-11).

Migrations 0008-0012 GRANT EXECUTE on the CapAuth SECURITY DEFINER functions
to sklegal_runtime, but migrations run as sklegal_migrator (NOCREATEROLE), so
the role itself must be provisioned by an administrator before migration.
This script is the in-repo provisioning path: idempotent create plus a
fail-closed readback of the exact least-privilege profile.

sklegal_runtime is the dedicated login class for the durable CapAuth
function surface. It is distinct from the per-principal RLS-bound runtime
logins installed by provision_postgres_principal.py; the reconciliation of
the two login classes is a Sprint 3 architecture decision (board card
S3-08). Until then this role holds no table grants beyond what the
migrations themselves assign.
"""

from __future__ import annotations

import argparse

from manage_migrations import PsqlClient, _sql_literal

RUNTIME_ROLE = "sklegal_runtime"


def provision(client: PsqlClient, *, runtime_role: str = RUNTIME_ROLE) -> None:
    role_literal = _sql_literal(runtime_role)
    client.run(
        f"""
        DO $provision$
        DECLARE
            selected_role record;
        BEGIN
            SELECT * INTO selected_role FROM pg_roles WHERE rolname = {role_literal};
            IF NOT FOUND THEN
                EXECUTE format(
                    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                    'NOINHERIT NOBYPASSRLS NOREPLICATION',
                    {role_literal}
                );
                SELECT * INTO selected_role FROM pg_roles WHERE rolname = {role_literal};
            END IF;
            IF NOT selected_role.rolcanlogin OR selected_role.rolsuper
               OR selected_role.rolbypassrls OR selected_role.rolcreaterole
               OR selected_role.rolcreatedb OR selected_role.rolreplication
               OR selected_role.rolinherit THEN
                RAISE EXCEPTION 'sklegal_runtime drifted outside the least-privilege profile';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_auth_members AS membership
                WHERE membership.member = selected_role.oid
                   OR membership.roleid = selected_role.oid
            ) THEN
                RAISE EXCEPTION 'sklegal_runtime must not participate in a PostgreSQL role graph';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_namespace WHERE nspname LIKE 'sklegal_%'
                  AND nspowner = selected_role.oid
                UNION ALL
                SELECT 1 FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname LIKE 'sklegal_%'
                  AND relation.relowner = selected_role.oid
            ) THEN
                RAISE EXCEPTION 'sklegal_runtime cannot own an SKLegal database object';
            END IF;
        END;
        $provision$;
        """
    )
    readback = client.run(
        f"""
        SELECT role_record.rolcanlogin AND NOT role_record.rolsuper
               AND NOT role_record.rolbypassrls
               AND NOT role_record.rolcreaterole
               AND NOT role_record.rolcreatedb
               AND NOT role_record.rolreplication
               AND NOT role_record.rolinherit
        FROM pg_roles AS role_record WHERE role_record.rolname = {role_literal};
        """
    )
    if readback != "t":
        raise RuntimeError(
            "sklegal_runtime readback differs from the safe role profile"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    connection = parser.add_mutually_exclusive_group(required=True)
    connection.add_argument("--direct", action="store_true")
    connection.add_argument("--docker-container")
    parser.add_argument("--database", default="sklegal")
    parser.add_argument("--admin-user", default="postgres")
    args = parser.parse_args()
    client = PsqlClient(
        direct=args.direct,
        docker_container=args.docker_container,
        database=args.database,
        user=args.admin_user,
    )
    provision(client)
    print(f"provisioned shared runtime role: {RUNTIME_ROLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
