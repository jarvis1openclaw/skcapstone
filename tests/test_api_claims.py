"""HTTP boundary tests for the claim ledger read API (SKL-S4-03B).

Covers the ledger wire shape (support and counter-support panels,
applicability factors, claim-state transitions, reviewer challenges, and
the claim gate), the preserved mismatch defect, the no-answer state,
capability and membership enforcement, and fail-closed behavior. All
fixtures are synthetic: no real matter content, no real citation, no
legacy identifiers, and no HammerTime paths.
"""

from __future__ import annotations

import unittest
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sklegal_api.claims import (
    ApplicabilityCheckRead,
    ChallengeDefectRead,
    ChallengeRead,
    ClaimLedgerEntryRead,
    ClaimLedgerRead,
    ClaimReviewRecordRead,
    ClaimStateTransitionRead,
    ClaimSupportRecordRead,
    GateCheckRead,
    GateEvaluationRead,
    InMemoryClaimLedgerStore,
    build_claim_ledger_router,
)
from sklegal_capauth import (
    BoundaryScope,
    Capability,
    PrincipalContext,
    Purpose,
)

from tests.support.capauth_contract import (
    TENANT_ID,
    CapabilityTestRig,
    raw_leaf,
)

OTHER_TENANT_ID = UUID("30000000-0000-4000-8000-000000000009")
MATTER_ID = UUID("20000000-0000-4000-8000-0000000000a1")
SECOND_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000a2")
UNRECORDED_MATTER_ID = UUID("20000000-0000-4000-8000-0000000000c1")

PRINCIPAL_ID = "30000000-0000-4000-8000-0000000000p1"
CLAIM_ID = "40000000-0000-4000-8000-0000000000c1"
SECOND_CLAIM_ID = "40000000-0000-4000-8000-0000000000c2"

SOURCE_SHA = "b" * 64
EXCERPT_SHA = "d" * 64
POLICY_REVISION = "e" * 64


def support_record(
    record_id: str,
    kind: str,
    *,
    locator: str = "fixture/synthetic-source-1",
) -> ClaimSupportRecordRead:
    return ClaimSupportRecordRead(
        support_id=record_id,
        kind=kind,
        recorded_at="2026-08-20T10:00:00+00:00",
        source_system="synthetic-corpus",
        source_version="1",
        source_locator=locator,
        content_sha256=SOURCE_SHA,
        span_start=0,
        span_end=12,
        excerpt_sha256=EXCERPT_SHA,
        note=None,
        recorded_by_principal_id=PRINCIPAL_ID,
        policy_revision=POLICY_REVISION,
    )


def mismatch_defect_challenge() -> ChallengeRead:
    return ChallengeRead(
        challenge_id="synthetic-challenge-1",
        issued_at="2026-08-20T11:00:00+00:00",
        independence="independent",
        outcome="defect_found",
        saw_challenged_conclusion=False,
        challenger_provider="synthetic",
        challenger_model_name="challenger-model",
        challenger_model_revision="r1",
        defects=(
            ChallengeDefectRead(
                defect_kind="quotation_error",
                description=(
                    "The recorded quotation does not match the pinned source "
                    "span bytes."
                ),
            ),
        ),
    )


