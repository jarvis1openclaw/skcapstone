"""Schema isolation and audit rollback guard persistence contract tests."""

from __future__ import annotations

import json
import subprocess
import unittest

from tests.integration.persistence_contract_support import (
    AUDIT_MIGRATION_STEPS,
    PersistenceContractBase,
)


class PersistenceContract11MigrationGuardTests(PersistenceContractBase):
    def test_13_schema_isolation_and_disposable_runtime(self) -> None:
        schemas = self._psql(
            "postgres",
            """
            SELECT string_agg(schema_name, ',' ORDER BY schema_name)
            FROM information_schema.schemata WHERE schema_name LIKE 'sklegal_%';
            """,
        )
        self.assertEqual(
            "sklegal_audit,sklegal_identity,sklegal_integrations,sklegal_legal,"
            "sklegal_migrations,sklegal_workflow",
            schemas.stdout.strip(),
        )
        inspection = subprocess.run(
            ["docker", "inspect", self.container],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(inspection.stdout)[0]
        self.assertIn(payload["HostConfig"].get("PortBindings"), (None, {}))
        self.assertEqual("none", payload["HostConfig"]["NetworkMode"])
        self.assertEqual(
            "rw,noexec,nosuid,size=512m",
            payload["HostConfig"]["Tmpfs"]["/var/lib/postgresql/data"],
        )
        self.assertFalse(
            any(mount["Type"] == "volume" for mount in payload.get("Mounts", []))
        )
        if self.skmemory_container_id:
            skmemory = subprocess.run(
                ["docker", "ps", "--quiet", "--filter", "name=^skmem-pg$"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(self.skmemory_container_id, skmemory.stdout.strip())

    def test_14_data_bearing_audit_migration_refuses_down(self) -> None:
        event = self._append_audit_event(
            event_id="a6700000-0000-4000-8000-000000000011",
            run_id="a6700000-0000-4000-8000-000000000001",
            correlation_id="a6700000-0000-4000-8000-000000000021",
            span_id="0000000000000061",
            boundary="api",
            action="audit.synthetic.rollback-guard",
        )
        self.assertTrue(event.stdout.strip())

        def state() -> str:
            return self._psql(
                "postgres",
                """
                SELECT (SELECT count(*) FROM sklegal_audit.events) || ':' ||
                       (SELECT count(*) FROM sklegal_audit.outbox) || ':' ||
                       (SELECT count(*) FROM sklegal_audit.rollback_guard);
                """,
            ).stdout.strip()

        def assert_down_denied(before: str) -> None:
            down = self._migrate(
                "down", "--steps", str(AUDIT_MIGRATION_STEPS), check=False
            )
            self.assertNotEqual(0, down.returncode)
            self.assertIn("audit chain cannot be rolled back", down.stderr)
            self.assertEqual(before, state())
            self.assertEqual(
                "1:true:true",
                self._psql(
                    "postgres",
                    """
                    SELECT count(*) || ':' ||
                           (to_regclass('sklegal_audit.outbox') IS NOT NULL) || ':' ||
                           (to_regprocedure(
                               'sklegal_audit.append_event(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,text,text,text,uuid,uuid,uuid,text,text,timestamptz,jsonb)'
                           ) IS NOT NULL)
                    FROM sklegal_migrations.schema_migrations
                    WHERE file = '0007_append_only_audit_outbox.sql';
                    """,
                ).stdout.strip(),
            )

        both = state()
        both_parts = [int(part) for part in both.split(":")]
        self.assertGreater(both_parts[0], 0)
        self.assertGreater(both_parts[1], 0)
        self.assertEqual(1, both_parts[2])
        assert_down_denied(both)

        self._psql(
            "postgres",
            """
            SET session_replication_role = replica;
            DELETE FROM sklegal_audit.outbox_deliveries;
            DELETE FROM sklegal_audit.outbox;
            SET session_replication_role = origin;
            """,
        )
        event_only = state()
        event_only_parts = [int(part) for part in event_only.split(":")]
        self.assertGreater(event_only_parts[0], 0)
        self.assertEqual([0, 1], event_only_parts[1:])
        assert_down_denied(event_only)

        self._psql(
            "postgres",
            """
            SET session_replication_role = replica;
            INSERT INTO sklegal_audit.outbox (
                id, tenant_id, matter_id, event_id, run_id, correlation_id,
                event_sequence, event_sha256, available_at
            )
            SELECT id, tenant_id, matter_id, id, run_id, correlation_id,
                   event_sequence, event_sha256, recorded_at
            FROM sklegal_audit.events
            ORDER BY tenant_id, event_sequence
            LIMIT 1;
            DELETE FROM sklegal_audit.events;
            SET session_replication_role = origin;
            """,
        )
        outbox_only = state()
        self.assertEqual("0:1:1", outbox_only)
        assert_down_denied(outbox_only)


if __name__ == "__main__":
    unittest.main()
