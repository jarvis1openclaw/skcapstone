"""Outbox watermark and tenant chain persistence contract tests."""

from __future__ import annotations

import json
import subprocess
import unittest

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract10OutboxWatermarkTests(PersistenceContractBase):
    def test_12_projection_watermark_initialization_is_serialized(self) -> None:
        run_id = "a6400000-0000-4000-8000-000000000001"
        first = json.loads(
            self._append_audit_event(
                event_id="a6400000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6400000-0000-4000-8000-000000000021",
                span_id="0000000000000031",
                boundary="workflow",
                action="audit.synthetic.race.first",
            ).stdout
        )
        second = json.loads(
            self._append_audit_event(
                event_id="a6400000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6400000-0000-4000-8000-000000000021",
                span_id="0000000000000032",
                boundary="workflow",
                action="audit.synthetic.race.second",
            ).stdout
        )
        for label, initial, competing in (
            ("forward", first, second),
            ("reverse", second, first),
        ):
            with self.subTest(order=label):
                projection = f"audit.race.{label}"
                application_name = f"sklegal-s105-watermark-{label}"
                initializer_sql = f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', 0, NULL,
                        {initial["event_sequence"]}, '{initial["event_sha256"]}'
                    );
                    SELECT pg_sleep(1.0);
                    COMMIT;
                """
                initializer = subprocess.Popen(
                    [
                        *self._psql_command("sklegal_test_alpha_one"),
                        "--command",
                        initializer_sql,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._wait_for_sleep(application_name, initializer)
                raced = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', 0, NULL,
                        {competing["event_sequence"]},
                        '{competing["event_sha256"]}'
                    );
                    """,
                    check=False,
                )
                stdout, stderr = initializer.communicate(timeout=10)
                self.assertEqual(0, initializer.returncode, stderr)
                self.assertIn("t", stdout.splitlines())
                self.assertNotEqual(0, raced.returncode)
                self.assertIn("projection watermark is stale", raced.stderr)
                current = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT event_sequence || ':' || event_sha256
                    FROM sklegal_audit.projection_watermarks
                    WHERE projection = '{projection}';
                    """,
                ).stdout.strip()
                self.assertEqual(
                    f"{initial['event_sequence']}:{initial['event_sha256']}",
                    current,
                )
                duplicate = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', {initial["event_sequence"]},
                        '{initial["event_sha256"]}',
                        {initial["event_sequence"]}, '{initial["event_sha256"]}'
                    );
                    """,
                )
                self.assertEqual("f", duplicate.stdout.strip())
                next_move = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', {initial["event_sequence"]},
                        '{initial["event_sha256"]}',
                        {competing["event_sequence"]},
                        '{competing["event_sha256"]}'
                    );
                    """,
                    check=label == "forward",
                )
                if label == "forward":
                    self.assertEqual("t", next_move.stdout.strip())
                else:
                    self.assertNotEqual(0, next_move.returncode)
                    self.assertIn("target audit event is unavailable", next_move.stderr)

    def test_12_projection_watermark_is_exact_monotonic_and_idempotent(self) -> None:
        run_id = "a6300000-0000-4000-8000-000000000001"
        first = json.loads(
            self._append_audit_event(
                event_id="a6300000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6300000-0000-4000-8000-000000000021",
                span_id="0000000000000021",
                boundary="api",
                action="audit.synthetic.projection.first",
            ).stdout
        )
        second = json.loads(
            self._append_audit_event(
                event_id="a6300000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6300000-0000-4000-8000-000000000021",
                span_id="0000000000000022",
                boundary="workflow",
                action="audit.synthetic.projection.second",
            ).stdout
        )
        advanced = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.projection', 0, NULL,
                {second["event_sequence"]}, '{second["event_sha256"]}'
            );
            """,
        )
        duplicate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.projection', {second["event_sequence"]},
                '{second["event_sha256"]}', {second["event_sequence"]},
                '{second["event_sha256"]}'
            );
            """,
        )
        self.assertEqual("t", advanced.stdout.strip())
        self.assertEqual("f", duplicate.stdout.strip())
        stale = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.projection', 0, NULL,
                {first["event_sequence"]}, '{first["event_sha256"]}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("projection watermark is stale", stale.stderr)
        unknown = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.unknown', 0, NULL,
                {second["event_sequence"]}, '{"f" * 64}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(0, unknown.returncode)
        self.assertIn("target audit event is unavailable", unknown.stderr)
        tamper = self._psql(
            "postgres",
            """
            UPDATE sklegal_audit.projection_watermarks
            SET event_sequence = event_sequence + 1
            WHERE projection = 'synthetic.projection';
            """,
            check=False,
        )
        self.assertNotEqual(0, tamper.returncode)
        self.assertIn("controlled projection writer", tamper.stderr)

    def test_12_tenant_chain_verification_sees_hidden_matter_rows(self) -> None:
        value = self.fixture
        run_id = "a6500000-0000-4000-8000-000000000001"
        prefix = json.loads(
            self._append_audit_event(
                event_id="a6500000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6500000-0000-4000-8000-000000000021",
                span_id="0000000000000041",
                boundary="api",
                action="audit.synthetic.visible-prefix",
            ).stdout
        )
        hidden = json.loads(
            self._append_audit_event(
                event_id="a6500000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6500000-0000-4000-8000-000000000021",
                span_id="0000000000000042",
                boundary="human",
                action="audit.synthetic.hidden",
                role="sklegal_test_alpha_two",
                tenant_id=value["tenant_alpha"],
                matter_id=value["matter_alpha_two"],
                principal_id=value["principal_alpha_two"],
            ).stdout
        )
        visible = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SET sklegal.audit_integrity_verification = 'on';
            SELECT count(*) || ':' || max(event_sequence)
            FROM sklegal_audit.events WHERE run_id = '{run_id}';
            """,
        ).stdout.strip()
        self.assertEqual(f"1:{prefix['event_sequence']}", visible.splitlines()[-1])
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_audit.verify_current_tenant_chain();",
            ).stdout.strip(),
        )
        try:
            self._psql(
                "postgres",
                f"""
                ALTER TABLE sklegal_audit.events
                    DISABLE TRIGGER audit_events_append_only;
                UPDATE sklegal_audit.events
                SET action = 'audit.synthetic.hidden-tamper'
                WHERE tenant_id = '{value["tenant_alpha"]}'
                  AND id = '{hidden["event_id"]}';
                ALTER TABLE sklegal_audit.events
                    ENABLE TRIGGER audit_events_append_only;
                """,
            )
            self.assertEqual(
                "f",
                self._psql(
                    "sklegal_test_alpha_one",
                    "SELECT sklegal_audit.verify_current_tenant_chain();",
                ).stdout.strip(),
            )
        finally:
            self._psql(
                "postgres",
                f"""
                ALTER TABLE sklegal_audit.events
                    DISABLE TRIGGER audit_events_append_only;
                UPDATE sklegal_audit.events
                SET action = 'audit.synthetic.hidden'
                WHERE tenant_id = '{value["tenant_alpha"]}'
                  AND id = '{hidden["event_id"]}';
                ALTER TABLE sklegal_audit.events
                    ENABLE TRIGGER audit_events_append_only;
                """,
            )
        after_gap = json.loads(
            self._append_audit_event(
                event_id="a6500000-0000-4000-8000-000000000013",
                run_id=run_id,
                correlation_id="a6500000-0000-4000-8000-000000000021",
                span_id="0000000000000043",
                boundary="connector",
                action="audit.synthetic.visible-after-gap",
            ).stdout
        )
        self.assertEqual(prefix["event_sequence"] + 2, after_gap["event_sequence"])
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_audit.verify_current_tenant_chain();",
            ).stdout.strip(),
        )


if __name__ == "__main__":
    unittest.main()