def failed_ledger_entry() -> ClaimLedgerEntryRead:
    """One claim with contrary support, a mismatch defect, and a failed gate."""

    return ClaimLedgerEntryRead(
        claim_id=CLAIM_ID,
        statement="The synthetic vehicle qualifies under the synthetic statute.",
        status="challenged",
        version=3,
        policy_revision=POLICY_REVISION,
        updated_at="2026-08-20T11:30:00+00:00",
        support=(support_record("synthetic-support-1", "support"),),
        counter_support=(
            support_record(
                "synthetic-counter-1",
                "counter_support",
                locator="fixture/synthetic-source-2",
            ),
        ),
        support_verification_state="failed",
        applicability=(
            ApplicabilityCheckRead(
                check_id="authority.quotation",
                subject_id="synthetic-citation-1",
                outcome="failed",
                reasons=("quotation_mismatch",),
            ),
            ApplicabilityCheckRead(
                check_id="contrary.leads_reviewed",
                subject_id=None,
                outcome="failed",
                reasons=("contrary_leads_require_review",),
            ),
        ),
        challenges=(mismatch_defect_challenge(),),
        review_history=(
            ClaimReviewRecordRead(
                review_id="synthetic-review-1",
                reviewed_at="2026-08-20T11:30:00+00:00",
                reviewer_principal_id=PRINCIPAL_ID,
                claim_version=3,
                decision="challenge_recorded",
                note="Quotation mismatch requires correction and renewed review.",
                policy_revision=POLICY_REVISION,
            ),
        ),
        gate=GateEvaluationRead(
            gate="claim_ready",
            evaluated_at="2026-08-20T11:45:00+00:00",
            outcome="failed",
            failed_checks=(
                GateCheckRead(
                    check_id="challenge.no_defect",
                    subject_id=CLAIM_ID,
                    reasons=("challenge_defect_unresolved",),
                ),
            ),
        ),
        state_transitions=(
            ClaimStateTransitionRead(
                from_status=None,
                to_status="proposed",
                at="2026-08-20T09:00:00+00:00",
                version=1,
            ),
            ClaimStateTransitionRead(
                from_status="proposed",
                to_status="under_review",
                at="2026-08-20T10:30:00+00:00",
                version=2,
            ),
            ClaimStateTransitionRead(
                from_status="under_review",
                to_status="challenged",
                at="2026-08-20T11:30:00+00:00",
                version=3,
            ),
        ),
    )


def ready_ledger_entry() -> ClaimLedgerEntryRead:
    return ClaimLedgerEntryRead(
        claim_id=SECOND_CLAIM_ID,
        statement="Synthetic notice was given within the statutory window.",
        status="supported",
        version=2,
        policy_revision=POLICY_REVISION,
        updated_at="2026-08-20T12:00:00+00:00",
        support=(support_record("synthetic-support-2", "support"),),
        counter_support=(),
        support_verification_state="passed",
        applicability=(
            ApplicabilityCheckRead(
                check_id="authority.jurisdiction",
                subject_id="synthetic-citation-2",
                outcome="passed",
                reasons=(),
            ),
        ),
        challenges=(
            ChallengeRead(
                challenge_id="synthetic-challenge-2",
                issued_at="2026-08-20T12:10:00+00:00",
                independence="independent",
                outcome="no_defect",
                saw_challenged_conclusion=False,
                challenger_provider="synthetic",
                challenger_model_name="challenger-model",
                challenger_model_revision="r1",
                defects=(),
            ),
        ),
        review_history=(
            ClaimReviewRecordRead(
                review_id="synthetic-review-2",
                reviewed_at="2026-08-20T12:15:00+00:00",
                reviewer_principal_id=PRINCIPAL_ID,
                claim_version=2,
                decision="accepted",
                note="Support and independent challenge checks passed.",
                policy_revision=POLICY_REVISION,
            ),
        ),
        gate=GateEvaluationRead(
            gate="claim_ready",
            evaluated_at="2026-08-20T12:15:00+00:00",
            outcome="passed",
            failed_checks=(),
        ),
        state_transitions=(
            ClaimStateTransitionRead(
                from_status=None,
                to_status="proposed",
                at="2026-08-20T09:30:00+00:00",
                version=1,
            ),
            ClaimStateTransitionRead(
                from_status="proposed",
                to_status="supported",
                at="2026-08-20T12:00:00+00:00",
                version=2,
            ),
        ),
    )


def ledger(
    entries: tuple[ClaimLedgerEntryRead, ...],
) -> ClaimLedgerRead:
    return ClaimLedgerRead(matter_id=MATTER_ID, claims=entries)


class ClaimLedgerApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.store = InMemoryClaimLedgerStore()
        self.store.add_ledger(
            TENANT_ID, MATTER_ID, ledger((failed_ledger_entry(), ready_ledger_entry()))
        )
        self.store.set_matter_members(
            TENANT_ID, MATTER_ID, frozenset({self.principal.principal_id})
        )
        self.store.set_matter_members(
            TENANT_ID, UNRECORDED_MATTER_ID, frozenset({self.principal.principal_id})
        )

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter = request.path_params.get("matter_id")
            matter_uuid = UUID(str(matter)) if matter is not None else None
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=matter_uuid,
                resource_id=str(matter_uuid) if matter_uuid is not None else None,
            )

        app = FastAPI()
        app.include_router(
            build_claim_ledger_router(
                store=self.store,
                authorizer=self.rig.authorizer,
                principal_resolver=principal_resolver,
                scope_resolver=scope_resolver,
            )
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.rig.close()

    def ledger_token(self, matter_id: UUID = MATTER_ID) -> str:
        grant = self.rig.grant(
            capability=Capability.CLAIM_REVIEW,
            purpose=Purpose.CLAIM_REVIEW,
            target="api:claims.ledger",
            matter_id=matter_id,
            resource_id=str(matter_id),
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def get_ledger(self, matter_id: UUID = MATTER_ID, token: str | None = None):
        return self.client.get(
            f"/v1/matters/{matter_id}/claims",
            headers={
                "Authorization": f"Bearer {token or self.ledger_token(matter_id)}"
            },
        )

    def test_ledger_returns_support_and_counter_support_panels(self) -> None:
        response = self.get_ledger()
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(str(MATTER_ID), body["matterId"])
        self.assertEqual(2, len(body["claims"]))
        entry = body["claims"][0]
        self.assertEqual(CLAIM_ID, entry["claimId"])
        self.assertEqual("challenged", entry["status"])
        # Support and counter-support stay separate panels on the wire.
        self.assertEqual(
            ["synthetic-support-1"], [r["supportId"] for r in entry["support"]]
        )
        self.assertEqual(
            ["synthetic-counter-1"], [r["supportId"] for r in entry["counterSupport"]]
        )
        self.assertEqual("support", entry["support"][0]["kind"])
        self.assertEqual("counter_support", entry["counterSupport"][0]["kind"])
        # Every span keeps its exact locator and hashes for tracing.
        self.assertEqual(
            "fixture/synthetic-source-2", entry["counterSupport"][0]["sourceLocator"]
        )
        self.assertEqual(SOURCE_SHA, entry["counterSupport"][0]["contentSha256"])
        self.assertEqual(EXCERPT_SHA, entry["counterSupport"][0]["excerptSha256"])

    def test_ledger_preserves_the_mismatch_defect_verbatim(self) -> None:
        response = self.get_ledger()
        entry = response.json()["claims"][0]
        challenge = entry["challenges"][0]
        self.assertEqual("defect_found", challenge["outcome"])
        self.assertEqual("independent", challenge["independence"])
        self.assertFalse(challenge["sawChallengedConclusion"])
        defect = challenge["defects"][0]
        self.assertEqual("quotation_error", defect["defectKind"])
        self.assertEqual(
            "The recorded quotation does not match the pinned source span bytes.",
            defect["description"],
        )
        # A defect-found challenge fails the gate and the reason is carried.
        self.assertEqual("failed", entry["gate"]["outcome"])
        self.assertEqual(
            ["challenge_defect_unresolved"],
            entry["gate"]["failedChecks"][0]["reasons"],
        )

    def test_ledger_reports_applicability_factors_with_reasons(self) -> None:
        response = self.get_ledger()
        entry = response.json()["claims"][0]
        factors = {factor["checkId"]: factor for factor in entry["applicability"]}
        self.assertEqual("failed", factors["authority.quotation"]["outcome"])
        self.assertEqual(
            ["quotation_mismatch"], factors["authority.quotation"]["reasons"]
        )
        self.assertEqual(
            ["contrary_leads_require_review"],
            factors["contrary.leads_reviewed"]["reasons"],
        )

    def test_ledger_carries_claim_state_transitions(self) -> None:
        response = self.get_ledger()
        entry = response.json()["claims"][0]
        transitions = entry["stateTransitions"]
        self.assertEqual(
            [
                (None, "proposed", 1),
                ("proposed", "under_review", 2),
                ("under_review", "challenged", 3),
            ],
            [(t["fromStatus"], t["toStatus"], t["version"]) for t in transitions],
        )

    def test_ledger_carries_human_reviewer_history_separately(self) -> None:
        response = self.get_ledger()
        entry = response.json()["claims"][0]
        review = entry["reviewHistory"][0]
        self.assertEqual("synthetic-review-1", review["reviewId"])
        self.assertEqual(PRINCIPAL_ID, review["reviewerPrincipalId"])
        self.assertEqual(3, review["claimVersion"])
        self.assertEqual("challenge_recorded", review["decision"])
        self.assertIn("Quotation mismatch", review["note"])

    def test_ready_claim_reports_passing_gate_without_failed_checks(self) -> None:
        response = self.get_ledger()
        entry = response.json()["claims"][1]
        self.assertEqual("supported", entry["status"])
        self.assertEqual("passed", entry["supportVerificationState"])
        self.assertEqual("passed", entry["gate"]["outcome"])
        self.assertEqual([], entry["gate"]["failedChecks"])
        self.assertEqual([], entry["challenges"][0]["defects"])

    def test_matter_with_no_claims_is_a_recorded_no_answer_state(self) -> None:
        empty_matter = UUID("20000000-0000-4000-8000-0000000000b1")
        # A recorded ledger with zero claims is the no-answer state, not 404.
        self.store.add_ledger(
            TENANT_ID,
            empty_matter,
            ClaimLedgerRead(matter_id=empty_matter),
        )
        self.store.set_matter_members(
            TENANT_ID, empty_matter, frozenset({self.principal.principal_id})
        )
        response = self.get_ledger(empty_matter)
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual([], body["claims"])

    def test_missing_credential_is_denied(self) -> None:
        response = self.client.get(f"/v1/matters/{MATTER_ID}/claims")
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_propose_capability_cannot_drive_the_ledger_read(self) -> None:
        grant = self.rig.grant(
            capability=Capability.CLAIM_PROPOSE,
            purpose=Purpose.CLAIM_DEVELOPMENT,
            target="api:claims.ledger",
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        token = raw_leaf(self.rig.issue(self.principal, grant))
        response = self.get_ledger(token=token)
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_capability_scoped_to_another_matter_is_denied(self) -> None:
        # Token pinned to the second matter cannot read this matter's ledger.
        response = self.get_ledger(token=self.ledger_token(SECOND_MATTER_ID))
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_non_member_with_valid_capability_is_denied(self) -> None:
        # The second matter has no recorded roster for this principal.
        token = self.ledger_token(SECOND_MATTER_ID)
        response = self.get_ledger(matter_id=SECOND_MATTER_ID, token=token)
        self.assertEqual(403, response.status_code)
        self.assertEqual("matter_membership_denied", response.json()["detail"]["code"])

    def test_unknown_matter_is_not_found_for_members(self) -> None:
        response = self.get_ledger(matter_id=UNRECORDED_MATTER_ID)
        self.assertEqual(404, response.status_code)
        self.assertEqual("not_found", response.json()["detail"]["code"])

    def test_token_for_another_tenant_is_denied(self) -> None:
        other_principal = self.rig.principal(tenant_id=OTHER_TENANT_ID)
        grant = self.rig.grant(
            capability=Capability.CLAIM_REVIEW,
            purpose=Purpose.CLAIM_REVIEW,
            target="api:claims.ledger",
            tenant_id=OTHER_TENANT_ID,
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        token = raw_leaf(self.rig.issue(other_principal, grant))
        response = self.get_ledger(token=token)
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])

    def test_store_outage_fails_closed_with_503(self) -> None:
        self.store.available = False
        response = self.get_ledger()
        self.assertEqual(503, response.status_code)
        self.assertEqual("claim_ledger_unavailable", response.json()["detail"]["code"])

    def test_support_panel_rejects_a_counter_support_record(self) -> None:
        with self.assertRaises(ValidationError):
            ClaimLedgerEntryRead(
                claim_id=CLAIM_ID,
                statement="synthetic statement",
                status="proposed",
                version=1,
                policy_revision=POLICY_REVISION,
                updated_at="2026-08-20T09:00:00+00:00",
                support=(support_record("synthetic-counter-1", "counter_support"),),
                support_verification_state="missing",
            )


if __name__ == "__main__":
    unittest.main()
