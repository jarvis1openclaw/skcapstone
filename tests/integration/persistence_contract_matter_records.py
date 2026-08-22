"""Encryption, legacy alias, tension, and domain state contract tests."""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import datetime

import sklegal_domain
from sklegal_domain.base import DomainEntity
from sklegal_persistence import (
    MAPPINGS,
    PersistenceMetadata,
    decompose,
    reconstruct_with_metadata,
)

from tests.integration.persistence_contract_support import PersistenceContractBase
from tests.support.persistence_write_adapter import insert_statement


class PersistenceContract03MatterRecordTests(PersistenceContractBase):
    def test_04_encryption_completeness_is_total_and_fail_closed(self) -> None:
        output = self._psql(
            "postgres",
            """
            WITH combinations AS (
                SELECT mask,
                    CASE WHEN mask & 1 = 1 THEN decode(repeat('ab', 16), 'hex') END AS payload,
                    CASE WHEN mask & 2 = 2 THEN 'vault:test/key-one' END AS key_ref,
                    CASE WHEN mask & 4 = 4 THEN 'aes-256-gcm' END AS algorithm,
                    CASE WHEN mask & 8 = 8 THEN clock_timestamp() END AS encrypted_at
                FROM generate_series(0, 15) AS mask
            )
            SELECT string_agg(mask || ':' || result, ',' ORDER BY mask)
            FROM (
                SELECT mask,
                    sklegal_identity.encrypted_payload_is_complete(
                        payload, key_ref, algorithm, encrypted_at
                    ) AS result
                FROM combinations
            ) AS checked;
            """,
        )
        results = dict(
            item.split(":", maxsplit=1) for item in output.stdout.strip().split(",")
        )
        self.assertEqual(
            {"0", "15"},
            {key for key, value in results.items() if value == "true"},
        )
        self.assertNotIn("", results.values())
        partial = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, encrypted_profile)
            VALUES ('40000000-0000-4000-8000-000000000001',
                    '{self.fixture["tenant_alpha"]}', 'agent', 'Partial encryption',
                    decode(repeat('ab', 16), 'hex'));
            """,
            check=False,
        )
        self.assertNotEqual(0, partial.returncode)
        self.assertIn("check constraint", partial.stderr)

    def test_05_strict_legacy_alias_binding_and_metadata(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        source_id = "50000000-0000-4000-8000-000000000001"
        event_id = "50000000-0000-4000-8000-000000000002"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{source_id}', '{tenant}', '{matter}', 'synthetic', 'v1',
                    '{"1" * 64}', 'synthetic/source-one', clock_timestamp());
            INSERT INTO sklegal_legal.matter_events
                (id, tenant_id, matter_id, event_type, description, observed_at,
                 source_reference_id)
            VALUES ('{event_id}', '{tenant}', '{matter}', 'synthetic_event',
                    'Synthetic event only.', clock_timestamp(), '{source_id}');
            INSERT INTO sklegal_legal.legacy_aliases
                (id, tenant_id, matter_id, canonical_record_kind,
                 canonical_record_id, legacy_record_kind, legacy_id, legacy_slug,
                 legacy_path, source_version, content_sha256, observed_at,
                 import_batch_id)
            VALUES
                ('50000000-0000-4000-8000-000000000003', '{tenant}', '{matter}',
                 'matter', '{matter}', 'problem', 'PRB-2026-100', 'synthetic-prb',
                 'problems/PRB-2026-100', 'v1', '{"2" * 64}', clock_timestamp(),
                 '50000000-0000-4000-8000-000000000010'),
                ('50000000-0000-4000-8000-000000000004', '{tenant}', '{matter}',
                 'matter_event', '{event_id}', 'incident', 'INC-100',
                 'synthetic-inc', 'incidents/INC-100', 'v1', '{"3" * 64}',
                 clock_timestamp(), '50000000-0000-4000-8000-000000000010');
            """,
        )
        invalid_cases = (
            ("matter", matter, "incident", "INC-101"),
            ("matter_event", event_id, "problem", "PRB-2026-101"),
            ("matter", matter, "problem", "PRB-101"),
        )
        for index, (canonical_kind, canonical_id, legacy_kind, legacy_id) in enumerate(
            invalid_cases, start=20
        ):
            denied = self._psql(
                "sklegal_test_alpha_one",
                f"""
                INSERT INTO sklegal_legal.legacy_aliases
                    (id, tenant_id, matter_id, canonical_record_kind,
                     canonical_record_id, legacy_record_kind, legacy_id,
                     legacy_slug, legacy_path, source_version, content_sha256,
                     observed_at, import_batch_id)
                VALUES ('50000000-0000-4000-8000-0000000000{index}',
                        '{tenant}', '{matter}', '{canonical_kind}',
                        '{canonical_id}', '{legacy_kind}', '{legacy_id}',
                        'invalid-{index}', 'synthetic/invalid-{index}', 'v1',
                        '{"4" * 64}', clock_timestamp(),
                        '50000000-0000-4000-8000-000000000010');
                """,
                check=False,
            )
            self.assertNotEqual(0, denied.returncode)
        for index, legacy_path in enumerate(
            (
                "/absolute/path",
                "synthetic/../escape",
                "synthetic/./dot",
                "synthetic//empty",
                "synthetic\\windows",
                " synthetic/outer-space",
            ),
            start=30,
        ):
            denied_path = self._psql(
                "sklegal_test_alpha_one",
                f"""
                INSERT INTO sklegal_legal.legacy_aliases
                    (id, tenant_id, matter_id, canonical_record_kind,
                     canonical_record_id, legacy_record_kind, legacy_id,
                     legacy_slug, legacy_path, source_version, content_sha256,
                     observed_at, import_batch_id)
                VALUES ('50000000-0000-4000-8000-0000000000{index}',
                        '{tenant}', '{matter}', 'matter', '{matter}', 'problem',
                        'PRB-2026-{index:03d}', 'invalid-path-{index}',
                        '{legacy_path}', 'v1', '{"4" * 64}', clock_timestamp(),
                        '50000000-0000-4000-8000-000000000010');
                """,
                check=False,
            )
            self.assertNotEqual(0, denied_path.returncode)
        owner_audit = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT matter.version || ':' || event.version || ':' ||
                   (matter.updated_at >= matter_alias.observed_at) || ':' ||
                   (event.updated_at >= event_alias.observed_at)
            FROM sklegal_legal.matters AS matter
            JOIN sklegal_legal.matter_events AS event
              ON event.tenant_id = matter.tenant_id
             AND event.matter_id = matter.matter_id
             AND event.id = '{event_id}'
            JOIN sklegal_legal.legacy_aliases AS matter_alias
              ON matter_alias.tenant_id = matter.tenant_id
             AND matter_alias.matter_id = matter.matter_id
             AND matter_alias.canonical_record_kind = 'matter'
            JOIN sklegal_legal.legacy_aliases AS event_alias
              ON event_alias.tenant_id = event.tenant_id
             AND event_alias.matter_id = event.matter_id
             AND event_alias.canonical_record_kind = 'matter_event'
            WHERE matter.id = '{matter}';
            """,
        )
        self.assertEqual("2:2:true:true", owner_audit.stdout.strip())

    def test_06_append_only_tensions_and_database_authority_history(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        source_id = "60000000-0000-4000-8000-000000000001"
        fact_one = "60000000-0000-4000-8000-000000000002"
        fact_two = "60000000-0000-4000-8000-000000000003"
        tension = "60000000-0000-4000-8000-000000000004"
        authority = "60000000-0000-4000-8000-000000000005"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{source_id}', '{tenant}', '{matter}', 'synthetic', 'v1',
                    '{"5" * 64}', 'synthetic/source-two', clock_timestamp());
            INSERT INTO sklegal_legal.fact_assertions
                (id, tenant_id, matter_id, subject_ref, predicate, value_type,
                 asserted_value, source_reference_id, source_locator, observed_at)
            VALUES
                ('{fact_one}', '{tenant}', '{matter}', '{matter}', 'timing',
                 'string', '"first"'::jsonb, '{source_id}', 'line:1',
                 clock_timestamp()),
                ('{fact_two}', '{tenant}', '{matter}', '{matter}', 'timing',
                 'string', '"second"'::jsonb, '{source_id}', 'line:2',
                 clock_timestamp());
            BEGIN;
            INSERT INTO sklegal_legal.tension_groups
                (id, tenant_id, matter_id, title)
            VALUES ('{tension}', '{tenant}', '{matter}', 'Synthetic tension');
            INSERT INTO sklegal_legal.tension_assertions
                (tenant_id, matter_id, tension_group_id, assertion_id)
            VALUES
                ('{tenant}', '{matter}', '{tension}', '{fact_one}'),
                ('{tenant}', '{matter}', '{tension}', '{fact_two}');
            COMMIT;
            INSERT INTO sklegal_legal.authority_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}', '{authority}');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version, system_from)
            VALUES ('{authority}', '{tenant}', '{matter}', 'Synthetic Authority',
                    'Synthetic 1', 'Synthetic', 'case', '{source_id}', 'proposed',
                    1, '2000-01-01T00:00:00Z');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version, system_from)
            VALUES ('{authority}', '{tenant}', '{matter}', 'Synthetic Authority',
                    'Synthetic 1', 'Synthetic', 'case', '{source_id}', 'challenged',
                    2, '2000-01-01T00:00:00Z');
            """,
        )
        history = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT count(*) || ':' || count(system_to) || ':' || max(version)
            FROM sklegal_legal.authority_history WHERE id = '{authority}';
            """,
        )
        self.assertEqual("2:1:2", history.stdout.strip())
        current = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT version FROM sklegal_legal.authority_current WHERE id = '{authority}';",
        )
        self.assertEqual("2", current.stdout.strip())
        controlled_time = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT bool_and(system_from > '2026-01-01T00:00:00Z')
            FROM sklegal_legal.authorities WHERE id = '{authority}';
            """,
        )
        self.assertEqual("t", controlled_time.stdout.strip())
        denied_link_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.tension_assertions
            SET assertion_id = '{fact_two}'
            WHERE tension_group_id = '{tension}' AND assertion_id = '{fact_one}';
            """,
            check=False,
        )
        denied_authority_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.authorities SET title = 'Rewritten'
            WHERE id = '{authority}' AND version = 1;
            """,
            check=False,
        )
        self.assertNotEqual(0, denied_link_update.returncode)
        self.assertNotEqual(0, denied_authority_update.returncode)

    def test_07_claim_and_defense_cardinality_never_cross_satisfies(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]

        def exercise(
            suffix: int,
            *,
            element_kind: str,
            target_kind: str,
            should_pass: bool,
        ) -> subprocess.CompletedProcess[str]:
            shared = f"73000000-0000-4000-8000-{suffix:012d}"
            issue = f"73000000-0000-4000-8001-{suffix:012d}"
            element = f"73000000-0000-4000-8002-{suffix:012d}"
            validation = f"73000000-0000-4000-8003-{suffix:012d}"
            target_table = "claims" if target_kind == "claim" else "defenses"
            self._psql(
                "postgres",
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.issues
                    (id, tenant_id, matter_id, question)
                VALUES ('{issue}', '{tenant}', '{matter}',
                        'Synthetic theory cardinality {suffix}?');
                INSERT INTO sklegal_legal.claims
                    (id, tenant_id, matter_id, issue_id, label, statement)
                VALUES ('{shared}', '{tenant}', '{matter}', '{issue}',
                        'Synthetic claim {suffix}', 'Synthetic claim statement.');
                INSERT INTO sklegal_legal.defenses
                    (id, tenant_id, matter_id, issue_id, label, statement)
                VALUES ('{shared}', '{tenant}', '{matter}', '{issue}',
                        'Synthetic defense {suffix}', 'Synthetic defense statement.');
                INSERT INTO sklegal_legal.elements
                    (id, tenant_id, matter_id, theory_kind, claim_id, description)
                VALUES ('{element}', '{tenant}', '{matter}', '{element_kind}',
                        '{shared}', 'Synthetic {element_kind} element.');
                COMMIT;
                """,
            )
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.validations
                    (id, tenant_id, matter_id, subject_kind,
                     subject_artifact_id, subject_artifact_version, outcome,
                     validator_principal_id, validated_at, rationale)
                VALUES ('{validation}', '{tenant}', '{matter}', '{target_kind}',
                        '{shared}', 1, 'passed', '{principal}',
                        clock_timestamp(), 'Synthetic exact theory validation.');
                INSERT INTO sklegal_legal.validation_checks
                    (tenant_id, matter_id, validation_id, check_id)
                VALUES ('{tenant}', '{matter}', '{validation}',
                        'synthetic.theory.{suffix}');
                COMMIT;
                """,
            )
            result = self._psql(
                "postgres",
                f"""
                BEGIN;
                UPDATE sklegal_legal.{target_table}
                SET status = 'accepted', acceptance_validation_id = '{validation}',
                    version = version + 1
                WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
                  AND id = '{shared}';
                COMMIT;
                """,
                check=False,
            )
            if should_pass:
                self.assertEqual(0, result.returncode, result.stderr)
                restored = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT target.status || ':' || count(element.id)
                    FROM sklegal_legal.{target_table} AS target
                    LEFT JOIN sklegal_legal.elements AS element
                      ON element.tenant_id = target.tenant_id
                     AND element.matter_id = target.matter_id
                     AND element.claim_id = target.id
                     AND element.theory_kind = '{target_kind}'
                    WHERE target.id = '{shared}'
                    GROUP BY target.status;
                    """,
                )
                self.assertEqual("accepted:1", restored.stdout.strip())
            else:
                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "accepted claim or defense requires an element", result.stderr
                )
            return result

        exercise(1, element_kind="claim", target_kind="defense", should_pass=False)
        exercise(2, element_kind="defense", target_kind="claim", should_pass=False)
        exercise(3, element_kind="claim", target_kind="claim", should_pass=True)
        exercise(4, element_kind="defense", target_kind="defense", should_pass=True)

    def test_07_matter_state_graph_including_closed_never_opened_round_trips(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        client = value["client_alpha"]
        engagement = value["engagement_alpha"]
        principal = value["principal_alpha_one"]
        role = "sklegal_test_alpha_one"
        created_at = "2026-08-20T04:00:00Z"
        updated_at = "2026-08-20T05:00:00Z"
        opened_at = "2026-08-20T04:30:00Z"
        closed_at = "2026-08-20T04:45:00Z"
        transition_closed_at = datetime.fromisoformat("2026-08-20T06:00:00+00:00")
        transition_archived_at = datetime.fromisoformat("2026-08-20T07:00:00+00:00")

        def matter_entity(
            suffix: int,
            status: str,
            *,
            opened: str | None = None,
            closed: str | None = None,
        ) -> DomainEntity:
            identifier = f"34000000-0000-4000-8000-{suffix:012d}"
            return sklegal_domain.Matter.model_validate_json(
                json.dumps(
                    {
                        "id": identifier,
                        "tenant_id": tenant,
                        "matter_id": identifier,
                        "client_id": client,
                        "engagement_id": engagement,
                        "title": f"Synthetic Matter State {status} {suffix}",
                        "summary": "Synthetic-only Matter state graph acceptance.",
                        "aliases": [],
                        "opened_at": opened,
                        "closed_at": closed,
                        "status": status,
                        "version": 1,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                    sort_keys=True,
                )
            )

        constructed = {
            "proposed": matter_entity(1, "proposed"),
            "open": matter_entity(2, "open", opened=opened_at),
            "closed_never_opened": matter_entity(3, "closed", closed=closed_at),
            "closed_after_opened": matter_entity(
                4, "closed", opened=opened_at, closed=closed_at
            ),
            "archived": matter_entity(
                5, "archived", opened=opened_at, closed=closed_at
            ),
        }
        proposed_transition_base = matter_entity(6, "proposed")
        opened_transition_base = matter_entity(7, "open", opened=opened_at)
        expected_closed_never_opened = proposed_transition_base.transition_to(
            sklegal_domain.MatterStatus.CLOSED,
            at=transition_closed_at,
            closed_at=transition_closed_at,
        )
        expected_archived = opened_transition_base.transition_to(
            sklegal_domain.MatterStatus.CLOSED,
            at=transition_closed_at,
            closed_at=transition_closed_at,
        ).transition_to(
            sklegal_domain.MatterStatus.ARCHIVED,
            at=transition_archived_at,
        )
        expected = {
            **constructed,
            "transitioned_closed_never_opened": expected_closed_never_opened,
            "transitioned_archived": expected_archived,
        }
        metadata = PersistenceMetadata(relations={"aliases": ()})
        decomposed = {
            name: decompose(entity, metadata) for name, entity in expected.items()
        }
        self.assertEqual(7, len(decomposed))
        insert_payloads = tuple(constructed.values()) + (
            proposed_transition_base,
            opened_transition_base,
        )
        inserts = "\n".join(
            insert_statement(
                decompose(entity, metadata).table,
                decompose(entity, metadata).row,
            )
            for entity in insert_payloads
        )
        memberships = "\n".join(
            "INSERT INTO sklegal_legal.matter_memberships "
            "(tenant_id, matter_id, principal_id, membership_role) VALUES "
            f"('{tenant}', '{entity.id}', '{principal}', 'administrator');"
            for entity in insert_payloads
        )
        self._psql(
            "postgres",
            "BEGIN;\n" + inserts + "\n" + memberships + "\nCOMMIT;",
        )
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.matters
            SET status = 'closed',
                closed_at = '{transition_closed_at.isoformat()}',
                version = version + 1
            WHERE tenant_id = '{tenant}'
              AND id = '{proposed_transition_base.id}';
            UPDATE sklegal_legal.matters
            SET status = 'closed',
                closed_at = '{transition_closed_at.isoformat()}',
                version = version + 1
            WHERE tenant_id = '{tenant}'
              AND id = '{opened_transition_base.id}';
            UPDATE sklegal_legal.matters
            SET status = 'archived', version = version + 1
            WHERE tenant_id = '{tenant}'
              AND id = '{opened_transition_base.id}';
            """,
        )
        observed_statuses: dict[str, str] = {}
        for name, expected_entity in expected.items():
            rows = self._json_rows(
                role,
                MAPPINGS["Matter"].table,
                f"id = '{expected_entity.id}'",
            )
            self.assertEqual(1, len(rows), name)
            reconstruction = reconstruct_with_metadata(
                "Matter", rows[0], {"aliases": []}
            )
            actual = reconstruction.entity.model_dump(mode="python")
            canonical_expected = expected_entity.model_dump(mode="python")
            self.assertEqual(
                self._normalize_controlled_entity_outputs(canonical_expected, actual),
                actual,
                name,
            )
            self.assertEqual(metadata, reconstruction.metadata, name)
            observed_statuses[name] = reconstruction.entity.status.value
        self.assertEqual(
            {
                "proposed": "proposed",
                "open": "open",
                "closed_never_opened": "closed",
                "closed_after_opened": "closed",
                "archived": "archived",
                "transitioned_closed_never_opened": "closed",
                "transitioned_archived": "archived",
            },
            observed_statuses,
        )
        self.assertIsNone(expected_closed_never_opened.opened_at)
        terminal_reopen = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.matters
            SET status = 'open', closed_at = NULL, version = version + 1
            WHERE tenant_id = '{tenant}' AND id = '{constructed["archived"].id}';
            """,
            check=False,
        )
        self.assertNotEqual(0, terminal_reopen.returncode)
        self.assertIn("invalid matter status transition", terminal_reopen.stderr)

    def test_07_representative_domain_state_constraints(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.evidence_items
                (id, tenant_id, matter_id, title, media_type, content_sha256,
                 source_reference_id)
            VALUES ('70000000-0000-4000-8000-000000000005', '{tenant}',
                    '{matter}', 'Synthetic state evidence', 'text/plain',
                    '{"6" * 64}', '60000000-0000-4000-8000-000000000001');
            """,
        )
        bad_statements = (
            f"""
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, status)
            VALUES ('70000000-0000-4000-8000-000000000001', '{tenant}',
                    '{value["client_alpha"]}', 'Bad active engagement', 'Synthetic',
                    'active');
            """,
            f"""
            INSERT INTO sklegal_legal.proceedings
                (id, tenant_id, matter_id, title, status)
            VALUES ('70000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', 'Bad active proceeding', 'active');
            """,
            f"""
            INSERT INTO sklegal_legal.tasks
                (id, tenant_id, matter_id, title, description, status)
            VALUES ('70000000-0000-4000-8000-000000000003', '{tenant}',
                    '{matter}', 'Bad ready task', 'Synthetic', 'ready');
            """,
            f"""
            INSERT INTO sklegal_legal.custody_events
                (id, tenant_id, matter_id, evidence_item_id, action, custodian_id,
                 occurred_at, source_reference_id)
            VALUES ('70000000-0000-4000-8000-000000000004', '{tenant}',
                    '{matter}', '70000000-0000-4000-8000-000000000005',
                    'rewritten', '{value["principal_alpha_one"]}',
                    clock_timestamp(), '60000000-0000-4000-8000-000000000001');
            """,
        )
        for statement in bad_statements:
            denied = self._psql("sklegal_test_alpha_one", statement, check=False)
            self.assertNotEqual(0, denied.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind,
                 source_reference_id)
            VALUES ('70000000-0000-4000-8000-000000000010', '{tenant}',
                    '{matter}', 'Optional source forum', 'Synthetic', 'other', NULL);
            INSERT INTO sklegal_legal.deadlines
                (id, tenant_id, matter_id, title, candidate_due_at, status)
            VALUES ('70000000-0000-4000-8000-000000000011', '{tenant}',
                    '{matter}', 'Unknown candidate date', NULL, 'candidate');
            """,
        )
        bad_authority_kind = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.authority_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}',
                    '70000000-0000-4000-8000-000000000012');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version)
            VALUES ('70000000-0000-4000-8000-000000000012', '{tenant}',
                    '{matter}', 'Bad kind', 'Synthetic', 'Synthetic',
                    'constitutional', '60000000-0000-4000-8000-000000000001',
                    'proposed', 1);
            """,
            check=False,
        )
        self.assertNotEqual(0, bad_authority_kind.returncode)
        missing_applicability = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version)
            VALUES ('70000000-0000-4000-8000-000000000012', '{tenant}',
                    '{matter}', 'Missing applicability', 'Synthetic', 'Synthetic',
                    'constitution', '60000000-0000-4000-8000-000000000001',
                    'not_applicable', 1);
            """,
            check=False,
        )
        self.assertNotEqual(0, missing_applicability.returncode)

    def test_07_typed_validation_rejects_failed_unrelated_and_stale_evidence(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        source = "71000000-0000-4000-8000-000000000001"
        party = "71000000-0000-4000-8000-000000000002"
        unrelated_party = "71000000-0000-4000-8000-000000000003"
        failed = "71000000-0000-4000-8000-000000000004"
        unrelated = "71000000-0000-4000-8000-000000000005"
        stale = "71000000-0000-4000-8000-000000000006"
        current = "71000000-0000-4000-8000-000000000007"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                    '{"7" * 64}', 'synthetic/typed-validation',
                    clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{party}', '{tenant}', '{matter}', 'Validation subject',
                 'person', '{source}'),
                ('{unrelated_party}', '{tenant}', '{matter}',
                 'Unrelated subject', 'person', '{source}');
            BEGIN;
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, outcome, validator_principal_id,
                 validated_at, rationale)
            VALUES
                ('{failed}', '{tenant}', '{matter}', 'party', '{party}', 1,
                 'failed', '{principal}', clock_timestamp(),
                 'Synthetic failed check.'),
                ('{unrelated}', '{tenant}', '{matter}', 'party',
                 '{unrelated_party}', 1, 'passed', '{principal}',
                 clock_timestamp(), 'Synthetic unrelated passed check.'),
                ('{stale}', '{tenant}', '{matter}', 'party', '{party}', 1,
                 'passed', '{principal}', clock_timestamp(),
                 'Synthetic version-one passed check.');
            INSERT INTO sklegal_legal.validation_checks
                (tenant_id, matter_id, validation_id, check_id)
            VALUES
                ('{tenant}', '{matter}', '{failed}', 'synthetic.failed'),
                ('{tenant}', '{matter}', '{unrelated}', 'synthetic.unrelated'),
                ('{tenant}', '{matter}', '{stale}', 'synthetic.stale');
            COMMIT;
            """,
        )
        for reference in (failed, unrelated):
            denied = self._psql(
                "postgres",
                f"""
                UPDATE sklegal_legal.parties
                SET status = 'verified', verification_reference_id = '{reference}',
                    version = version + 1
                WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
                  AND id = '{party}';
                """,
                check=False,
            )
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("exact typed prior version", denied.stderr)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.parties
            SET display_name = 'Validation subject revised', version = version + 1
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND id = '{party}';
            """,
        )
        stale_denied = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.parties
            SET status = 'verified', verification_reference_id = '{stale}',
                version = version + 1
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND id = '{party}';
            """,
            check=False,
        )
        self.assertNotEqual(0, stale_denied.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, outcome, validator_principal_id,
                 validated_at, rationale)
            VALUES ('{current}', '{tenant}', '{matter}', 'party', '{party}', 2,
                    'passed', '{principal}', clock_timestamp(),
                    'Synthetic current exact check.');
            INSERT INTO sklegal_legal.validation_checks
                (tenant_id, matter_id, validation_id, check_id)
            VALUES ('{tenant}', '{matter}', '{current}', 'synthetic.current');
            COMMIT;
            """,
        )
        actor_spoof = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, outcome, validator_principal_id,
                 validated_at, rationale)
            VALUES ('71000000-0000-4000-8000-000000000008', '{tenant}',
                    '{matter}', 'party', '{party}', 2, 'passed',
                    '{value["principal_alpha_two"]}', clock_timestamp(),
                    'Actor spoof attempt.');
            """,
            check=False,
        )
        self.assertNotEqual(0, actor_spoof.returncode)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.parties
            SET status = 'verified', verification_reference_id = '{current}',
                version = version + 1
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND id = '{party}';
            """,
        )
        restored = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT status || ':' || version FROM sklegal_legal.parties "
            f"WHERE id = '{party}';",
        )
        self.assertEqual("verified:3", restored.stdout.strip())


if __name__ == "__main__":
    unittest.main()
