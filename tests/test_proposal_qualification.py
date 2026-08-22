"""SKL-S5-02B challenge, human decision, and replay qualification tests."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sklegal_domain import (
    AuthoritySupportVerification,
    BlindChallengeRecord,
    ChallengeDefect,
    ChallengeDefectKind,
    ChallengeOutcome,
    ClaimSupport,
    ClaimSupportKind,
    LedgerClaim,
    LedgerClaimStatus,
    ModelRunIdentity,
    SourceReference,
    ValidationOutcome,
)
from sklegal_worker.errors import PolicyDeniedError, WorkflowInvariantError
from sklegal_worker.models import SourcePin
from sklegal_worker.proposal_qualification import (
    BlindChallengeRequest,
    HumanClaimDecision,
    HumanDecisionReceipt,
    InMemoryQualificationReplayLedger,
    PolicyCheckpoint,
    ProposalQualificationCoordinator,
    QualificationAction,
    ReplayStage,
    ReviewPresentationReceipt,
    _digest,
)
from sklegal_worker.proposal_run import ProposalRunRecord

T0 = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)
T4 = T0 + timedelta(minutes=4)

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("10000000-0000-4000-8000-000000000301")
CLAIM_ID = UUID("10000000-0000-4000-8000-000000000401")
SUPPORT_ID = UUID("10000000-0000-4000-8000-000000000402")
SOURCE_ID = UUID("10000000-0000-4000-8000-000000000403")
REVIEW_ID = UUID("10000000-0000-4000-8000-000000000404")
DECISION_ID = UUID("10000000-0000-4000-8000-000000000405")
REVIEWER_ID = UUID("10000000-0000-4000-8000-000000000406")
AUTH_1 = UUID("10000000-0000-4000-8000-000000000407")
AUTH_2 = UUID("10000000-0000-4000-8000-000000000408")
IDEMPOTENCY_KEY = UUID("10000000-0000-4000-8000-000000000409")
POLICY_REVISION = "a" * 64
SOURCE_SHA256 = "b" * 64
RECEIPT_SHA256 = "c" * 64


def _claim() -> LedgerClaim:
    reference = SourceReference(
        source_reference_id=SOURCE_ID,
        source_system="synthetic",
        source_version="v1",
        content_sha256=SOURCE_SHA256,
        locator="synthetic:qualification",
        observed_at=T0,
    )
    support = ClaimSupport(
        id=SUPPORT_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        claim_id=CLAIM_ID,
        kind=ClaimSupportKind.SUPPORT,
        source_reference=reference,
        span_start=0,
        span_end=10,
        excerpt_sha256="d" * 64,
        recorded_by_principal_id=REVIEWER_ID,
        policy_revision=POLICY_REVISION,
        created_at=T0,
        updated_at=T0,
    )
    claim = LedgerClaim(
        id=CLAIM_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        statement="Synthetic claim for governed qualification.",
        policy_revision=POLICY_REVISION,
        support=(support,),
        created_at=T0,
        updated_at=T0,
    )
    claim = claim.transition_to(LedgerClaimStatus.UNDER_REVIEW, at=T1)
    return claim.transition_to(LedgerClaimStatus.SUPPORTED, at=T2)


def _verification() -> AuthoritySupportVerification:
    return AuthoritySupportVerification(
        claim_id=CLAIM_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        evaluated_at=T3,
        checks=(),
        outcome=ValidationOutcome.PASSED,
    )


def _proposal() -> ProposalRunRecord:
    return ProposalRunRecord(
        run_key="pilot-run-02b",
        proposal_id="proposal-02b",
        gateway_request_id="pilot-run-02b",
        route_id="qwen.corpus-summary.v1",
        provider="qwen_local",
        model_name="qwen3",
        model_revision="v1",
        prompt_template_id="prompt-v1",
        prompt_template_sha256="1" * 64,
        output_schema_id="schema-v1",
        output_schema_sha256="2" * 64,
        registry_revision="registry-v1",
        payload_sha256="3" * 64,
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
        egress_rule="local-only",
        policy_revision=POLICY_REVISION,
        classification="protected",
        retrieval_request_sha256="4" * 64,
        release_id="release-v1",
        projection_generation=1,
        retrieval_adapter_version="adapter-v1",
        context_sources=(
            SourcePin(source_id="pilot-source", source_sha256=SOURCE_SHA256),
        ),
        context_hit_count=1,
        validated_at=T3.isoformat(),
    )


class _Policy:
    def __init__(self, *, revoke_on_decision: bool = False) -> None:
        self.revoke_on_decision = revoke_on_decision
        self.calls: list[QualificationAction] = []

    def authorize(
        self,
        *,
        run_key: str,
        tenant_id: UUID,
        matter_id: UUID,
        action: QualificationAction,
        at: datetime,
    ) -> PolicyCheckpoint:
        del run_key, tenant_id, matter_id
        self.calls.append(action)
        if (
            self.revoke_on_decision
            and action is QualificationAction.RECORD_HUMAN_DECISION
        ):
            raise PolicyDeniedError("capability revoked during qualification")
        return PolicyCheckpoint(
            action=action,
            authorization_decision_id=(
                AUTH_1 if action is QualificationAction.RUN_BLIND_CHALLENGE else AUTH_2
            ),
            policy_revision=POLICY_REVISION,
            checked_at=at,
        )


class _Challenger:
    def __init__(self, *, defect: bool = False) -> None:
        self.defect = defect
        self.requests: list[BlindChallengeRequest] = []

    def run(
        self, request: BlindChallengeRequest, *, at: datetime
    ) -> BlindChallengeRecord:
        self.requests.append(request)
        defects = (
            (
                ChallengeDefect(
                    defect_kind=ChallengeDefectKind.QUOTATION_ERROR,
                    description="Pinned quotation does not match the assertion.",
                ),
            )
            if self.defect
            else ()
        )
        return BlindChallengeRecord(
            challenge_id="challenge-02b",
            claim_id=request.claim_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            challenged_model=request.challenged_model,
            challenger_model=ModelRunIdentity(
                provider="frontier",
                model_name="challenge-model",
                model_revision="r1",
            ),
            saw_challenged_conclusion=False,
            issued_at=at,
            outcome=(
                ChallengeOutcome.DEFECT_FOUND
                if self.defect
                else ChallengeOutcome.NO_DEFECT
            ),
            defects=defects,
        )


class _ReviewSurface:
    def __init__(self) -> None:
        self.reviews = []

    def present(self, review: object, *, at: datetime) -> ReviewPresentationReceipt:
        self.reviews.append(review)
        return ReviewPresentationReceipt(
            review_id=REVIEW_ID,
            review_sha256=_digest(review),
            presented_at=at,
        )


class _DecisionApi:
    def __init__(self) -> None:
        self.requests = []

    def record(self, request: object) -> HumanDecisionReceipt:
        self.requests.append(request)
        return HumanDecisionReceipt(
            decision_id=DECISION_ID,
            idempotency_key=request.idempotency_key,
            reviewer_principal_id=REVIEWER_ID,
            decision=request.decision,
            claim_id=request.claim_id,
            claim_version=request.claim_version,
            policy_revision=POLICY_REVISION,
            authorization_decision_id=AUTH_2,
            decided_at=T4,
            receipt_sha256=RECEIPT_SHA256,
        )


class ProposalQualificationTests(unittest.TestCase):
    def harness(
        self, *, revoke_on_decision: bool = False, defect: bool = False
    ) -> tuple[
        ProposalQualificationCoordinator,
        _Policy,
        _Challenger,
        _ReviewSurface,
        _DecisionApi,
        InMemoryQualificationReplayLedger,
    ]:
        policy = _Policy(revoke_on_decision=revoke_on_decision)
        challenger = _Challenger(defect=defect)
        surface = _ReviewSurface()
        decisions = _DecisionApi()
        ledger = InMemoryQualificationReplayLedger()
        coordinator = ProposalQualificationCoordinator(
            policy=policy,
            challenger=challenger,
            review_surface=surface,
            decisions=decisions,
            replay_ledger=ledger,
        )
        return coordinator, policy, challenger, surface, decisions, ledger

    def qualify(
        self,
        coordinator: ProposalQualificationCoordinator,
        decision: HumanClaimDecision,
    ):
        return coordinator.run(
            proposal=_proposal(),
            claim=_claim(),
            support_verification=_verification(),
            decision=decision,
            decision_idempotency_key=IDEMPOTENCY_KEY,
            note="Synthetic reviewer decision.",
            at=T4,
        )

    def test_complete_acceptance_replay_crosses_every_boundary_once(self) -> None:
        coordinator, policy, challenger, surface, decisions, ledger = self.harness()

        replay = self.qualify(coordinator, HumanClaimDecision.ACCEPTED)
        replayed = self.qualify(coordinator, HumanClaimDecision.ACCEPTED)

        self.assertEqual(replay, replayed)
        self.assertEqual(ValidationOutcome.PASSED, replay.gate_outcome)
        self.assertEqual(
            tuple(ReplayStage), tuple(event.stage for event in replay.events)
        )
        self.assertEqual(replay.chain_head_sha256, replay.events[-1].event_sha256)
        self.assertEqual(2, len(policy.calls))
        self.assertEqual(1, len(challenger.requests))
        self.assertNotIn("conclusion", challenger.requests[0].model_dump())
        self.assertEqual(1, len(surface.reviews))
        self.assertEqual(1, len(decisions.requests))
        self.assertEqual(replay, ledger.find(replay.run_key))

    def test_policy_revocation_mid_run_fails_closed_before_human_decision(self) -> None:
        coordinator, policy, challenger, surface, decisions, ledger = self.harness(
            revoke_on_decision=True
        )

        with self.assertRaises(PolicyDeniedError):
            self.qualify(coordinator, HumanClaimDecision.REJECTED)

        self.assertEqual(
            [
                QualificationAction.RUN_BLIND_CHALLENGE,
                QualificationAction.RECORD_HUMAN_DECISION,
            ],
            policy.calls,
        )
        self.assertEqual(1, len(challenger.requests))
        self.assertEqual(1, len(surface.reviews))
        self.assertEqual([], decisions.requests)
        self.assertIsNone(ledger.find("pilot-run-02b"))

    def test_challenge_defect_is_preserved_and_blocks_human_acceptance(self) -> None:
        coordinator, _, _, surface, decisions, ledger = self.harness(defect=True)

        with self.assertRaises(WorkflowInvariantError):
            self.qualify(coordinator, HumanClaimDecision.ACCEPTED)

        review = surface.reviews[0]
        self.assertEqual(ChallengeOutcome.DEFECT_FOUND, review.challenge.outcome)
        self.assertEqual(
            "Pinned quotation does not match the assertion.",
            review.challenge.defects[0].description,
        )
        self.assertEqual(ValidationOutcome.FAILED, review.gate.outcome)
        self.assertEqual([], decisions.requests)
        self.assertIsNone(ledger.find("pilot-run-02b"))

    def test_human_rejection_produces_complete_replay_without_state_advance(
        self,
    ) -> None:
        coordinator, _, _, surface, decisions, _ = self.harness()

        replay = self.qualify(coordinator, HumanClaimDecision.REJECTED)

        self.assertEqual(HumanClaimDecision.REJECTED, replay.decision)
        self.assertEqual(ValidationOutcome.PASSED, replay.gate_outcome)
        self.assertEqual(LedgerClaimStatus.SUPPORTED, _claim().status)
        self.assertEqual(HumanClaimDecision.REJECTED, decisions.requests[0].decision)
        self.assertEqual(_claim().version, decisions.requests[0].claim_version)
        self.assertEqual(1, len(surface.reviews))


class SourceBoundaryTests(unittest.TestCase):
    def test_module_contains_no_hammertime_connector_or_external_action(self) -> None:
        source = Path(
            "services/worker/src/sklegal_worker/proposal_qualification.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("sklegal_hammertime", source)
        self.assertNotIn("dispatch(", source)


if __name__ == "__main__":
    unittest.main()
