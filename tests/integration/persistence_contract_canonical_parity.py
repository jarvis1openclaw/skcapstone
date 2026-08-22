"""Canonical write-authority and round-trip parity persistence contract tests."""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any
from uuid import UUID

import sklegal_domain
from sklegal_domain.base import DomainEntity
from sklegal_persistence import (
    MAPPINGS,
    MappingContractError,
    PersistenceMetadata,
    decompose,
    reconstruct,
    reconstruct_with_metadata,
)

from tests.integration.persistence_contract_support import PersistenceContractBase
from tests.support.fresh_persistence_contract import build_fresh_contract
from tests.support.persistence_write_adapter import (
    auxiliary_statements,
    create_communication_statement,
    insert_statement,
    relation_statements,
)


class PersistenceContract08CanonicalParityTests(PersistenceContractBase):
    def test_11_canonical_entities_write_first_through_declared_authorities(
        self,
    ) -> None:
        self.maxDiff = None
        contract = build_fresh_contract()
        expected_entities = dict(contract.entities)
        payloads = {
            name: decompose(entity, contract.metadata[name])
            for name, entity in contract.entities.items()
            if name not in {"Execution", "ExecutionReceipt"}
        }
        final_execution_template = contract.entities["Execution"]
        draft_execution_data = final_execution_template.model_dump(mode="python")
        draft_execution_data.update(
            {
                "version": 1,
                "updated_at": draft_execution_data["created_at"],
                "validation_result": None,
                "approval": None,
                "events": (),
                "receipt": None,
                "status": sklegal_domain.ExecutionStatus.DRAFT,
            }
        )
        draft_execution = type(final_execution_template).model_validate(
            draft_execution_data, strict=True
        )
        execution_insert = decompose(
            draft_execution,
            PersistenceMetadata(
                scalar={
                    "validation_result_id": None,
                    "approval_id": None,
                    "approval_version": None,
                },
                relations={
                    "validation_result": (),
                    "approval": (),
                    "events": (),
                    "receipt": (),
                },
            ),
        )
        payloads["Execution"] = execution_insert
        auxiliary = tuple(
            decompose(entity, metadata)
            for entity, metadata in zip(
                contract.auxiliary_entities,
                contract.auxiliary_metadata,
                strict=True,
            )
        )
        self.assertEqual(set(MAPPINGS) - {"ExecutionReceipt"}, set(payloads))
        self.assertEqual(33, len(payloads))

        tenant = str(contract.tenant_id)
        principal = str(contract.principal_id)
        matter = str(contract.matter_id)
        role = contract.runtime_role
        administrative_entities = ("Tenant", "Client", "Engagement", "Matter")
        administrative_sql = "\n".join(
            insert_statement(payloads[name].table, payloads[name].row)
            for name in administrative_entities
        )
        self._psql(
            "postgres",
            f"""
            BEGIN;
            {administrative_sql}
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, status)
            VALUES ('{principal}', '{tenant}', 'human',
                    'Synthetic Fresh Principal', 'active');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES ('{tenant}', '{principal}', 'administrator');
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{tenant}', '{matter}', '{principal}', 'administrator');
            CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOINHERIT NOBYPASSRLS NOREPLICATION;
            COMMIT;
            """,
        )
        self._provision(role, tenant, principal)

        runtime_base_order = (
            "Forum",
            "Proceeding",
            "Party",
            "PartyRole",
            "MatterEvent",
            "Transaction",
            "FactAssertion",
            "TensionGroup",
            "EvidenceItem",
            "CustodyEvent",
            "Authority",
            "Issue",
            "Claim",
            "Defense",
            "Element",
            "Remedy",
            "DeadlineCalculation",
            "Deadline",
            "Task",
            "Communication",
            "WorkProduct",
            "WorkProductVersion",
            "WorkProductTemplate",
            "WorkProductTemplateVersion",
            "WorkProductUnknown",
        )
        base_payloads = [payloads[name] for name in runtime_base_order]
        base_payloads.extend(auxiliary)
        relation_sql = tuple(
            statement
            for payload in base_payloads
            if payload.entity_name != "Communication"
            for statement in relation_statements(payload)
        )
        source_sql = tuple(
            dict.fromkeys(
                statement
                for statement in relation_sql
                if statement.startswith("INSERT INTO sklegal_legal.source_references")
            )
        )
        link_sql = tuple(
            statement
            for statement in relation_sql
            if not statement.startswith("INSERT INTO sklegal_legal.source_references")
        )
        base_sql: list[str] = list(source_sql)
        for name in runtime_base_order:
            payload = payloads[name]
            base_sql.extend(auxiliary_statements(payload))
            if name == "Communication":
                base_sql.append(create_communication_statement(payload))
                continue
            overrides: dict[str, Any] | None = (
                {"status": "draft"} if name == "WorkProductVersion" else None
            )
            if name == "WorkProductUnknown":
                overrides = {
                    "status": "open",
                    "resolved_by_principal_id": None,
                    "resolved_at": None,
                }
            base_sql.append(
                insert_statement(payload.table, payload.row, overrides=overrides)
            )
            if name == "FactAssertion":
                base_sql.append(insert_statement(auxiliary[0].table, auxiliary[0].row))
            if name == "Element":
                base_sql.append(insert_statement(auxiliary[1].table, auxiliary[1].row))
        self._psql(
            role,
            "BEGIN;\n" + "\n".join(base_sql) + "\n" + "\n".join(link_sql) + "\nCOMMIT;",
        )

        artifact = payloads["WorkProductVersion"]
        unknown = payloads["WorkProductUnknown"]
        resolved_at = expected_entities["WorkProductUnknown"].resolved_at
        assert resolved_at is not None
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.resolve_work_product_unknown(
                '{tenant}', '{matter}', '{unknown.row["id"]}', 1,
                '{resolved_at.isoformat()}');
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{artifact.row["id"]}', 1, 'frozen');
            """,
        )

        validation = payloads["ValidationResult"]
        self._psql(
            role,
            "BEGIN;\n"
            + insert_statement(validation.table, validation.row)
            + "\n"
            + "\n".join(relation_statements(validation))
            + "\nCOMMIT;",
        )

        approval = payloads["Approval"]
        approval_reason = contract.entities["Approval"].rationale or ""
        self._psql(
            role,
            insert_statement(
                approval.table,
                approval.row,
                overrides={
                    "status": "pending",
                    "reviewer_principal_id": None,
                    "decided_at": None,
                    "rationale": None,
                    "revoker_principal_id": None,
                    "revocation_rationale": None,
                    "revoked_at": None,
                },
            )
            + f"""
            SELECT status FROM sklegal_legal.transition_approval(
                '{tenant}', '{matter}', '{approval.row["id"]}', 1,
                'approved', '{approval_reason.replace("'", "''")}');
            """,
        )

        execution = payloads["Execution"]
        execution_id = str(execution.row["id"])
        self._psql(
            role,
            insert_statement(
                execution.table,
                execution.row,
                overrides={
                    "status": "draft",
                    "validation_result_id": None,
                    "approval_id": None,
                },
            )
            + f"""
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 1, 'validated',
                'synthetic-fresh-validated', '{validation.row["id"]}', NULL);
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 2, 'approved',
                'synthetic-fresh-approved', NULL, '{approval.row["id"]}');
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 3, 'queued',
                'synthetic-fresh-queued');
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 4, 'dispatched',
                'synthetic-fresh-dispatched');
            """,
        )

        receipt_evidence_at = datetime.fromisoformat(
            self._psql(
                role,
                f"""
                SELECT GREATEST(
                    clock_timestamp(), event.occurred_at + interval '1 microsecond'
                )
                FROM sklegal_legal.execution_events AS event
                WHERE event.tenant_id = '{tenant}'
                  AND event.matter_id = '{matter}'
                  AND event.execution_id = '{execution_id}'
                  AND event.step = 'dispatched';
                """,
            ).stdout.strip()
        )
        phase_receipt_data = contract.entities["ExecutionReceipt"].model_dump(
            mode="python"
        )
        phase_receipt_data.update(
            {
                "created_at": receipt_evidence_at,
                "updated_at": receipt_evidence_at,
                "received_at": receipt_evidence_at,
                "verified_at": receipt_evidence_at,
            }
        )
        phase_receipt = type(contract.entities["ExecutionReceipt"]).model_validate(
            phase_receipt_data, strict=True
        )
        receipt_payload = decompose(
            phase_receipt, contract.metadata["ExecutionReceipt"]
        )
        payloads["ExecutionReceipt"] = receipt_payload

        final_execution_data = final_execution_template.model_dump(mode="python")
        final_events = list(final_execution_data["events"])
        final_events[-1].update(
            {
                "created_at": receipt_evidence_at,
                "updated_at": receipt_evidence_at,
                "occurred_at": receipt_evidence_at,
            }
        )
        final_execution_data.update(
            {
                "events": tuple(final_events),
                "receipt": phase_receipt,
                "updated_at": receipt_evidence_at,
            }
        )
        final_execution = type(final_execution_template).model_validate(
            final_execution_data, strict=True
        )
        payloads["Execution"] = decompose(
            final_execution, contract.metadata["Execution"]
        )
        expected_entities["Execution"] = final_execution
        expected_entities["ExecutionReceipt"] = phase_receipt
        receipt = receipt_payload.row
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 5,
                'receipt_verified', 'synthetic-fresh-receipt_verified',
                NULL, NULL, '{receipt["id"]}',
                '{receipt["connector"]}', '{receipt["external_receipt_id"]}',
                '{receipt["received_at"].isoformat()}',
                '{receipt["verified_at"].isoformat()}');
            """,
        )
        self.assertEqual(set(MAPPINGS), set(payloads))
        self.assertEqual(34, len(payloads))

        write_authority = {
            name: payload.write_contract.authority for name, payload in payloads.items()
        }
        self.assertEqual(34, len(write_authority))
        self.assertEqual("administrative_bootstrap", write_authority["Tenant"])
        self.assertEqual("controlled_writer", write_authority["ExecutionEvent"])
        self.assertEqual("controlled_writer", write_authority["ExecutionReceipt"])
        receipt_inputs = set(receipt_payload.write_contract.canonical_input_paths)
        receipt_outputs = set(receipt_payload.write_contract.database_output_paths)
        self.assertTrue({"row.received_at", "row.verified_at"} <= receipt_inputs)
        self.assertFalse({"row.received_at", "row.verified_at"} & receipt_outputs)
        for entity_name, payload in payloads.items():
            with self.subTest(entity=entity_name, contract="write-disposition"):
                canonical_inputs = set(payload.write_contract.canonical_input_paths)
                database_outputs = set(payload.write_contract.database_output_paths)
                self.assertTrue(canonical_inputs)
                self.assertFalse(canonical_inputs & database_outputs)
        self.assertEqual(
            "f|f|f|f|f|f|f|f|t",
            self._psql(
                "postgres",
                f"""
                SELECT has_table_privilege('{role}', 'sklegal_identity.tenants', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.clients', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.engagements', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.matters', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.execution_events', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.execution_receipts', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.communications', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.communication_participants', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.executions', 'INSERT');
                """,
            ).stdout.strip(),
        )

        restored: dict[str, DomainEntity] = {}
        retained: dict[str, PersistenceMetadata] = {}
        for entity_name, expected_entity in expected_entities.items():
            mapping = MAPPINGS[entity_name]
            read_relation = mapping.read_relation or mapping.table
            if entity_name == "ExecutionEvent":
                where = f"execution_id = '{execution_id}' AND sequence_no = 1"
            else:
                where = f"id = '{expected_entity.id}'"
            rows = self._json_rows(role, read_relation, where)
            self.assertEqual(1, len(rows), entity_name)
            row = rows[0]
            relations = self._normalized_relations(role, entity_name, row)
            reconstruction = reconstruct_with_metadata(entity_name, row, relations)
            actual = reconstruction.entity.model_dump(mode="python")
            expected = expected_entity.model_dump(mode="python")
            normalized_expected = self._normalize_controlled_entity_outputs(
                expected, actual
            )
            self.assertEqual(normalized_expected, actual, entity_name)
            normalized_metadata = self._normalize_controlled_metadata_outputs(
                contract.metadata[entity_name], reconstruction.metadata
            )
            self.assertEqual(
                dict(normalized_metadata.scalar),
                dict(reconstruction.metadata.scalar),
                f"{entity_name} scalar persistence metadata",
            )
            self.assertEqual(
                set(normalized_metadata.relations),
                set(reconstruction.metadata.relations),
                f"{entity_name} relation metadata names",
            )
            for (
                relation_name,
                expected_relation_rows,
            ) in normalized_metadata.relations.items():
                self.assertEqual(
                    tuple(dict(item) for item in expected_relation_rows),
                    tuple(
                        dict(item)
                        for item in reconstruction.metadata.relations[relation_name]
                    ),
                    f"{entity_name}.{relation_name} persistence metadata",
                )
            restored[entity_name] = reconstruction.entity
            retained[entity_name] = reconstruction.metadata
        self.assertEqual(set(MAPPINGS), set(restored))
        self.assertEqual(34, len(retained))
        restored_execution = restored["Execution"]
        self.assertEqual(
            tuple(range(1, 6)),
            tuple(
                retained["Execution"]
                .relations["events"][index]["nested_metadata"]
                .scalar["sequence_no"]
                for index in range(len(restored_execution.events))
            ),
        )

    def test_11_every_domain_entity_round_trips_through_rls(self) -> None:
        role = "sklegal_test_alpha_one"
        restored: dict[str, DomainEntity] = {}
        retained_metadata: dict[str, PersistenceMetadata] = {}
        decomposed_count = 0
        for entity_name, mapping in MAPPINGS.items():
            read_relation = mapping.read_relation or mapping.table
            rows = self._json_rows(role, read_relation, order_by="id")
            self.assertTrue(rows, entity_name)
            row = rows[0]
            relations = self._normalized_relations(role, entity_name, row)
            reconstruction = reconstruct_with_metadata(entity_name, row, relations)
            entity = reconstruction.entity
            canonical = entity.model_dump(mode="python")
            self.assertEqual(
                canonical,
                type(entity)
                .model_validate(canonical, strict=True)
                .model_dump(mode="python"),
                entity_name,
            )
            write_payload = decompose(entity, reconstruction.metadata)
            self.assertEqual(mapping.table, write_payload.table, entity_name)
            self.assertEqual(row, write_payload.row, entity_name)
            self.assertEqual(set(relations), set(write_payload.relations), entity_name)
            for relation_name, relation_rows in relations.items():
                self.assertEqual(
                    self._canonical_container(relation_rows),
                    self._canonical_container(write_payload.relations[relation_name]),
                    f"{entity_name}.{relation_name}",
                )
            decomposed_count += 1
            restored[entity_name] = entity
            retained_metadata[entity_name] = reconstruction.metadata
        self.assertEqual(set(MAPPINGS), set(restored))
        self.assertEqual(34, decomposed_count)

        self.assertIn(
            "import_batch_id", retained_metadata["Matter"].relations["aliases"][0]
        )
        self.assertIn("current_version_number", retained_metadata["WorkProduct"].scalar)
        self.assertIn(
            "work_product_version_number",
            retained_metadata["Communication"].scalar,
        )
        self.assertIn("sequence_no", retained_metadata["ExecutionEvent"].scalar)

        authority_mapping = MAPPINGS["Authority"]
        self.assertIsNotNone(authority_mapping.history_relation)
        authority_history = self._json_rows(
            role, authority_mapping.history_relation or "", order_by="id, version"
        )
        grouped_history: dict[UUID, list[dict[str, Any]]] = {}
        for history_row in authority_history:
            grouped_history.setdefault(history_row["id"], []).append(history_row)
        versioned_history = max(grouped_history.values(), key=len)
        self.assertGreaterEqual(len(versioned_history), 2)
        self.assertIsNotNone(versioned_history[-2]["system_to"])
        self.assertIsNone(versioned_history[-1]["system_to"])
        for history_row in versioned_history:
            authority_relations = self._normalized_relations(
                role, "Authority", history_row
            )
            history_reconstruction = reconstruct_with_metadata(
                "Authority", history_row, authority_relations
            )
            history_write = decompose(
                history_reconstruction.entity, history_reconstruction.metadata
            )
            self.assertEqual(history_row, history_write.row)
        current_authority = self._json_rows(
            role,
            authority_mapping.read_relation or "",
            f"id = '{versioned_history[-1]['id']}'",
        )
        self.assertEqual(1, len(current_authority))
        self.assertEqual(versioned_history[-1], current_authority[0])

        matter_mapping = MAPPINGS["Matter"]
        matter_row = self._json_rows(role, matter_mapping.table)[0]
        with self.assertRaisesRegex(
            MappingContractError, "missing normalized relation"
        ):
            reconstruct("Matter", matter_row, {})
        with self.assertRaisesRegex(MappingContractError, "missing columns"):
            reconstruct("Tenant", {"id": matter_row["id"]}, {})


if __name__ == "__main__":
    unittest.main()
