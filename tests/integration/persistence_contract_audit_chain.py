"""Audit chain persistence contract tests."""

from __future__ import annotations

import json
import unittest
from datetime import (
    UTC,
    datetime,
)

from sklegal_audit import (
    AuditEventDraft,
    DurableAuditEvent,
    PostgresAuditRepository,
    recompute_event_sha256,
    verify_event_chain,
)
from sklegal_audit.ledger import _canonical_timestamp

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract09AuditChainTests(PersistenceContractBase):
    def test_12_audit_and_outbox_share_rollback_and_delivery_receipt(self) -> None:
        run_id = "a6200000-0000-4000-8000-000000000001"
        event_id = "a6200000-0000-4000-8000-000000000011"
        event = json.loads(
            self._append_audit_event(
                event_id=event_id,
                run_id=run_id,
                correlation_id="a6200000-0000-4000-8000-000000000021",
                span_id="0000000000000011",
                boundary="connector",
                action="audit.synthetic.connector",
            ).stdout
        )
        before = self._psql(
            "postgres",
            f"""
            SELECT (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox),
                   last_event_sequence, last_event_sha256
            FROM sklegal_audit.chain_heads
            WHERE tenant_id = '{self.fixture["tenant_alpha"]}';
            """,
        ).stdout.strip()
        rollback = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            SELECT sklegal_audit.append_event(
                'a6200000-0000-4000-8000-000000000012',
                '{self.fixture["tenant_alpha"]}',
                '{self.fixture["matter_alpha_one"]}',
                '{self.fixture["principal_alpha_one"]}',
                '{run_id}', 'a6200000-0000-4000-8000-000000000021',
                '{"1" * 32}', '0000000000000012', '01', 'tool',
                'audit.synthetic.rollback', 'matter',
                '{self.fixture["matter_alpha_one"]}',
                'a6000000-0000-4000-8000-000000000001',
                'a6000000-0000-4000-8000-000000000002',
                'allow', 'allow', '2026-08-20T12:00:00Z', '{{}}'::jsonb
            );
            SELECT 1 / 0;
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, rollback.returncode)
        after = self._psql(
            "postgres",
            f"""
            SELECT (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox),
                   last_event_sequence, last_event_sha256
            FROM sklegal_audit.chain_heads
            WHERE tenant_id = '{self.fixture["tenant_alpha"]}';
            """,
        ).stdout.strip()
        self.assertEqual(before, after)

        delivery_id = "a6200000-0000-4000-8000-000000000031"
        first = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT sklegal_audit.record_outbox_delivery("
            f"'{event['outbox_id']}', 'audit.local', '{delivery_id}');",
        )
        duplicate = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT sklegal_audit.record_outbox_delivery("
            f"'{event['outbox_id']}', 'audit.local', "
            "'a6200000-0000-4000-8000-000000000032');",
        )
        self.assertEqual("t", first.stdout.strip())
        self.assertEqual("f", duplicate.stdout.strip())
        receipt = self._psql(
            "postgres",
            f"""
            SELECT count(*) || ':' || min(delivery_id::text) || ':' ||
                   bool_and(event_sha256 = '{event["event_sha256"]}')
            FROM sklegal_audit.outbox_deliveries
            WHERE outbox_id = '{event["outbox_id"]}';
            """,
        )
        self.assertEqual(f"1:{delivery_id}:true", receipt.stdout.strip())
        tamper = self._psql(
            "postgres",
            f"UPDATE sklegal_audit.outbox SET delivered_at = NULL "
            f"WHERE id = '{event['outbox_id']}';",
            check=False,
        )
        self.assertNotEqual(0, tamper.returncode)
        self.assertIn("controlled outbox writer", tamper.stderr)

    def test_12_audit_chain_is_scoped_append_only_and_suppresses_content(self) -> None:
        value = self.fixture
        insert_sql = f"""
            INSERT INTO sklegal_audit.events
                (id, tenant_id, matter_id, principal_id, action, resource_kind,
                 correlation_id, event_sha256, occurred_at)
            VALUES ('a0000000-0000-4000-8000-000000000001',
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    '{value["principal_alpha_one"]}', 'spoof', 'matter',
                    'a0000000-0000-4000-8000-000000000002',
                    '{"d" * 64}', clock_timestamp());
        """
        runtime = self._psql("sklegal_test_alpha_one", insert_sql, check=False)
        administrator = self._psql("postgres", insert_sql, check=False)
        owner = self._psql("sklegal_migrator", insert_sql, check=False)
        self.assertNotEqual(0, runtime.returncode)
        self.assertNotEqual(0, administrator.returncode)
        self.assertIn("controlled audit chain writer", administrator.stderr)
        self.assertNotEqual(0, owner.returncode)

        run_id = "a6100000-0000-4000-8000-000000000001"
        first = json.loads(
            self._append_audit_event(
                event_id="a6100000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6100000-0000-4000-8000-000000000021",
                span_id="0000000000000001",
                boundary="api",
                action="audit.synthetic.api",
                attributes='{"event_schema":"sklegal-audit-event/v1"}',
            ).stdout
        )
        second = json.loads(
            self._append_audit_event(
                event_id="a6100000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6100000-0000-4000-8000-000000000021",
                span_id="0000000000000002",
                boundary="workflow",
                action="audit.synthetic.workflow",
                attributes=(
                    '{"operation":"read","resource_version":1,'
                    '"status_code":200,"retry_count":0}'
                ),
            ).stdout
        )
        self.assertEqual(first["event_sequence"] + 1, second["event_sequence"])
        self.assertEqual(first["event_sha256"], second["previous_event_sha256"])
        self.assertEqual(first["event_id"], first["outbox_id"])
        self.assertEqual(
            "api,workflow",
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                SELECT string_agg(boundary, ',' ORDER BY event_sequence)
                FROM sklegal_audit.events WHERE run_id = '{run_id}';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_audit.verify_current_tenant_chain();",
            ).stdout.strip(),
        )
        for other_role in ("sklegal_test_alpha_two", "sklegal_test_beta_one"):
            with self.subTest(other_role=other_role):
                self.assertEqual(
                    "0",
                    self._psql(
                        other_role,
                        f"SELECT count(*) FROM sklegal_audit.events "
                        f"WHERE run_id = '{run_id}';",
                    ).stdout.strip(),
                )

        protected = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000013",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000003",
            boundary="model",
            action="audit.synthetic.model",
            attributes='{"prompt":"synthetic protected content"}',
            check=False,
        )
        self.assertNotEqual(0, protected.returncode)
        self.assertIn("audit attributes are not allowlisted", protected.stderr)
        self.assertNotIn("synthetic protected content", protected.stderr)

        invalid_attributes = (
            '{"capability":123}',
            '{"operation":"READ"}',
            '{"resource_version":"1"}',
            '{"resource_version":0}',
            '{"resource_version":1.5}',
            '{"status_code":true}',
            '{"status_code":99}',
            '{"status_code":600}',
            '{"retry_count":-1}',
            '{"retry_count":1001}',
            '{"retry_count":1.5}',
        )
        for attributes in invalid_attributes:
            with self.subTest(attributes=attributes):
                malformed = self._append_audit_event(
                    event_id="a6100000-0000-4000-8000-000000000015",
                    run_id=run_id,
                    correlation_id="a6100000-0000-4000-8000-000000000021",
                    span_id="0000000000000005",
                    boundary="tool",
                    action="audit.synthetic.invalid",
                    attributes=attributes,
                    check=False,
                )
                self.assertNotEqual(0, malformed.returncode)
                self.assertIn("audit attributes are not allowlisted", malformed.stderr)

        exact_resource_kind = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000016",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000006",
            boundary="tool",
            action="audit.synthetic.resource-kind-limit",
            resource_kind="r" * 100,
        )
        self.assertEqual(
            100, len(json.loads(exact_resource_kind.stdout)["resource_kind"])
        )
        excessive_resource_kind = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000017",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000007",
            boundary="tool",
            action="audit.synthetic.resource-kind-excess",
            resource_kind="r" * 101,
            check=False,
        )
        self.assertNotEqual(0, excessive_resource_kind.returncode)
        self.assertIn("audit event metadata is invalid", excessive_resource_kind.stderr)

        wrong_scope = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000014",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000004",
            boundary="human",
            action="audit.synthetic.human",
            principal_id=value["principal_alpha_two"],
            check=False,
        )
        self.assertNotEqual(0, wrong_scope.returncode)
        self.assertIn("audit scope is unauthorized", wrong_scope.stderr)

        for mutation in (
            f"UPDATE sklegal_audit.events SET action = 'tamper' "
            f"WHERE id = '{first['event_id']}';",
            f"DELETE FROM sklegal_audit.events WHERE id = '{first['event_id']}';",
        ):
            with self.subTest(mutation=mutation.split()[0]):
                result = self._psql("postgres", mutation, check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("append-only", result.stderr)

        policies = self._psql(
            "postgres",
            """
            SELECT count(*) FROM pg_policies
            WHERE schemaname = 'sklegal_audit' AND tablename = 'events'
              AND cmd = 'INSERT';
            """,
        )
        self.assertEqual("1", policies.stdout.strip())

    def test_12_canonical_audit_payload_is_timezone_independent(self) -> None:
        value = self.fixture
        timezone_probe = self._psql(
            "postgres",
            f"""
            BEGIN;
            SET LOCAL TIME ZONE 'UTC';
            CREATE TEMP TABLE audit_timezone_probe ON COMMIT DROP AS
            SELECT payload,
                   sklegal_audit.payload_sha256(payload) AS digest
            FROM (
                SELECT sklegal_audit.canonical_event_payload(
                    'a6600000-0000-4000-8000-000000000011',
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    '{value["principal_alpha_one"]}',
                    'a6600000-0000-4000-8000-000000000001',
                    'a6600000-0000-4000-8000-000000000021',
                    '{"1" * 32}', '0000000000000051', '01', 'api',
                    'audit.synthetic.timezone', 'matter',
                    '{value["matter_alpha_one"]}', NULL, NULL,
                    'success', 'allow', '2026-08-20T12:34:56.123456Z',
                    '{{"operation":"read"}}'::jsonb, 1, NULL,
                    '2026-08-20T12:34:57.654321Z'
                ) AS payload
            ) AS canonical;
            SET LOCAL TIME ZONE 'Pacific/Chatham';
            SELECT payload = sklegal_audit.canonical_event_payload(
                       'a6600000-0000-4000-8000-000000000011',
                       '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                       '{value["principal_alpha_one"]}',
                       'a6600000-0000-4000-8000-000000000001',
                       'a6600000-0000-4000-8000-000000000021',
                       '{"1" * 32}', '0000000000000051', '01', 'api',
                       'audit.synthetic.timezone', 'matter',
                       '{value["matter_alpha_one"]}', NULL, NULL,
                       'success', 'allow',
                       '2026-08-20T12:34:56.123456Z',
                       '{{"operation":"read"}}'::jsonb, 1, NULL,
                       '2026-08-20T12:34:57.654321Z'
                   )
                   AND digest = sklegal_audit.payload_sha256(
                       sklegal_audit.canonical_event_payload(
                           'a6600000-0000-4000-8000-000000000011',
                           '{value["tenant_alpha"]}',
                           '{value["matter_alpha_one"]}',
                           '{value["principal_alpha_one"]}',
                           'a6600000-0000-4000-8000-000000000001',
                           'a6600000-0000-4000-8000-000000000021',
                           '{"1" * 32}', '0000000000000051', '01', 'api',
                           'audit.synthetic.timezone', 'matter',
                           '{value["matter_alpha_one"]}', NULL, NULL,
                           'success', 'allow',
                           '2026-08-20T12:34:56.123456Z',
                           '{{"operation":"read"}}'::jsonb, 1, NULL,
                           '2026-08-20T12:34:57.654321Z'
                       )
                   )
            FROM audit_timezone_probe;
            ROLLBACK;
            """,
        )
        self.assertIn("t", timezone_probe.stdout.splitlines())
        appended = self._append_audit_event(
            event_id="a6600000-0000-4000-8000-000000000012",
            run_id="a6600000-0000-4000-8000-000000000002",
            correlation_id="a6600000-0000-4000-8000-000000000022",
            span_id="0000000000000052",
            boundary="model",
            action="audit.synthetic.timezone-append",
            time_zone="Pacific/Chatham",
        )
        payload = next(
            line for line in appended.stdout.splitlines() if line.startswith("{")
        )
        reconstructed = DurableAuditEvent.model_validate_json(payload)
        self.assertEqual(0, reconstructed.occurred_at.utcoffset().total_seconds())
        self.assertEqual(0, reconstructed.recorded_at.utcoffset().total_seconds())
        verification = self._psql(
            "sklegal_test_alpha_one",
            """
            SET TIME ZONE 'America/Los_Angeles';
            SELECT sklegal_audit.verify_current_tenant_chain();
            """,
        )
        self.assertEqual("t", verification.stdout.splitlines()[-1])

    def test_12_integrity_mode_cannot_be_laundered_through_definers(self) -> None:
        value = self.fixture
        event = json.loads(
            self._append_audit_event(
                event_id="a6900000-0000-4000-8000-000000000011",
                run_id="a6900000-0000-4000-8000-000000000001",
                correlation_id="a6900000-0000-4000-8000-000000000021",
                span_id="0000000000000081",
                boundary="workflow",
                action="audit.synthetic.lost-matter-access",
            ).stdout
        )
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.matter_memberships
            SET active = false,
                version = version + 1,
                updated_at = clock_timestamp()
            WHERE tenant_id = '{value["tenant_alpha"]}'
              AND matter_id = '{value["matter_alpha_one"]}'
              AND principal_id = '{value["principal_alpha_one"]}';
            """,
        )
        try:
            direct = self._psql(
                "sklegal_test_alpha_one",
                f"""
                SET sklegal.audit_integrity_verification = 'on';
                SELECT count(*) FROM sklegal_audit.events
                WHERE id = '{event["event_id"]}';
                """,
            )
            self.assertEqual("0", direct.stdout.splitlines()[-1])
            for projection, setup in (
                ("audit.lost-access.plain", ""),
                (
                    "audit.lost-access.caller-guc",
                    "SET sklegal.audit_integrity_verification = 'on';",
                ),
            ):
                with self.subTest(projection=projection):
                    denied = self._psql(
                        "sklegal_test_alpha_one",
                        f"""
                        {setup}
                        SELECT sklegal_audit.advance_projection_watermark(
                            '{projection}', 0, NULL,
                            {event["event_sequence"]}, '{event["event_sha256"]}'
                        );
                        """,
                        check=False,
                    )
                    self.assertNotEqual(0, denied.returncode)
                    self.assertIn("target audit event is unavailable", denied.stderr)
            restored = self._psql(
                "sklegal_test_alpha_one",
                """
                BEGIN;
                SET LOCAL sklegal.audit_integrity_verification = 'caller';
                SELECT sklegal_audit.verify_current_tenant_chain();
                SELECT current_setting(
                    'sklegal.audit_integrity_verification', true
                );
                ROLLBACK;
                """,
            )
            self.assertEqual(
                ["t", "caller"],
                [
                    line
                    for line in restored.stdout.splitlines()
                    if line in {"t", "caller"}
                ],
            )
        finally:
            self._psql(
                "postgres",
                f"""
                UPDATE sklegal_legal.matter_memberships
                SET active = true,
                    version = version + 1,
                    updated_at = clock_timestamp()
                WHERE tenant_id = '{value["tenant_alpha"]}'
                  AND matter_id = '{value["matter_alpha_one"]}'
                  AND principal_id = '{value["principal_alpha_one"]}';
                """,
            )

    def test_12_postgres_events_match_python_canonical_digest_contract(self) -> None:
        value = self.fixture
        canonical_probe = {
            "z": None,
            "a": {"b": 2, "a": 1},
            "m": [True, "synthetic/value"],
        }
        expected_text = json.dumps(
            canonical_probe,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        sql_text = self._psql(
            "postgres",
            """
            SELECT sklegal_audit.canonical_json_text(
                '{"z":null,"a":{"b":2,"a":1},'
                '"m":[true,"synthetic/value"]}'::jsonb
            );
            """,
        ).stdout.strip()
        self.assertEqual(expected_text, sql_text)
        self.assertEqual(expected_text.encode("utf-8").hex(), sql_text.encode().hex())
        current_year = datetime.now(UTC).year
        canonical_years = (1, 9, 99, 999, 1000, current_year, 9999)
        timestamp_expressions = ",\n".join(
            f"('{year:04d}-01-02 03:04:05.123456+00'::timestamptz, {index})"
            for index, year in enumerate(canonical_years, start=1)
        )
        sql_timestamps = self._psql(
            "postgres",
            f"""
            SELECT sklegal_audit.canonical_timestamp(sample.value)
            FROM (VALUES {timestamp_expressions}) AS sample(value, position)
            ORDER BY sample.position;
            """,
        ).stdout.splitlines()
        python_timestamps = [
            _canonical_timestamp(datetime(year, 1, 2, 3, 4, 5, 123456, tzinfo=UTC))
            for year in canonical_years
        ]
        self.assertEqual(python_timestamps, sql_timestamps)

        for index, year in enumerate((1, 9, 99, 999, 1000, current_year), start=1):
            self._append_audit_event(
                event_id=f"a6810000-0000-4000-8000-{index:012d}",
                run_id="a6810000-0000-4000-8000-000000000010",
                correlation_id="a6810000-0000-4000-8000-000000000020",
                span_id=f"{128 + index:016x}",
                boundary="api",
                action=f"audit.synthetic.canonical-year-{year}",
                occurred_at=f"{year:04d}-01-01T00:00:00.000000Z",
                time_zone="Pacific/Chatham",
            )
        self._append_audit_event(
            event_id="a6800000-0000-4000-8000-000000000011",
            run_id="a6800000-0000-4000-8000-000000000001",
            correlation_id="a6800000-0000-4000-8000-000000000021",
            span_id="0000000000000071",
            boundary="api",
            action="audit.synthetic.canonical-zero",
            resource_kind="tenant",
            occurred_at="2026-08-20T12:34:56Z",
            tenant_scoped=True,
            include_decision_references=False,
            attributes=('{"error_code":null,"event_schema":"sklegal-audit-event/v1"}'),
            time_zone="Pacific/Chatham",
        )
        self._append_audit_event(
            event_id="a6800000-0000-4000-8000-000000000012",
            run_id="a6800000-0000-4000-8000-000000000001",
            correlation_id="a6800000-0000-4000-8000-000000000021",
            span_id="0000000000000072",
            boundary="connector",
            action="audit.synthetic.canonical-fraction",
            occurred_at="2026-08-20T12:34:56.123456Z",
            attributes=(
                '{"operation":"read","resource_version":7,'
                '"status_code":200,"retry_count":0}'
            ),
            time_zone="America/Los_Angeles",
        )
        raw_events = json.loads(
            self._psql(
                "postgres",
                f"""
                SET TIME ZONE 'Asia/Kathmandu';
                SELECT COALESCE(
                    jsonb_agg(
                        event.canonical_payload || jsonb_build_object(
                            'event_sha256', event.event_sha256,
                            'outbox_id', event.id
                        ) ORDER BY event.event_sequence
                    ),
                    '[]'::jsonb
                )::text
                FROM sklegal_audit.events AS event
                WHERE event.tenant_id = '{value["tenant_alpha"]}';
                """,
            ).stdout.splitlines()[-1]
        )
        reconstructed: list[DurableAuditEvent] = []
        for payload in raw_events:
            wire = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            source_event = DurableAuditEvent.model_validate_json(wire)
            draft = AuditEventDraft.model_validate(
                {
                    field: getattr(source_event, field)
                    for field in AuditEventDraft.model_fields
                }
            )

            def executor(
                sql: str,
                parameters: tuple[object, ...],
                returned_wire: str = wire,
            ) -> dict[str, object]:
                self.assertTrue(sql)
                self.assertEqual(19, len(parameters))
                return {"event": returned_wire}

            rebuilt = PostgresAuditRepository(executor).append(draft)
            self.assertEqual(source_event, rebuilt)
            self.assertEqual(source_event.event_sha256, recompute_event_sha256(rebuilt))
            reconstructed.append(rebuilt)

        self.assertGreaterEqual(len(reconstructed), 2)
        self.assertTrue(verify_event_chain(reconstructed))


if __name__ == "__main__":
    unittest.main()
