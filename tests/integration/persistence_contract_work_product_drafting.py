"""Work product template and bracketed-unknown blocker contract tests."""

from __future__ import annotations

import unittest

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract07BWorkProductDraftingTests(PersistenceContractBase):
    def test_07g_template_lifecycle_supersession_and_immutability(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        role = "sklegal_test_alpha_one"
        template = "92000000-0000-4000-8000-000000000001"
        first_version = "92000000-0000-4000-8001-000000000001"
        second_version = "92000000-0000-4000-8001-000000000002"
        digest_one = "1" * 64
        digest_two = "2" * 64
        self._psql(
            role,
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_product_templates
                (id, tenant_id, name, work_product_kind, current_version_id,
                 current_version_number, current_content_sha256)
            VALUES ('{template}', '{tenant}', 'Synthetic memo template', 'memo',
                    '{first_version}', 1, '{digest_one}');
            INSERT INTO sklegal_legal.work_product_template_versions
                (id, tenant_id, template_id, version_number, content_sha256)
            VALUES ('{first_version}', '{tenant}', '{template}', 1, '{digest_one}');
            COMMIT;
            """,
        )
        premature = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_template(
                '{tenant}', '{template}', 1, 'active');
            """,
            check=False,
        )
        self.assertNotEqual(0, premature.returncode)
        self.assertIn("exact current version to be frozen", premature.stderr)

        frozen_insert = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_template_versions
                (id, tenant_id, template_id, version_number, content_sha256,
                 status)
            VALUES ('92000000-0000-4000-8001-000000000099', '{tenant}',
                    '{template}', 99, '{digest_two}', 'frozen');
            """,
            check=False,
        )
        self.assertNotEqual(0, frozen_insert.returncode)

        direct_payload_update = self._psql(
            role,
            f"""
            UPDATE sklegal_legal.work_product_template_versions
            SET content_sha256 = '{digest_two}', version = version + 1
            WHERE id = '{first_version}';
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_payload_update.returncode)
        self.assertEqual(
            digest_one,
            self._psql(
                role,
                f"""
                SELECT content_sha256
                FROM sklegal_legal.work_product_template_versions
                WHERE id = '{first_version}';
                """,
            ).stdout.strip(),
        )
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_template_version(
                '{tenant}', '{first_version}', 1, 'frozen');
            SELECT status FROM sklegal_legal.transition_work_product_template(
                '{tenant}', '{template}', 1, 'active');
            """,
        )
        rename_after_activation = self._psql(
            role,
            f"""
            UPDATE sklegal_legal.work_product_templates
            SET name = 'Late rename', version = version + 1
            WHERE id = '{template}';
            """,
            check=False,
        )
        self.assertNotEqual(0, rename_after_activation.returncode)
        self.assertIn(
            "permission denied for table work_product_templates",
            rename_after_activation.stderr,
        )

        self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_template_versions
                (id, tenant_id, template_id, version_number, content_sha256)
            VALUES ('{second_version}', '{tenant}', '{template}', 2,
                    '{digest_two}');
            SELECT status FROM sklegal_legal.transition_work_product_template_version(
                '{tenant}', '{second_version}', 1, 'frozen');
            """,
        )
        backward = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.advance_work_product_template(
                '{tenant}', '{template}', 2, '{first_version}', 1,
                '{digest_one}');
            """,
            check=False,
        )
        self.assertNotEqual(0, backward.returncode)
        self.assertIn("advances forward only", backward.stderr)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.advance_work_product_template(
                '{tenant}', '{template}', 2, '{second_version}', 2,
                '{digest_two}');
            """,
        )
        archive_current = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_template_version(
                '{tenant}', '{second_version}', 2, 'archived');
            """,
            check=False,
        )
        self.assertNotEqual(0, archive_current.returncode)
        self.assertIn("current version cannot be archived", archive_current.stderr)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_template_version(
                '{tenant}', '{first_version}', 2, 'archived');
            """,
        )
        self.assertEqual(
            "active:2",
            self._psql(
                role,
                f"""
                SELECT status || ':' || current_version_number
                FROM sklegal_legal.work_product_templates
                WHERE id = '{template}';
                """,
            ).stdout.strip(),
        )
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_template(
                '{tenant}', '{template}', 3, 'retired');
            """,
        )
        reactivate = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_template(
                '{tenant}', '{template}', 4, 'active');
            """,
            check=False,
        )
        self.assertNotEqual(0, reactivate.returncode)
        self.assertIn("adjacent declared edge", reactivate.stderr)

    def test_07h_unknown_placeholder_blocks_freeze_until_resolved(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        role = "sklegal_test_alpha_one"
        work_product = "92000000-0000-4000-8000-000000000010"
        artifact = "92000000-0000-4000-8001-000000000010"
        unknown = "92000000-0000-4000-8002-000000000010"
        digest = "3" * 64
        self._psql(
            role,
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{work_product}', '{tenant}', '{matter}',
                    'Synthetic blocked memo', 'memo', '{artifact}', 1,
                    '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}', 1,
                    '{digest}', '92000000-0000-4000-8003-000000000010');
            INSERT INTO sklegal_legal.work_product_unknowns
                (id, tenant_id, matter_id, work_product_version_id,
                 work_product_version_number, work_product_content_sha256,
                 placeholder_key, hint)
            VALUES ('{unknown}', '{tenant}', '{matter}', '{artifact}', 1,
                    '{digest}', 'client_name', 'Full legal name');
            COMMIT;
            """,
        )
        blocked = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
            """,
            check=False,
        )
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("unresolved bracketed unknowns", blocked.stderr)

        direct_resolve = self._psql(
            role,
            f"""
            UPDATE sklegal_legal.work_product_unknowns
            SET status = 'resolved', resolved_by_principal_id =
                    '{value["principal_alpha_one"]}',
                resolved_at = clock_timestamp(), version = version + 1
            WHERE id = '{unknown}';
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_resolve.returncode)

        future_resolution = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.resolve_work_product_unknown(
                '{tenant}', '{matter}', '{unknown}', 1,
                clock_timestamp() + interval '1 day');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_resolution.returncode)
        self.assertIn("not in the future", future_resolution.stderr)

        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.resolve_work_product_unknown(
                '{tenant}', '{matter}', '{unknown}', 1,
                '2026-08-01T00:00:00Z');
            """,
        )
        resolved = self._psql(
            role,
            f"""
            SELECT status || ':' || version || ':' ||
                   (resolved_by_principal_id = '{value["principal_alpha_one"]}')
            FROM sklegal_legal.work_product_unknowns
            WHERE id = '{unknown}';
            """,
        )
        self.assertEqual("resolved:2:true", resolved.stdout.strip())
        stale_resolve = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.resolve_work_product_unknown(
                '{tenant}', '{matter}', '{unknown}', 1,
                '2026-08-01T00:00:00Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, stale_resolve.returncode)
        self.assertIn("unknown conflict", stale_resolve.stderr)
        self.assertEqual(
            "frozen",
            self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
                """,
            ).stdout.strip(),
        )

        duplicate = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_unknowns
                (id, tenant_id, matter_id, work_product_version_id,
                 work_product_version_number, work_product_content_sha256,
                 placeholder_key)
            VALUES ('92000000-0000-4000-8002-000000000011', '{tenant}',
                    '{matter}', '{artifact}', 1, '{digest}', 'client_name');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate.returncode)
        resolved_insert = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_unknowns
                (id, tenant_id, matter_id, work_product_version_id,
                 work_product_version_number, work_product_content_sha256,
                 placeholder_key, status, resolved_by_principal_id, resolved_at)
            VALUES ('92000000-0000-4000-8002-000000000012', '{tenant}',
                    '{matter}', '{artifact}', 1, '{digest}', 'forum_name',
                    'resolved', '{value["principal_alpha_one"]}',
                    clock_timestamp());
            """,
            check=False,
        )
        self.assertNotEqual(0, resolved_insert.returncode)
        bad_key = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_unknowns
                (id, tenant_id, matter_id, work_product_version_id,
                 work_product_version_number, work_product_content_sha256,
                 placeholder_key)
            VALUES ('92000000-0000-4000-8002-000000000013', '{tenant}',
                    '{matter}', '{artifact}', 1, '{digest}', 'BadKey');
            """,
            check=False,
        )
        self.assertNotEqual(0, bad_key.returncode)
        self.assertIn("placeholder_key", bad_key.stderr)

    def test_07i_drafting_rows_stay_inside_the_tenant_boundary(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        other_tenant = value["tenant_beta"]
        matter = value["matter_alpha_one"]
        role = "sklegal_test_alpha_one"
        self.assertEqual(
            "0:0:0",
            self._psql(
                role,
                f"""
                SELECT (SELECT count(*) FROM sklegal_legal.work_product_templates
                        WHERE tenant_id = '{other_tenant}')::text || ':' ||
                       (SELECT count(*) FROM sklegal_legal.work_product_template_versions
                        WHERE tenant_id = '{other_tenant}')::text || ':' ||
                       (SELECT count(*) FROM sklegal_legal.work_product_unknowns
                        WHERE tenant_id = '{other_tenant}')::text;
                """,
            ).stdout.strip(),
        )
        cross_tenant_template = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_templates
                (id, tenant_id, name, work_product_kind, current_version_id,
                 current_version_number, current_content_sha256)
            VALUES ('92000000-0000-4000-8000-000000000020', '{other_tenant}',
                    'Cross-tenant template', 'memo',
                    '92000000-0000-4000-8001-000000000020', 1, '{"4" * 64}');
            """,
            check=False,
        )
        self.assertNotEqual(0, cross_tenant_template.returncode)
        cross_tenant_unknown = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.work_product_unknowns
                (id, tenant_id, matter_id, work_product_version_id,
                 work_product_version_number, work_product_content_sha256,
                 placeholder_key)
            VALUES ('92000000-0000-4000-8002-000000000020', '{other_tenant}',
                    '{value["matter_beta_one"]}',
                    '92000000-0000-4000-8001-000000000021', 1, '{"4" * 64}',
                    'client_name');
            """,
            check=False,
        )
        self.assertNotEqual(0, cross_tenant_unknown.returncode)
        self.assertEqual(
            "0",
            self._psql(
                "sklegal_test_beta_one",
                """
                SELECT count(*) FROM sklegal_legal.work_product_templates;
                """,
            ).stdout.strip(),
        )
        self.assertTrue(
            self._psql(
                role,
                f"""
                SELECT (SELECT count(*) FROM sklegal_legal.work_product_templates
                        WHERE tenant_id = '{tenant}')::text || ':' ||
                       (SELECT count(*) FROM sklegal_legal.work_product_unknowns
                        WHERE tenant_id = '{tenant}' AND matter_id = '{matter}')::text;
                """,
            )
            .stdout.strip()
            .startswith("1:"),
        )


if __name__ == "__main__":
    unittest.main()
