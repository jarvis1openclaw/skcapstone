from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sklegal_policies import (
    ConflictDisposition,
    ConflictService,
    KnownPartyAssociation,
    PartyCandidate,
    PartyRelationship,
    WaiverReference,
    normalize_party_name,
)

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("a3000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("a3000000-0000-4000-8000-000000000002")


class PartyConflictTests(unittest.TestCase):
    def test_normalization_is_deterministic_unicode_aware_and_conservative(
        self,
    ) -> None:
        self.assertEqual("acme", normalize_party_name(" A.C.M.E., Inc. ", "company"))
        self.assertEqual("acme", normalize_party_name("ＡＣＭＥ LLC", "company"))
        self.assertEqual(
            "jose nunez", normalize_party_name("Jose\u0301 Nun\u0303ez", "person")
        )
        self.assertNotEqual(
            normalize_party_name("Synthetic Alpha", "company"),
            normalize_party_name("Synthetic Alfa", "company"),
        )

    def test_adverse_party_collision_creates_a_hold_result_without_deciding_waiver(
        self,
    ) -> None:
        service = ConflictService()
        candidate = PartyCandidate(
            party_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            display_name="ACME, Incorporated",
            party_kind="company",
            proposed_relationship=PartyRelationship.CLIENT,
        )
        known = KnownPartyAssociation(
            association_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=uuid4(),
            party_id=uuid4(),
            display_name="A.C.M.E. Inc.",
            party_kind="company",
            relationship=PartyRelationship.ADVERSE_PARTY,
            active=True,
        )
        result = service.check(
            check_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            candidates=(candidate,),
            known_associations=(known,),
            checked_at=T0,
        )
        self.assertEqual(ConflictDisposition.HOLD, result.recommended_disposition)
        self.assertEqual(1, len(result.matches))
        self.assertEqual(candidate.party_id, result.matches[0].candidate_party_id)
        self.assertEqual(known.association_id, result.matches[0].association_id)
        self.assertNotIn(candidate.display_name, result.model_dump_json())
        self.assertNotIn(known.display_name, result.model_dump_json())

    def test_profile_owner_hint_cannot_become_a_party_or_membership(self) -> None:
        with self.assertRaises(ValidationError):
            PartyCandidate.model_validate(
                {
                    "party_id": uuid4(),
                    "tenant_id": TENANT_ID,
                    "matter_id": MATTER_ID,
                    "display_name": "Synthetic Candidate",
                    "party_kind": "company",
                    "proposed_relationship": "client",
                    "profile_owner_principal_id": uuid4(),
                }
            )

    def test_waiver_is_exact_versioned_scoped_and_time_bounded(self) -> None:
        waiver = WaiverReference(
            waiver_id=uuid4(),
            artifact_id=uuid4(),
            artifact_version=3,
            content_sha256="c" * 64,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            valid_from=T0,
            valid_to=T0 + timedelta(days=1),
        )
        self.assertTrue(waiver.is_effective(T0))
        self.assertFalse(waiver.is_effective(T0 + timedelta(days=1)))
        with self.assertRaises(ValidationError):
            WaiverReference.model_validate(
                {**waiver.model_dump(mode="python"), "artifact_version": 0}
            )


if __name__ == "__main__":
    unittest.main()
