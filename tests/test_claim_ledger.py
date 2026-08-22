from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    ClaimSupport,
    ClaimSupportKind,
    DomainTransitionError,
    IdentityMutationError,
    LedgerClaim,
    LedgerClaimStatus,
    SourceReference,
)
from sklegal_persistence import (
    MAPPINGS,
    MappingContractError,
    PersistenceMetadata,
    decompose,
    reconstruct,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT_ID = UUID("20000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("10000000-0000-4000-8000-000000000301")
OTHER_MATTER_ID = UUID("10000000-0000-4000-8000-000000000302")
PRINCIPAL_ID = UUID("10000000-0000-4000-8000-000000000011")
CLAIM_ID = UUID("10000000-0000-4000-8000-000000000401")
SUPPORT_ID = UUID("10000000-0000-4000-8000-000000000402")
COUNTER_ID = UUID("10000000-0000-4000-8000-000000000403")
POLICY_REVISION = "a" * 64
EXCERPT_DIGEST = "b" * 64


def source(reference_id: UUID) -> SourceReference:
    return SourceReference(
        source_reference_id=reference_id,
        source_system="synthetic",
        source_version="v1",
        content_sha256="c" * 64,
        locator="synthetic:claim-ledger",
        observed_at=T0,
    )


def support_record(
    identifier: UUID = SUPPORT_ID,
    *,
    claim_id: UUID = CLAIM_ID,
    kind: ClaimSupportKind = ClaimSupportKind.SUPPORT,
    tenant_id: UUID = TENANT_ID,
    matter_id: UUID = MATTER_ID,
    created_at: datetime = T0,
    span_start: int = 0,
    span_end: int = 12,
) -> ClaimSupport:
    return ClaimSupport(
        id=identifier,
        tenant_id=tenant_id,
        matter_id=matter_id,
        claim_id=claim_id,
        kind=kind,
        source_reference=source(UUID("10000000-0000-4000-8000-000000000501")),
        span_start=span_start,
        span_end=span_end,
        excerpt_sha256=EXCERPT_DIGEST,
        recorded_by_principal_id=PRINCIPAL_ID,
        policy_revision=POLICY_REVISION,
        created_at=created_at,
        updated_at=created_at,
    )


def ledger_claim(**overrides: object) -> LedgerClaim:
    payload: dict[str, object] = {
        "id": CLAIM_ID,
        "tenant_id": TENANT_ID,
        "matter_id": MATTER_ID,
        "statement": "Synthetic ledger claim statement.",
        "policy_revision": POLICY_REVISION,
        "support": (support_record(),),
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    return LedgerClaim.model_validate(payload)


def claim_metadata() -> PersistenceMetadata:
    source_row = {
        "tenant_id": TENANT_ID,
        "matter_id": MATTER_ID,
        "classification": "confidential",
        "completeness": "incomplete",
        "version": 1,
        "created_at": T0,
        "updated_at": T0,
    }
    return PersistenceMetadata(
        scalar={"system_from": T0, "system_to": None},
        relations={
            "support": (
                {
                    "nested_metadata": PersistenceMetadata(
                        relations={"source_reference": (source_row,)}
                    )
                },
            )
        },
    )


class ClaimSupportTests(unittest.TestCase):
    def test_support_span_must_be_nonempty(self) -> None:
        with self.assertRaises(ValidationError):
            support_record(span_start=12, span_end=12)

    def test_support_fields_are_immutable(self) -> None:
        record = support_record()
        with self.assertRaises(IdentityMutationError):
            record.evolve(at=T1, span_end=13)
        with self.assertRaises(IdentityMutationError):
            record.evolve(at=T1, kind=ClaimSupportKind.COUNTER_SUPPORT)


class LedgerClaimWriteTests(unittest.TestCase):
    def test_unsupported_claim_is_rejected_at_write(self) -> None:
        with self.assertRaisesRegex(ValidationError, "at least one support record"):
            ledger_claim(support=())

    def test_counter_support_alone_is_rejected_at_write(self) -> None:
        with self.assertRaisesRegex(
            ValidationError, "at least one supporting source span"
        ):
            ledger_claim(
                support=(support_record(kind=ClaimSupportKind.COUNTER_SUPPORT),)
            )

    def test_every_claim_row_traces_to_source_spans_and_policy_revision(self) -> None:
        claim = ledger_claim()
        payload = decompose(claim, claim_metadata())
        self.assertEqual("sklegal_legal.ledger_claims", payload.table)
        self.assertEqual(POLICY_REVISION, payload.row["policy_revision"])
        support_row = payload.relations["support"][0]["row"]
        self.assertEqual(CLAIM_ID, support_row["claim_id"])
        self.assertEqual("support", support_row["kind"])
        self.assertEqual(0, support_row["span_start"])
        self.assertEqual(12, support_row["span_end"])
        self.assertEqual(EXCERPT_DIGEST, support_row["excerpt_sha256"])
        self.assertIn("source_reference", payload.relations["support"][0]["relations"])
        source_row = payload.relations["support"][0]["relations"]["source_reference"][0]
        self.assertEqual("c" * 64, source_row["content_sha256"])
        identity_write = payload.write_contract.auxiliary_writes[0]
        self.assertEqual("sklegal_legal.ledger_claim_identities", identity_write.table)

    def test_cross_tenant_support_scope_is_denied(self) -> None:
        with self.assertRaisesRegex(ValidationError, "crosses tenant, matter"):
            ledger_claim(support=(support_record(tenant_id=OTHER_TENANT_ID),))
        with self.assertRaisesRegex(ValidationError, "crosses tenant, matter"):
            ledger_claim(support=(support_record(matter_id=OTHER_MATTER_ID),))
        with self.assertRaisesRegex(ValidationError, "crosses tenant, matter"):
            ledger_claim(
                support=(
                    support_record(
                        claim_id=UUID("10000000-0000-4000-8000-000000000499")
                    ),
                )
            )

    def test_cross_tenant_reconstruction_is_denied(self) -> None:
        claim = ledger_claim()
        payload = decompose(claim, claim_metadata())
        tampered = dict(payload.relations["support"][0])
        tampered_row = dict(tampered["row"])
        tampered_row["tenant_id"] = OTHER_TENANT_ID
        tampered["row"] = tampered_row
        with self.assertRaisesRegex(MappingContractError, "owner binding"):
            reconstruct("LedgerClaim", dict(payload.row), {"support": (tampered,)})

    def test_decompose_rejects_undeclared_support_metadata(self) -> None:
        claim = ledger_claim()
        with self.assertRaisesRegex(MappingContractError, "nested snapshot metadata"):
            decompose(
                claim,
                PersistenceMetadata(
                    scalar={"system_from": T0},
                    relations={"support": ({},)},
                ),
            )


class LedgerClaimRevisionTests(unittest.TestCase):
    def test_revision_bumps_version_and_preserves_history(self) -> None:
        claim = ledger_claim()
        revised = claim.evolve(at=T1, statement="Synthetic revised statement.")
        self.assertEqual(2, revised.version)
        self.assertEqual(T0, revised.created_at)
        self.assertEqual(T1, revised.updated_at)
        self.assertEqual("Synthetic ledger claim statement.", claim.statement)
        self.assertEqual("Synthetic revised statement.", revised.statement)
        self.assertEqual(claim.support, revised.support)
        self.assertEqual(POLICY_REVISION, revised.policy_revision)

    def test_policy_revision_is_immutable(self) -> None:
        claim = ledger_claim()
        with self.assertRaises(IdentityMutationError):
            claim.evolve(at=T1, policy_revision="d" * 64)

    def test_support_history_is_append_only(self) -> None:
        claim = ledger_claim()
        with self.assertRaisesRegex(DomainTransitionError, "append-only"):
            claim.evolve(at=T1, support=())
        with self.assertRaisesRegex(DomainTransitionError, "append-only"):
            claim.evolve(at=T1, support=claim.support)
        counter = support_record(
            COUNTER_ID, kind=ClaimSupportKind.COUNTER_SUPPORT, created_at=T1
        )
        appended = claim.record_support(counter, at=T1)
        self.assertEqual(2, len(appended.support))
        self.assertEqual(ClaimSupportKind.COUNTER_SUPPORT, appended.support[1].kind)
        with self.assertRaisesRegex(DomainTransitionError, "append-only"):
            appended.evolve(at=T2, support=(counter, appended.support[0]))

    def test_state_machine_edges(self) -> None:
        claim = ledger_claim()
        under_review = claim.transition_to(LedgerClaimStatus.UNDER_REVIEW, at=T1)
        supported = under_review.transition_to(LedgerClaimStatus.SUPPORTED, at=T2)
        challenged = supported.transition_to(LedgerClaimStatus.CHALLENGED, at=T3)
        self.assertEqual(4, challenged.version)
        with self.assertRaises(DomainTransitionError):
            claim.transition_to(LedgerClaimStatus.SUPPORTED, at=T1)
        withdrawn = challenged.transition_to(LedgerClaimStatus.WITHDRAWN, at=T3)
        with self.assertRaises(DomainTransitionError):
            withdrawn.transition_to(LedgerClaimStatus.UNDER_REVIEW, at=T3)

    def test_transition_can_append_support(self) -> None:
        claim = ledger_claim()
        counter = support_record(
            COUNTER_ID, kind=ClaimSupportKind.COUNTER_SUPPORT, created_at=T1
        )
        challenged = claim.transition_to(
            LedgerClaimStatus.UNDER_REVIEW, at=T1
        ).transition_to(
            LedgerClaimStatus.CHALLENGED,
            at=T2,
            support=(*claim.support, counter),
        )
        self.assertEqual(2, len(challenged.support))


class ClaimLedgerMappingTests(unittest.TestCase):
    def test_mapping_covers_every_model_field(self) -> None:
        claim_fields = set(LedgerClaim.model_fields)
        support_fields = set(ClaimSupport.model_fields)
        claim_mapping = MAPPINGS["LedgerClaim"]
        support_mapping = MAPPINGS["ClaimSupport"]
        covered_claim = {field for field, _ in claim_mapping.direct}
        covered_claim.update(relation.field for relation in claim_mapping.relations)
        covered_support = {field for field, _ in support_mapping.direct}
        covered_support.update(relation.field for relation in support_mapping.relations)
        self.assertEqual(claim_fields, covered_claim)
        self.assertEqual(support_fields, covered_support)
        self.assertEqual(
            "sklegal_legal.ledger_claim_current", claim_mapping.read_relation
        )
        self.assertEqual(
            "sklegal_legal.ledger_claim_history", claim_mapping.history_relation
        )

    def test_revision_history_round_trip_through_mapping(self) -> None:
        claim = ledger_claim()
        metadata = claim_metadata()
        payload = decompose(claim, metadata)
        restored = reconstruct("LedgerClaim", dict(payload.row), payload.relations)
        self.assertEqual(claim, restored)
        revised = claim.evolve(at=T1, statement="Synthetic revised statement.")
        revised_metadata = PersistenceMetadata(
            scalar={"system_from": T1, "system_to": None},
            relations=metadata.relations,
        )
        revised_payload = decompose(revised, revised_metadata)
        self.assertEqual(2, revised_payload.row["version"])
        restored_revised = reconstruct(
            "LedgerClaim", dict(revised_payload.row), revised_payload.relations
        )
        self.assertEqual(revised, restored_revised)
        self.assertNotEqual(restored.statement, restored_revised.statement)
        self.assertEqual(restored.support, restored_revised.support)


if __name__ == "__main__":
    unittest.main()
