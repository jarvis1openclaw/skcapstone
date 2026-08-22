from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sklegal_domain import (
    ApprovalStatus,
    DataClassification,
    Defense,
    Element,
    ElementStatus,
    LegacyAlias,
    LegacyRecordKind,
    Matter,
    RecordCompleteness,
    Tenant,
    TenantStatus,
)
from sklegal_persistence import (
    MAPPINGS,
    MappingContractError,
    PersistenceMetadata,
    decompose,
    reconstruct,
    reconstruct_with_metadata,
    validate_mapping_contract,
)

from tests.support.fresh_persistence_contract import build_fresh_contract

AT = datetime(2026, 1, 1, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")


def audited(identifier: str) -> dict[str, object]:
    return {
        "id": UUID(identifier),
        "version": 1,
        "created_at": AT,
        "updated_at": AT,
        "tenant_id": TENANT_ID,
        "classification": "confidential",
    }


def mutable_relations(
    relations: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    copied: dict[str, list[dict[str, object]]] = {}
    for relation, rows in relations.items():
        copied_rows: list[dict[str, object]] = []
        for source_row in rows:
            row = dict(source_row)
            nested = row.get("row")
            if isinstance(nested, Mapping):
                row["row"] = dict(nested)
            copied_rows.append(row)
        copied[relation] = copied_rows
    return copied


def metadata_with_relation_change(
    metadata: PersistenceMetadata,
    relation: str,
    index: int,
    **changes: object,
) -> PersistenceMetadata:
    relation_rows = {
        name: tuple(dict(row) for row in rows)
        for name, rows in metadata.relations.items()
    }
    selected = list(relation_rows[relation])
    selected[index] = {**selected[index], **changes}
    relation_rows[relation] = tuple(selected)
    return PersistenceMetadata(scalar=metadata.scalar, relations=relation_rows)


class PersistenceMappingTests(unittest.TestCase):
    def test_mapping_contract_covers_every_public_domain_entity(self) -> None:
        validate_mapping_contract()
        self.assertEqual(33, len(MAPPINGS))

    def test_all_decompositions_have_closed_machine_verifiable_write_contracts(
        self,
    ) -> None:
        contract = build_fresh_contract()
        payloads = {
            name: decompose(entity, contract.metadata[name])
            for name, entity in contract.entities.items()
        }
        self.assertEqual(set(MAPPINGS), set(payloads))
        for name, payload in payloads.items():
            with self.subTest(entity=name):
                inputs = set(payload.write_contract.canonical_input_paths)
                outputs = set(payload.write_contract.database_output_paths)
                self.assertTrue(inputs)
                self.assertFalse(inputs & outputs)
                self.assertTrue(payload.write_contract.operation)

        for name in ("Tenant", "Client", "Engagement", "Matter"):
            self.assertEqual(
                "administrative_bootstrap",
                payloads[name].write_contract.authority,
            )
        for name in ("Communication", "ExecutionEvent", "ExecutionReceipt"):
            self.assertEqual(
                "controlled_writer",
                payloads[name].write_contract.authority,
            )

        authority_auxiliary = payloads["Authority"].write_contract.auxiliary_writes
        self.assertEqual(1, len(authority_auxiliary))
        self.assertEqual(
            "sklegal_legal.authority_identities", authority_auxiliary[0].table
        )
        self.assertEqual(
            {
                "id": payloads["Authority"].row["id"],
                "tenant_id": payloads["Authority"].row["tenant_id"],
                "matter_id": payloads["Authority"].row["matter_id"],
            },
            dict(authority_auxiliary[0].row),
        )

        event_outputs = set(
            payloads["ExecutionEvent"].write_contract.database_output_paths
        )
        self.assertTrue(
            {
                "row.id",
                "row.occurred_at",
                "row.actor_principal_id",
                "row.sequence_no",
            }.issubset(event_outputs)
        )
        receipt_outputs = set(
            payloads["ExecutionReceipt"].write_contract.database_output_paths
        )
        receipt_inputs = set(
            payloads["ExecutionReceipt"].write_contract.canonical_input_paths
        )
        self.assertTrue(
            {
                "row.artifact_content_sha256",
                "row.destination_sha256",
            }.issubset(receipt_outputs)
        )
        self.assertTrue({"row.received_at", "row.verified_at"}.issubset(receipt_inputs))
        self.assertFalse({"row.received_at", "row.verified_at"} & receipt_outputs)

        execution_inputs = set(
            payloads["Execution"].write_contract.canonical_input_paths
        )
        execution_outputs = set(
            payloads["Execution"].write_contract.database_output_paths
        )
        nested_receipt_times = {
            "relations.receipt[0].row.received_at",
            "relations.receipt[0].row.verified_at",
        }
        self.assertTrue(nested_receipt_times.issubset(execution_inputs))
        self.assertFalse(nested_receipt_times & execution_outputs)

    def test_strict_tenant_reconstruction_is_canonical(self) -> None:
        row = {
            **audited("00000000-0000-4000-8000-000000000001"),
            "status": "proposed",
            "name": "Synthetic Legal Tenant",
            "slug": "synthetic-legal",
        }
        reconstruction = reconstruct_with_metadata("Tenant", row, {})
        restored = reconstruction.entity
        self.assertIsInstance(restored, Tenant)
        self.assertEqual({"slug": "synthetic-legal"}, reconstruction.metadata.scalar)
        decomposed = decompose(restored, reconstruction.metadata)
        self.assertEqual(row, decomposed.row)
        self.assertEqual({}, decomposed.relations)
        canonical = restored.model_dump(mode="python")
        self.assertEqual(
            canonical,
            Tenant.model_validate(canonical, strict=True).model_dump(mode="python"),
        )

    def test_missing_normalized_relation_fails_closed(self) -> None:
        row = {
            **audited("00000000-0000-4000-8000-000000000004"),
            "matter_id": UUID("00000000-0000-4000-8000-000000000004"),
            "status": "proposed",
            "client_id": UUID("00000000-0000-4000-8000-000000000002"),
            "engagement_id": UUID("00000000-0000-4000-8000-000000000003"),
            "title": "Synthetic Matter",
            "summary": "Fixture-only matter.",
            "completeness": "incomplete",
            "opened_at": None,
            "closed_at": None,
        }
        with self.assertRaisesRegex(MappingContractError, "aliases"):
            reconstruct("Matter", row, {})
        restored = reconstruct("Matter", row, {"aliases": []})
        self.assertIsInstance(restored, Matter)

    def test_unknown_entity_and_missing_scalar_columns_fail_closed(self) -> None:
        with self.assertRaisesRegex(MappingContractError, "unknown domain entity"):
            reconstruct("Unknown", {}, {})
        with self.assertRaisesRegex(MappingContractError, "missing columns"):
            reconstruct("Tenant", {"id": TENANT_ID}, {})

    def test_closed_persistence_metadata_rejects_unknown_or_missing_values(
        self,
    ) -> None:
        tenant = Tenant.model_validate(
            {
                **audited("00000000-0000-4000-8000-000000000001"),
                "classification": DataClassification.CONFIDENTIAL,
                "status": TenantStatus.PROPOSED,
                "name": "Synthetic Legal Tenant",
            },
            strict=True,
        )
        with self.assertRaisesRegex(MappingContractError, "missing persistence"):
            decompose(tenant)
        with self.assertRaisesRegex(MappingContractError, "undeclared persistence"):
            decompose(
                tenant,
                PersistenceMetadata(
                    scalar={"slug": "synthetic-legal", "unknown": "rejected"}
                ),
            )
        metadata = PersistenceMetadata(scalar={"slug": "synthetic-legal"})
        with self.assertRaises(TypeError):
            metadata.scalar["slug"] = "mutated"  # type: ignore[index]

    def test_element_owner_kind_is_bidirectionally_unambiguous(self) -> None:
        shared = UUID("00000000-0000-4000-8000-000000000099")
        common = {
            **audited("00000000-0000-4000-8000-000000000009"),
            "classification": DataClassification.CONFIDENTIAL,
            "matter_id": UUID("00000000-0000-4000-8000-000000000004"),
            "completeness": RecordCompleteness.COMPLETE,
            "claim_id": shared,
            "description": "Synthetic element.",
            "evidence_item_ids": (),
            "status": ElementStatus.ALLEGED,
        }
        claim_element = Element.model_validate(
            {**common, "theory_kind": "claim"}, strict=True
        )
        defense_element = Element.model_validate(
            {**common, "theory_kind": "defense"}, strict=True
        )
        claim_write = decompose(
            claim_element, PersistenceMetadata(relations={"evidence": ()})
        )
        defense_write = decompose(
            defense_element, PersistenceMetadata(relations={"evidence": ()})
        )
        self.assertEqual("claim", claim_write.row["theory_kind"])
        self.assertEqual("defense", defense_write.row["theory_kind"])
        self.assertNotEqual(claim_write.row, defense_write.row)

    def test_claim_and_defense_relation_discriminators_bind_same_owner_id(
        self,
    ) -> None:
        contract = build_fresh_contract()
        claim = contract.entities["Claim"]
        original_defense = contract.entities["Defense"]
        defense_payload = original_defense.model_dump(mode="python")
        defense_payload["id"] = claim.id
        defense = Defense.model_validate(defense_payload, strict=True)

        claim_metadata = contract.metadata["Claim"]
        defense_metadata = contract.metadata["Defense"]
        defense_relations = {
            name: tuple(
                {
                    **dict(row),
                    **(
                        {"claim_id": claim.id}
                        if name == "elements"
                        else {"theory_id": claim.id}
                    ),
                }
                for row in rows
            )
            for name, rows in defense_metadata.relations.items()
        }
        defense_metadata = PersistenceMetadata(relations=defense_relations)

        claim_write = decompose(claim, claim_metadata)
        defense_write = decompose(defense, defense_metadata)
        self.assertEqual(claim.id, defense.id)
        self.assertEqual("claim", claim_write.relations["evidence"][0]["theory_kind"])
        self.assertEqual(
            "defense", defense_write.relations["evidence"][0]["theory_kind"]
        )
        self.assertEqual(
            claim,
            reconstruct("Claim", claim_write.row, claim_write.relations),
        )
        self.assertEqual(
            defense,
            reconstruct("Defense", defense_write.row, defense_write.relations),
        )

        for entity, metadata, relation, wrong_kind in (
            (claim, claim_metadata, "elements", "defense"),
            (claim, claim_metadata, "evidence", "defense"),
            (claim, claim_metadata, "authorities", "defense"),
            (defense, defense_metadata, "elements", "claim"),
            (defense, defense_metadata, "evidence", "claim"),
        ):
            with self.subTest(entity=type(entity).__name__, relation=relation):
                wrong = metadata_with_relation_change(
                    metadata, relation, 0, theory_kind=wrong_kind
                )
                with self.assertRaisesRegex(
                    MappingContractError, "discriminator binding"
                ):
                    decompose(entity, wrong)

        wrong_owner = UUID("00000000-0000-4000-8000-000000000094")
        for entity, metadata, relation, owner_column in (
            (claim, claim_metadata, "elements", "claim_id"),
            (claim, claim_metadata, "evidence", "theory_id"),
            (defense, defense_metadata, "elements", "claim_id"),
            (defense, defense_metadata, "evidence", "theory_id"),
        ):
            with self.subTest(
                entity=type(entity).__name__, relation=relation, owner=owner_column
            ):
                wrong = metadata_with_relation_change(
                    metadata, relation, 0, **{owner_column: wrong_owner}
                )
                with self.assertRaisesRegex(MappingContractError, "owner binding"):
                    decompose(entity, wrong)

        claim_relations = mutable_relations(claim_write.relations)
        claim_relations["evidence"][0]["theory_kind"] = "defense"
        with self.assertRaisesRegex(MappingContractError, "discriminator binding"):
            reconstruct("Claim", claim_write.row, claim_relations)

    def test_source_join_binds_scope_reference_and_presence(self) -> None:
        contract = build_fresh_contract()
        forum = contract.entities["Forum"]
        metadata = contract.metadata["Forum"]
        write = decompose(forum, metadata)
        self.assertEqual(forum, reconstruct("Forum", write.row, write.relations))

        other_tenant = UUID("00000000-0000-4000-8000-000000000099")
        wrong_scope = metadata_with_relation_change(
            metadata, "source_reference", 0, tenant_id=other_tenant
        )
        with self.assertRaisesRegex(MappingContractError, "owner binding"):
            decompose(forum, wrong_scope)

        wrong_relations = mutable_relations(write.relations)
        wrong_relations["source_reference"][0]["id"] = other_tenant
        with self.assertRaisesRegex(MappingContractError, "reference binding"):
            reconstruct("Forum", write.row, wrong_relations)

        null_reference = dict(write.row)
        null_reference["source_reference_id"] = None
        with self.assertRaisesRegex(MappingContractError, "present while.*null"):
            reconstruct("Forum", null_reference, write.relations)
        with self.assertRaisesRegex(MappingContractError, "missing while.*present"):
            reconstruct("Forum", write.row, {"source_reference": []})
        doubled = mutable_relations(write.relations)
        doubled["source_reference"].append(dict(doubled["source_reference"][0]))
        with self.assertRaisesRegex(MappingContractError, "invalid cardinality"):
            reconstruct("Forum", write.row, doubled)

    def test_alias_join_binds_canonical_owner_kind_and_scope(self) -> None:
        contract = build_fresh_contract()
        original = contract.entities["Matter"]
        alias = LegacyAlias.model_validate(
            {
                "record_kind": LegacyRecordKind.CONTAINER,
                "legacy_id": "PRB-2026-100",
                "legacy_slug": "synthetic-owner-binding",
                "legacy_path": "synthetic/prb-2026-100",
                "source_version": "v1",
                "content_sha256": "f" * 64,
                "observed_at": original.updated_at,
            },
            strict=True,
        )
        payload = original.model_dump(mode="python")
        payload["aliases"] = (alias,)
        matter = Matter.model_validate(payload, strict=True)
        alias_metadata = {
            "id": UUID("00000000-0000-4000-8000-000000000098"),
            "tenant_id": matter.tenant_id,
            "matter_id": matter.matter_id,
            "canonical_record_kind": "matter",
            "canonical_record_id": matter.matter_id,
            "canonical_matter_event_id": None,
            "import_batch_id": UUID("00000000-0000-4000-8000-000000000097"),
            "classification": "confidential",
            "completeness": "incomplete",
            "version": 1,
            "created_at": matter.created_at,
            "updated_at": matter.updated_at,
        }
        metadata = PersistenceMetadata(relations={"aliases": (alias_metadata,)})
        write = decompose(matter, metadata)
        self.assertEqual(matter, reconstruct("Matter", write.row, write.relations))

        invalid_changes = (
            {"canonical_record_id": alias_metadata["id"]},
            {"canonical_record_kind": "matter_event"},
            {"canonical_matter_event_id": alias_metadata["id"]},
            {"matter_id": alias_metadata["id"]},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                wrong = metadata_with_relation_change(metadata, "aliases", 0, **changes)
                with self.assertRaises(MappingContractError):
                    decompose(matter, wrong)

        wrong_relations = mutable_relations(write.relations)
        wrong_relations["aliases"][0]["canonical_record_id"] = alias_metadata["id"]
        with self.assertRaisesRegex(MappingContractError, "owner binding"):
            reconstruct("Matter", write.row, wrong_relations)

    def test_transaction_and_validation_relation_owners_fail_closed(self) -> None:
        contract = build_fresh_contract()
        wrong_owner = UUID("00000000-0000-4000-8000-000000000096")
        cases = (
            ("Transaction", "party_roles", "transaction_id"),
            ("Transaction", "source_references", "transaction_id"),
            ("ValidationResult", "checks", "validation_id"),
        )
        for entity_name, relation, owner_column in cases:
            with self.subTest(entity=entity_name, relation=relation):
                entity = contract.entities[entity_name]
                metadata = contract.metadata[entity_name]
                write = decompose(entity, metadata)
                self.assertEqual(
                    entity,
                    reconstruct(entity_name, write.row, write.relations),
                )
                wrong = metadata_with_relation_change(
                    metadata, relation, 0, **{owner_column: wrong_owner}
                )
                with self.assertRaisesRegex(MappingContractError, "owner binding"):
                    decompose(entity, wrong)
                wrong_relations = mutable_relations(write.relations)
                wrong_relations[relation][0][owner_column] = wrong_owner
                with self.assertRaisesRegex(MappingContractError, "owner binding"):
                    reconstruct(entity_name, write.row, wrong_relations)

    def test_execution_nested_joins_bind_scope_owner_subject_and_scalar_fks(
        self,
    ) -> None:
        contract = build_fresh_contract()
        execution = contract.entities["Execution"]
        metadata = contract.metadata["Execution"]
        write = decompose(execution, metadata)
        self.assertIsNotNone(execution.approval)
        assert execution.approval is not None
        read_relations = mutable_relations(write.relations)
        approval_snapshot_row = read_relations["approval"][0]["row"]
        assert isinstance(approval_snapshot_row, dict)
        approval_snapshot_row["captured_at"] = execution.approval.updated_at
        self.assertEqual(
            execution,
            reconstruct("Execution", write.row, read_relations),
        )
        reconstruction = reconstruct_with_metadata(
            "Execution", write.row, read_relations
        )
        self.assertEqual(
            execution.approval.version,
            reconstruction.metadata.scalar["approval_version"],
        )
        self.assertEqual(
            execution.approval.updated_at,
            reconstruction.metadata.relations["approval"][0]["captured_at"],
        )
        self.assertEqual(
            "sklegal_legal.approval_history",
            next(
                relation
                for relation in MAPPINGS["Execution"].relations
                if relation.field == "approval"
            ).read_relation,
        )
        self.assertEqual(execution.approval.version, write.row["approval_version"])
        self.assertEqual(
            execution.approval.id,
            write.relations["approval"][0]["row"]["approval_id"],
        )
        self.assertEqual(
            execution.approval.version,
            write.relations["approval"][0]["row"]["approval_version"],
        )
        self.assertIn(
            "row.approval_version",
            write.write_contract.canonical_input_paths,
        )
        round_trip_write = decompose(reconstruction.entity, reconstruction.metadata)
        self.assertEqual(
            read_relations,
            mutable_relations(round_trip_write.relations),
        )

        wrong_id = UUID("00000000-0000-4000-8000-000000000095")
        wrong_scalar = PersistenceMetadata(
            scalar={**metadata.scalar, "validation_result_id": wrong_id},
            relations=metadata.relations,
        )
        with self.assertRaisesRegex(MappingContractError, "disagrees"):
            decompose(execution, wrong_scalar)

        for relation, scalar_column in (
            ("validation_result", "validation_result_id"),
            ("approval", "approval_id"),
        ):
            with self.subTest(relation=relation, mode="wrong_id"):
                wrong_row = dict(write.row)
                wrong_row[scalar_column] = wrong_id
                with self.assertRaisesRegex(MappingContractError, "reference binding"):
                    reconstruct("Execution", wrong_row, read_relations)
            with self.subTest(relation=relation, mode="present_for_null"):
                null_row = dict(write.row)
                null_row[scalar_column] = None
                if relation == "approval":
                    null_row["approval_version"] = None
                with self.assertRaisesRegex(
                    MappingContractError, "present while.*null"
                ):
                    reconstruct("Execution", null_row, read_relations)
            with self.subTest(relation=relation, mode="missing_for_present"):
                missing = mutable_relations(read_relations)
                missing[relation] = []
                with self.assertRaisesRegex(
                    MappingContractError, "missing while.*present"
                ):
                    reconstruct("Execution", write.row, missing)
            with self.subTest(relation=relation, mode="over_cardinality"):
                doubled = mutable_relations(read_relations)
                doubled[relation].append(dict(doubled[relation][0]))
                with self.assertRaisesRegex(
                    MappingContractError, "invalid cardinality"
                ):
                    reconstruct("Execution", write.row, doubled)

        mixed_approval_reference = dict(write.row)
        mixed_approval_reference["approval_version"] = None
        with self.assertRaisesRegex(MappingContractError, "mixed nullability"):
            reconstruct("Execution", mixed_approval_reference, read_relations)

        nested_scope = mutable_relations(read_relations)
        validation_row = nested_scope["validation_result"][0]["row"]
        assert isinstance(validation_row, dict)
        validation_row["tenant_id"] = wrong_id
        with self.assertRaisesRegex(MappingContractError, "owner binding"):
            reconstruct("Execution", write.row, nested_scope)

        nested_subject = mutable_relations(read_relations)
        approval_row = nested_subject["approval"][0]["row"]
        assert isinstance(approval_row, dict)
        approval_row["subject_artifact_id"] = wrong_id
        with self.assertRaisesRegex(MappingContractError, "owner binding"):
            reconstruct("Execution", write.row, nested_subject)

        nested_event = mutable_relations(read_relations)
        event_row = nested_event["events"][0]["row"]
        assert isinstance(event_row, dict)
        event_row["execution_id"] = wrong_id
        with self.assertRaisesRegex(MappingContractError, "owner binding"):
            reconstruct("Execution", write.row, nested_event)

    def test_execution_uses_immutable_approved_snapshot_after_revocation(
        self,
    ) -> None:
        contract = build_fresh_contract()
        execution = contract.entities["Execution"]
        self.assertIsNotNone(execution.approval)
        assert execution.approval is not None
        approved_snapshot = execution.approval
        write = decompose(execution, contract.metadata["Execution"])
        immutable_relations = mutable_relations(write.relations)
        immutable_row = immutable_relations["approval"][0]["row"]
        assert isinstance(immutable_row, dict)
        immutable_row["captured_at"] = approved_snapshot.updated_at

        current_approval = approved_snapshot.transition_to(
            ApprovalStatus.REVOKED,
            at=execution.updated_at,
            revoker_principal_id=contract.principal_id,
            revocation_rationale="Synthetic later revocation.",
            revoked_at=execution.updated_at,
        )
        self.assertEqual(ApprovalStatus.REVOKED, current_approval.status)
        self.assertGreater(current_approval.version, approved_snapshot.version)

        restored = reconstruct("Execution", write.row, immutable_relations)
        self.assertIsNotNone(restored.approval)
        assert restored.approval is not None
        self.assertEqual(ApprovalStatus.APPROVED, restored.approval.status)
        self.assertEqual(approved_snapshot.version, restored.approval.version)
        self.assertNotEqual(current_approval, restored.approval)

        mutable_current_row = dict(decompose(current_approval).row)
        mutable_current_row["approval_id"] = mutable_current_row.pop("id")
        mutable_current_row["approval_version"] = mutable_current_row.pop("version")
        for column in (
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        ):
            mutable_current_row.pop(column)
        mutable_current_row["captured_at"] = current_approval.updated_at
        current_relations = mutable_relations(immutable_relations)
        current_relations["approval"][0]["row"] = mutable_current_row
        with self.assertRaisesRegex(MappingContractError, "approval_version"):
            reconstruct("Execution", write.row, current_relations)


if __name__ == "__main__":
    unittest.main()
