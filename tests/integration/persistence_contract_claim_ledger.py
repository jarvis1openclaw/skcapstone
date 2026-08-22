"""Claim ledger persistence contract: support cardinality, isolation, history."""

from __future__ import annotations

import unittest

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract12ClaimLedgerTests(PersistenceContractBase):
    def test_15_ledger_claim_rejects_unsupported_write(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        digest = "c" * 64
        unsupported = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.ledger_claim_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}',
                    '8c000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.ledger_claims
                (id, tenant_id, matter_id, statement, policy_revision, version)
            VALUES ('8c000000-0000-4000-8000-000000000001', '{tenant}',
                    '{matter}', 'Unsupported synthetic claim.', '{digest}', 1);
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, unsupported.returncode)
        self.assertIn(
            "ledger claim requires at least one supporting source span",
            unsupported.stderr,
        )
        counter_only = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.ledger_claim_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}',
                    '8c000000-0000-4000-8000-000000000002');
            INSERT INTO sklegal_legal.ledger_claims
                (id, tenant_id, matter_id, statement, policy_revision, version)
            VALUES ('8c000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', 'Counter-only synthetic claim.', '{digest}', 1);
            INSERT INTO sklegal_legal.ledger_claim_support
                (id, tenant_id, matter_id, claim_id, kind, source_reference_id,
                 span_start, span_end, excerpt_sha256, recorded_by_principal_id,
                 policy_revision)
            VALUES ('8c000000-0000-4000-8000-000000000003', '{tenant}', '{matter}',
                    '8c000000-0000-4000-8000-000000000002', 'counter_support',
                    '80000000-0000-4000-8000-000000000001', 0, 4, '{digest}',
                    '{principal}', '{digest}');
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, counter_only.returncode)
        self.assertIn(
            "ledger claim requires at least one supporting source span",
            counter_only.stderr,
        )
        absent = self._psql(
            "sklegal_test_alpha_one",
            """
            SELECT count(*) FROM sklegal_legal.ledger_claims
            WHERE id IN ('8c000000-0000-4000-8000-000000000001',
                         '8c000000-0000-4000-8000-000000000002');
            """,
        )
        self.assertEqual("0", absent.stdout.strip())

    def test_16_ledger_claim_cross_tenant_and_cross_matter_denial(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        beta = value["tenant_beta"]
        matter = value["matter_alpha_one"]
        principal_beta = value["principal_beta_one"]
        digest = "c" * 64
        hidden_from_beta = self._psql(
            "sklegal_test_beta_one",
            "SELECT count(*) FROM sklegal_legal.ledger_claims;",
        )
        self.assertEqual("0", hidden_from_beta.stdout.strip())
        hidden_from_other_matter = self._psql(
            "sklegal_test_alpha_two",
            "SELECT count(*) FROM sklegal_legal.ledger_claims;",
        )
        self.assertEqual("0", hidden_from_other_matter.stdout.strip())
        cross_tenant_write = self._psql(
            "sklegal_test_beta_one",
            f"""
            INSERT INTO sklegal_legal.ledger_claim_support
                (id, tenant_id, matter_id, claim_id, kind, source_reference_id,
                 span_start, span_end, excerpt_sha256, recorded_by_principal_id,
                 policy_revision)
            VALUES ('8c000000-0000-4000-8000-000000000004', '{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000022', 'support',
                    '80000000-0000-4000-8000-000000000001', 0, 4, '{digest}',
                    '{principal_beta}', '{digest}');
            """,
            check=False,
        )
        self.assertNotEqual(0, cross_tenant_write.returncode)
        own_tenant_write = self._psql(
            "sklegal_test_beta_one",
            f"""
            INSERT INTO sklegal_legal.ledger_claims
                (id, tenant_id, matter_id, statement, policy_revision, version)
            VALUES ('8c000000-0000-4000-8000-000000000005', '{beta}',
                    '{matter}', 'Cross-scope synthetic claim.', '{digest}', 1);
            """,
            check=False,
        )
        self.assertNotEqual(0, own_tenant_write.returncode)

    def test_17_ledger_claim_revision_history_and_immutable_revision(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        claim_id = "80000000-0000-4000-8000-000000000022"
        digest = "8" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.ledger_claims
                (id, tenant_id, matter_id, statement, policy_revision, status,
                 version)
            VALUES ('{claim_id}', '{tenant}', '{matter}',
                    'Synthetic ledger claim revised statement.', '{digest}',
                    'under_review', 2);
            """,
        )
        history = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT string_agg(version || ':' || status || ':' ||
                              (system_to IS NULL)::text, ',' ORDER BY version)
            FROM sklegal_legal.ledger_claim_history
            WHERE id = '{claim_id}';
            """,
        )
        self.assertEqual("1:proposed:false,2:under_review:true", history.stdout.strip())
        current = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT version || ':' || status
            FROM sklegal_legal.ledger_claim_current
            WHERE id = '{claim_id}';
            """,
        )
        self.assertEqual("2:under_review", current.stdout.strip())
        changed_revision = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.ledger_claims
                (id, tenant_id, matter_id, statement, policy_revision, status,
                 version)
            VALUES ('{claim_id}', '{tenant}', '{matter}',
                    'Synthetic ledger claim revised statement.', '{"d" * 64}',
                    'supported', 3);
            """,
            check=False,
        )
        self.assertNotEqual(0, changed_revision.returncode)
        self.assertIn("policy revision is immutable", changed_revision.stderr)
        invalid_edge = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.ledger_claims
                (id, tenant_id, matter_id, statement, policy_revision, status,
                 version)
            VALUES ('{claim_id}', '{tenant}', '{matter}',
                    'Synthetic ledger claim revised statement.', '{digest}',
                    'proposed', 3);
            """,
            check=False,
        )
        self.assertNotEqual(0, invalid_edge.returncode)
        self.assertIn("not an adjacent declared edge", invalid_edge.stderr)
        support_append_only = self._psql(
            "sklegal_test_alpha_one",
            """
            DELETE FROM sklegal_legal.ledger_claim_support
            WHERE id = '80000000-0000-4000-8000-000000000024';
            """,
            check=False,
        )
        self.assertNotEqual(0, support_append_only.returncode)
        self.assertIn("permission denied", support_append_only.stderr)


if __name__ == "__main__":
    unittest.main()
