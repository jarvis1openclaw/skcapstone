"""SKL-S3-05C tests: blind challenge and the release gate stack.

The named card cases are same-model challenge labeling and changed artifact
after approval, plus the CLAIM_READY, DRAFT_READY, and RELEASE_READY gates
consumed by SKL-S4-03, SKL-S4-04, and Sprint 5. The acceptance test proves
that no failed gate can be waived by model output.
"""

from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    Approval,
    ApprovalStatus,
    ArtifactBinding,
    Authority,
    AuthorityCitation,
    AuthorityQuotation,
    AuthorityStatus,
    AuthoritySupportVerification,
    BlindChallengeRecord,
    ChallengeDefect,
    ChallengeDefectKind,
    ChallengeIndependence,
    ChallengeOutcome,
    ClaimSupport,
    ClaimSupportKind,
    EffectiveInterval,
    GateCheck,
    GateCheckId,
    GateEvaluation,
    GateFailureReason,
    GateKind,
    LedgerClaim,
    LedgerClaimStatus,
    MatterAuthorityScope,
    ModelRunIdentity,
    Remedy,
    RetrievalPlaneBinding,
    SourceReference,
    ValidationOutcome,
    WorkProduct,
    WorkProductStatus,
    WorkProductUnknown,
    WorkProductUnknownStatus,
    WorkProductVersion,
    WorkProductVersionStatus,
    evaluate_claim_ready_gate,
    evaluate_draft_ready_gate,
    evaluate_release_ready_gate,
    label_challenge_independence,
    run_contrary_authority_search,
    verify_authority_quotation,
    verify_authority_support,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)
T4 = T0 + timedelta(minutes=4)
T5 = T0 + timedelta(minutes=5)
T6 = T0 + timedelta(minutes=6)
T7 = T0 + timedelta(minutes=7)
T8 = T0 + timedelta(minutes=8)

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT_ID = UUID("20000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("10000000-0000-4000-8000-000000000301")
OTHER_MATTER_ID = UUID("10000000-0000-4000-8000-000000000302")
PRINCIPAL_ID = UUID("10000000-0000-4000-8000-000000000011")
CLAIM_ID = UUID("10000000-0000-4000-8000-000000000401")
SUPPORT_ID = UUID("10000000-0000-4000-8000-000000000402")
OTHER_CLAIM_ID = UUID("10000000-0000-4000-8000-000000000403")
AUTHORITY_ID = UUID("10000000-0000-4000-8000-000000000601")
CITATION_ID = UUID("10000000-0000-4000-8000-000000000602")
QUOTATION_ID = UUID("10000000-0000-4000-8000-000000000603")
VALIDATION_ID = UUID("10000000-0000-4000-8000-000000000604")
REMEDY_ID = UUID("10000000-0000-4000-8000-000000000605")
SOURCE_REFERENCE_ID = UUID("10000000-0000-4000-8000-000000000701")
WORK_PRODUCT_ID = UUID("10000000-0000-4000-8000-000000000801")
OTHER_VERSION_ID = UUID("10000000-0000-4000-8000-000000000802")
VERSION_ID = UUID("10000000-0000-4000-8000-000000000803")
UNKNOWN_ID = UUID("10000000-0000-4000-8000-000000000804")
APPROVAL_ID = UUID("10000000-0000-4000-8000-000000000805")
WORK_VALIDATION_ID = UUID("10000000-0000-4000-8000-000000000806")
POLICY_REVISION = "a" * 64

SOURCE_TEXT = "Section 10 of the Act reaches deceptive practices in trade or commerce."
SPAN_START = 0
SPAN_END = 27
EXCERPT = SOURCE_TEXT[SPAN_START:SPAN_END]
SOURCE_SHA256 = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
EXCERPT_SHA256 = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()
VERSION_SHA256 = "f" * 64
OTHER_CONTENT_SHA256 = "d" * 64

SCOPE = MatterAuthorityScope(jurisdiction="US-IL", scope_keys=("consumer_fraud",))

PLANE = RetrievalPlaneBinding(
    plane_id="tenant-us-il-primary",
    route_label="embedding.serving.primary",
    embedding_model_revision="bge-m3-v1",
    qualification_verdict_sha256="e" * 64,
    alias_revision_sequence=3,
)

QWEN = ModelRunIdentity(provider="qwen_local", model_name="qwen3", model_revision="v1")
OPENAI = ModelRunIdentity(provider="openai", model_name="gpt-5", model_revision="r1")


def _reference(content_sha256: str = SOURCE_SHA256) -> SourceReference:
    return SourceReference(
        source_reference_id=SOURCE_REFERENCE_ID,
        source_system="synthetic",
        source_version="v1",
        content_sha256=content_sha256,
        locator="synthetic:release-gates",
        observed_at=T0,
    )


def _support_record() -> ClaimSupport:
    return ClaimSupport(
        id=SUPPORT_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        claim_id=CLAIM_ID,
        kind=ClaimSupportKind.SUPPORT,
        source_reference=_reference(),
        span_start=0,
        span_end=12,
        excerpt_sha256="b" * 64,
        recorded_by_principal_id=PRINCIPAL_ID,
        policy_revision=POLICY_REVISION,
        created_at=T0,
        updated_at=T0,
    )


def _ledger_claim(*, supported_at: datetime = T2) -> LedgerClaim:
    """A ledger claim that reached SUPPORTED at ``supported_at``."""

    claim = LedgerClaim.model_validate(
        {
            "id": CLAIM_ID,
            "tenant_id": TENANT_ID,
            "matter_id": MATTER_ID,
            "statement": "Synthetic ledger claim statement.",
            "policy_revision": POLICY_REVISION,
            "support": (_support_record(),),
            "created_at": T0,
            "updated_at": T0,
        }
    )
    claim = claim.transition_to(LedgerClaimStatus.UNDER_REVIEW, at=T1)
    return claim.transition_to(LedgerClaimStatus.SUPPORTED, at=supported_at)


def _authority() -> Authority:
    return Authority.model_validate(
        {
            "id": AUTHORITY_ID,
            "tenant_id": TENANT_ID,
            "matter_id": MATTER_ID,
            "title": "Consumer Fraud Act section 10",
            "citation": "815 ILCS 505/10",
            "jurisdiction": "US-IL",
            "authority_kind": "statute",
            "source_reference": _reference(),
            "effective_interval": EffectiveInterval(valid_from=T0, valid_to=None),
            "applicability_validation_id": VALIDATION_ID,
            "status": AuthorityStatus.VERIFIED,
            "created_at": T0,
            "updated_at": T0,
        }
    )


def _quotation() -> AuthorityQuotation:
    return AuthorityQuotation(
        quotation_id=QUOTATION_ID,
        authority_id=AUTHORITY_ID,
        quoted_text=EXCERPT,
        span_start=SPAN_START,
        span_end=SPAN_END,
        excerpt_sha256=EXCERPT_SHA256,
        source_content_sha256=SOURCE_SHA256,
    )


def _citation() -> AuthorityCitation:
    return AuthorityCitation(
        citation_id=CITATION_ID,
        authority=_authority(),
        asserted_scope_keys=("consumer_fraud",),
        quotations=(_quotation(),),
    )


def _remedy() -> Remedy:
    return Remedy.model_validate(
        {
            "id": REMEDY_ID,
            "tenant_id": TENANT_ID,
            "matter_id": MATTER_ID,
            "claim_id": CLAIM_ID,
            "description": "Rescission and costs.",
            "authority_ids": (AUTHORITY_ID,),
            "created_at": T0,
            "updated_at": T0,
        }
    )


def _passing_verification(
    *, at: datetime = T3, remedies: tuple[Remedy, ...] | None = None
) -> AuthoritySupportVerification:
    return verify_authority_support(
        claim=_ledger_claim(),
        scope=SCOPE,
        citations=(_citation(),),
        quotation_verifications={
            QUOTATION_ID: verify_authority_quotation(
                _quotation(),
                source_text=SOURCE_TEXT,
                expected_source_sha256=SOURCE_SHA256,
            )
        },
        remedies=(_remedy(),) if remedies is None else remedies,
        contrary_search=run_contrary_authority_search(
            SCOPE, plane=PLANE, candidates=(), at=at
        ),
        at=at,
    )


def _challenge(**overrides: object) -> BlindChallengeRecord:
    payload: dict[str, object] = {
        "challenge_id": "ch-1",
        "claim_id": CLAIM_ID,
        "tenant_id": TENANT_ID,
        "matter_id": MATTER_ID,
        "challenged_model": QWEN,
        "challenger_model": OPENAI,
        "saw_challenged_conclusion": False,
        "issued_at": T4,
        "outcome": ChallengeOutcome.NO_DEFECT,
    }
    payload.update(overrides)
    return BlindChallengeRecord.model_validate(payload)


def _defect() -> ChallengeDefect:
    return ChallengeDefect(
        defect_kind=ChallengeDefectKind.UNSUPPORTED_ASSERTION,
        description="The cited section does not support the stated scope.",
    )


def _frozen_version(
    *, status: WorkProductVersionStatus = WorkProductVersionStatus.FROZEN
) -> WorkProductVersion:
    version = WorkProductVersion(
        id=VERSION_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        work_product_id=WORK_PRODUCT_ID,
        version_number=1,
        content_sha256=VERSION_SHA256,
        source_artifact_id=SOURCE_REFERENCE_ID,
        created_at=T0,
        updated_at=T0,
    )
    if status is WorkProductVersionStatus.DRAFT:
        return version
    frozen = version.transition_to(WorkProductVersionStatus.FROZEN, at=T1)
    if status is WorkProductVersionStatus.SUPERSEDED:
        return frozen.transition_to(status, at=T1)
    return frozen


def _work_product(
    *,
    status: WorkProductStatus = WorkProductStatus.APPROVED,
    current_version_id: UUID = VERSION_ID,
) -> WorkProduct:
    product = WorkProduct.model_validate(
        {
            "id": WORK_PRODUCT_ID,
            "tenant_id": TENANT_ID,
            "matter_id": MATTER_ID,
            "title": "Synthetic motion to dismiss",
            "work_product_kind": "pleading",
            "current_version_id": current_version_id,
            "created_at": T0,
            "updated_at": T0,
        }
    )
    product = product.transition_to(WorkProductStatus.IN_REVIEW, at=T2)
    product = product.transition_to(
        WorkProductStatus.VALIDATED, at=T3, validation_result_id=WORK_VALIDATION_ID
    )
    if status is WorkProductStatus.APPROVED:
        product = product.transition_to(
            WorkProductStatus.APPROVED, at=T6, approval_id=APPROVAL_ID
        )
    elif status is not WorkProductStatus.VALIDATED:
        raise ValueError(f"unsupported fixture status: {status}")
    return product


def _resolved_unknown() -> WorkProductUnknown:
    unknown = WorkProductUnknown(
        id=UNKNOWN_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        version_binding=ArtifactBinding(
            artifact_id=VERSION_ID,
            artifact_version=1,
            content_sha256=VERSION_SHA256,
        ),
        placeholder_key="filing_court",
        created_at=T0,
        updated_at=T0,
    )
    return unknown.transition_to(
        WorkProductUnknownStatus.RESOLVED,
        at=T1,
        resolved_by_principal_id=PRINCIPAL_ID,
        resolved_at=T1,
    )


def _open_unknown() -> WorkProductUnknown:
    return WorkProductUnknown(
        id=UNKNOWN_ID,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        version_binding=ArtifactBinding(
            artifact_id=VERSION_ID,
            artifact_version=1,
            content_sha256=VERSION_SHA256,
        ),
        placeholder_key="filing_court",
        created_at=T0,
        updated_at=T0,
    )


def _approval(
    *,
    subject: ArtifactBinding | None = None,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    approval_id: UUID = APPROVAL_ID,
) -> Approval:
    binding = subject or ArtifactBinding(
        artifact_id=VERSION_ID, artifact_version=1, content_sha256=VERSION_SHA256
    )
    approval = Approval(
        id=approval_id,
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        subject=binding,
        created_at=T5,
        updated_at=T5,
    )
    if status is ApprovalStatus.APPROVED:
        return approval.transition_to(
            ApprovalStatus.APPROVED,
            at=T6,
            reviewer_principal_id=PRINCIPAL_ID,
            decided_at=T6,
            rationale="Exact version reviewed and approved.",
        )
    if status is ApprovalStatus.REVOKED:
        approved = approval.transition_to(
            ApprovalStatus.APPROVED,
            at=T6,
            reviewer_principal_id=PRINCIPAL_ID,
            decided_at=T6,
            rationale="Exact version reviewed and approved.",
        )
        return approved.transition_to(
            ApprovalStatus.REVOKED,
            at=T7,
            revoker_principal_id=PRINCIPAL_ID,
            revocation_rationale="New contrary authority surfaced.",
            revoked_at=T7,
        )
    raise ValueError(f"unsupported fixture status: {status}")


_PASSING = object()


def _claim_gate(
    *,
    at: datetime = T5,
    verification: AuthoritySupportVerification | None | object = _PASSING,
    challenges: tuple[BlindChallengeRecord, ...] | None = None,
) -> GateEvaluation:
    return evaluate_claim_ready_gate(
        claim=_ledger_claim(),
        support_verification=(
            _passing_verification() if verification is _PASSING else verification
        ),
        challenges=(_challenge(),) if challenges is None else challenges,
        at=at,
    )


class ModelRunIdentityTests(unittest.TestCase):
    def test_same_model_across_routes_and_aliases(self) -> None:
        routed = ModelRunIdentity(
            provider="qwen_local",
            model_name="qwen3",
            model_revision="v1",
            route_id="model.challenge.secondary",
            served_alias="qwen3-8b",
        )
        self.assertTrue(QWEN.same_model_as(routed))
        self.assertTrue(routed.same_model_as(QWEN))

    def test_different_revision_is_a_different_model(self) -> None:
        other = ModelRunIdentity(
            provider="qwen_local", model_name="qwen3", model_revision="v2"
        )
        self.assertFalse(QWEN.same_model_as(other))

    def test_different_provider_is_a_different_model(self) -> None:
        self.assertFalse(QWEN.same_model_as(OPENAI))

    def test_identity_comparison_ignores_case_and_spacing(self) -> None:
        noisy = ModelRunIdentity(
            provider=" Qwen_Local ", model_name="Qwen3", model_revision=" V1 "
        )
        self.assertTrue(QWEN.same_model_as(noisy))


class BlindChallengeRecordTests(unittest.TestCase):
    def test_same_model_challenge_is_labeled(self) -> None:
        challenge = _challenge(challenger_model=QWEN)
        self.assertTrue(challenge.same_model)
        self.assertEqual(ChallengeIndependence.SAME_MODEL, challenge.independence)
        self.assertEqual(
            ChallengeIndependence.SAME_MODEL, label_challenge_independence(challenge)
        )

    def test_not_blind_challenge_is_labeled(self) -> None:
        challenge = _challenge(saw_challenged_conclusion=True)
        self.assertFalse(challenge.same_model)
        self.assertEqual(ChallengeIndependence.NOT_BLIND, challenge.independence)

    def test_independent_challenge_is_labeled(self) -> None:
        challenge = _challenge()
        self.assertEqual(ChallengeIndependence.INDEPENDENT, challenge.independence)

    def test_defect_found_requires_defects(self) -> None:
        with self.assertRaises(ValidationError):
            _challenge(outcome=ChallengeOutcome.DEFECT_FOUND)

    def test_no_defect_cannot_carry_defects(self) -> None:
        with self.assertRaises(ValidationError):
            _challenge(defects=(_defect(),))

    def test_defects_are_preserved_verbatim(self) -> None:
        challenge = _challenge(
            outcome=ChallengeOutcome.DEFECT_FOUND, defects=(_defect(),)
        )
        self.assertEqual((_defect(),), challenge.defects)


class ClaimReadyGateTests(unittest.TestCase):
    def _failed_reasons(self, evaluation: GateEvaluation, check_id: GateCheckId):
        failed = {check.check_id: check for check in evaluation.failed_checks}
        return failed[check_id].reasons

    def test_supported_challenged_claim_passes_every_check(self) -> None:
        evaluation = _claim_gate()
        self.assertEqual(GateKind.CLAIM_READY, evaluation.gate)
        self.assertEqual(ValidationOutcome.PASSED, evaluation.outcome)
        self.assertEqual(CLAIM_ID, evaluation.subject_id)
        self.assertEqual(TENANT_ID, evaluation.tenant_id)
        self.assertEqual(MATTER_ID, evaluation.matter_id)
        self.assertEqual(T5, evaluation.evaluated_at)
        self.assertEqual((), evaluation.failed_checks)
        self.assertEqual(
            {
                GateCheckId.CLAIM_SUPPORT_VERIFIED,
                GateCheckId.CLAIM_STATUS_SUPPORTED,
                GateCheckId.CHALLENGE_EXECUTED,
                GateCheckId.CHALLENGE_CURRENT,
                GateCheckId.CHALLENGE_INDEPENDENT,
                GateCheckId.CHALLENGE_NO_DEFECT,
            },
            {check.check_id for check in evaluation.checks},
        )

    def test_missing_authority_support_fails_closed(self) -> None:
        evaluation = _claim_gate(verification=None)
        self.assertEqual(ValidationOutcome.FAILED, evaluation.outcome)
        self.assertEqual(
            (GateFailureReason.AUTHORITY_SUPPORT_MISSING,),
            self._failed_reasons(evaluation, GateCheckId.CLAIM_SUPPORT_VERIFIED),
        )

    def test_failed_authority_support_fails_the_gate(self) -> None:
        failed = _passing_verification(remedies=())
        self.assertEqual(ValidationOutcome.FAILED, failed.outcome)
        evaluation = _claim_gate(verification=failed)
        self.assertEqual(
            (GateFailureReason.AUTHORITY_SUPPORT_FAILED,),
            self._failed_reasons(evaluation, GateCheckId.CLAIM_SUPPORT_VERIFIED),
        )

    def test_stale_authority_support_fails_the_gate(self) -> None:
        stale = _passing_verification(at=T1)
        claim = _ledger_claim(supported_at=T2)
        self.assertGreater(claim.updated_at, stale.evaluated_at)
        evaluation = evaluate_claim_ready_gate(
            claim=claim,
            support_verification=stale,
            challenges=(_challenge(),),
            at=T5,
        )
        self.assertEqual(
            (GateFailureReason.AUTHORITY_SUPPORT_STALE,),
            self._failed_reasons(evaluation, GateCheckId.CLAIM_SUPPORT_VERIFIED),
        )

    def test_support_for_another_claim_crosses_scope(self) -> None:
        crossing = AuthoritySupportVerification(
            claim_id=OTHER_CLAIM_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            evaluated_at=T3,
            checks=(),
            outcome=ValidationOutcome.PASSED,
        )
        evaluation = _claim_gate(verification=crossing)
        self.assertIn(
            GateFailureReason.SUPPORT_SCOPE_CROSSING,
            self._failed_reasons(evaluation, GateCheckId.CLAIM_SUPPORT_VERIFIED),
        )

    def test_unsupported_claim_status_fails_the_gate(self) -> None:
        claim = _ledger_claim()
        claim = claim.transition_to(LedgerClaimStatus.CHALLENGED, at=T3)
        evaluation = evaluate_claim_ready_gate(
            claim=claim,
            support_verification=_passing_verification(),
            challenges=(_challenge(issued_at=T4),),
            at=T5,
        )
        self.assertEqual(
            (GateFailureReason.CLAIM_NOT_SUPPORTED,),
            self._failed_reasons(evaluation, GateCheckId.CLAIM_STATUS_SUPPORTED),
        )

    def test_missing_challenge_fails_closed(self) -> None:
        evaluation = _claim_gate(challenges=())
        self.assertEqual(
            (GateFailureReason.CHALLENGE_MISSING,),
            self._failed_reasons(evaluation, GateCheckId.CHALLENGE_EXECUTED),
        )

    def test_challenge_for_another_claim_crosses_scope(self) -> None:
        evaluation = _claim_gate(challenges=(_challenge(claim_id=OTHER_CLAIM_ID),))
        self.assertEqual(
            {
                GateFailureReason.CHALLENGE_SCOPE_CROSSING,
                GateFailureReason.CHALLENGE_MISSING,
            },
            set(self._failed_reasons(evaluation, GateCheckId.CHALLENGE_EXECUTED)),
        )

    def test_stale_challenge_fails_the_gate(self) -> None:
        evaluation = _claim_gate(challenges=(_challenge(issued_at=T1),))
        self.assertEqual(
            (GateFailureReason.STALE_CHALLENGE,),
            self._failed_reasons(evaluation, GateCheckId.CHALLENGE_CURRENT),
        )

    def test_same_model_challenge_cannot_satisfy_independence(self) -> None:
        evaluation = _claim_gate(challenges=(_challenge(challenger_model=QWEN),))
        self.assertEqual(ValidationOutcome.FAILED, evaluation.outcome)
        self.assertEqual(
            (GateFailureReason.SAME_MODEL_CHALLENGE,),
            self._failed_reasons(evaluation, GateCheckId.CHALLENGE_INDEPENDENT),
        )

    def test_not_blind_challenge_cannot_satisfy_independence(self) -> None:
        evaluation = _claim_gate(
            challenges=(_challenge(saw_challenged_conclusion=True),)
        )
        self.assertEqual(
            (GateFailureReason.NOT_BLIND_CHALLENGE,),
            self._failed_reasons(evaluation, GateCheckId.CHALLENGE_INDEPENDENT),
        )

    def test_same_model_and_not_blind_reasons_are_reported_together(self) -> None:
        evaluation = _claim_gate(
            challenges=(
                _challenge(challenger_model=QWEN),
                _challenge(saw_challenged_conclusion=True),
            )
        )
        self.assertEqual(
            (
                GateFailureReason.SAME_MODEL_CHALLENGE,
                GateFailureReason.NOT_BLIND_CHALLENGE,
            ),
            self._failed_reasons(evaluation, GateCheckId.CHALLENGE_INDEPENDENT),
        )

    def test_unresolved_challenge_defect_fails_the_gate(self) -> None:
        evaluation = _claim_gate(
            challenges=(
                _challenge(outcome=ChallengeOutcome.DEFECT_FOUND, defects=(_defect(),)),
            )
        )
        self.assertEqual(
            (GateFailureReason.CHALLENGE_DEFECT_UNRESOLVED,),
            self._failed_reasons(evaluation, GateCheckId.CHALLENGE_NO_DEFECT),
        )

    def test_later_independent_no_defect_challenge_recovers_the_gate(self) -> None:
        evaluation = _claim_gate(
            challenges=(
                _challenge(
                    challenge_id="ch-0",
                    outcome=ChallengeOutcome.DEFECT_FOUND,
                    defects=(_defect(),),
                ),
                _challenge(challenge_id="ch-1"),
            )
        )
        self.assertEqual(ValidationOutcome.PASSED, evaluation.outcome)

    def test_future_challenge_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "postdate"):
            _claim_gate(challenges=(_challenge(issued_at=T6),), at=T5)

    def test_future_support_verification_is_rejected(self) -> None:
        future = AuthoritySupportVerification(
            claim_id=CLAIM_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            evaluated_at=T6,
            checks=(),
            outcome=ValidationOutcome.PASSED,
        )
        with self.assertRaisesRegex(ValueError, "postdate"):
            _claim_gate(verification=future, at=T5)

    def test_naive_evaluation_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _claim_gate(at=datetime(2026, 1, 1, 12, 0, 0))

    def test_evaluation_is_deterministic(self) -> None:
        self.assertEqual(_claim_gate(), _claim_gate())


class DraftReadyGateTests(unittest.TestCase):
    def _evaluate(self, **overrides: object) -> GateEvaluation:
        payload: dict[str, object] = {
            "work_product": _work_product(),
            "version": _frozen_version(),
            "unknowns": (_resolved_unknown(),),
            "grounding_claim_ids": (CLAIM_ID,),
            "claim_gates": (_claim_gate(),),
            "at": T6,
        }
        payload.update(overrides)
        return evaluate_draft_ready_gate(**payload)

    def _failed_reasons(self, evaluation: GateEvaluation, check_id: GateCheckId):
        failed = {check.check_id: check for check in evaluation.failed_checks}
        return failed[check_id].reasons

    def test_frozen_grounded_draft_passes_every_check(self) -> None:
        evaluation = self._evaluate()
        self.assertEqual(GateKind.DRAFT_READY, evaluation.gate)
        self.assertEqual(WORK_PRODUCT_ID, evaluation.subject_id)
        self.assertEqual(ValidationOutcome.PASSED, evaluation.outcome)
        self.assertEqual((), evaluation.failed_checks)
        self.assertEqual(
            {
                GateCheckId.DRAFT_CLAIMS_READY,
                GateCheckId.DRAFT_VERSION_FROZEN,
                GateCheckId.DRAFT_UNKNOWNS_RESOLVED,
            },
            {check.check_id for check in evaluation.checks},
        )

    def test_draft_without_grounding_fails_closed(self) -> None:
        evaluation = self._evaluate(grounding_claim_ids=())
        self.assertEqual(
            (GateFailureReason.GROUNDING_MISSING,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_CLAIMS_READY),
        )

    def test_ungrounded_claim_fails_the_claims_check(self) -> None:
        evaluation = self._evaluate(claim_gates=())
        failed = {
            (check.check_id, check.subject_id) for check in evaluation.failed_checks
        }
        self.assertIn(
            (GateCheckId.DRAFT_CLAIMS_READY, CLAIM_ID),
            failed,
        )
        self.assertEqual(
            (GateFailureReason.CLAIM_NOT_READY,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_CLAIMS_READY),
        )

    def test_failed_claim_gate_fails_the_claims_check(self) -> None:
        failed_claim_gate = _claim_gate(challenges=())
        evaluation = self._evaluate(claim_gates=(failed_claim_gate,))
        self.assertEqual(
            (GateFailureReason.CLAIM_NOT_READY,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_CLAIMS_READY),
        )

    def test_claim_gate_from_another_tenant_crosses_scope(self) -> None:
        crossing = GateEvaluation(
            gate=GateKind.CLAIM_READY,
            subject_id=CLAIM_ID,
            tenant_id=OTHER_TENANT_ID,
            matter_id=MATTER_ID,
            evaluated_at=T5,
            checks=(),
            outcome=ValidationOutcome.PASSED,
        )
        evaluation = self._evaluate(claim_gates=(crossing,))
        self.assertEqual(
            (GateFailureReason.CLAIM_GATE_SCOPE_CROSSING,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_CLAIMS_READY),
        )

    def test_duplicate_claim_gates_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._evaluate(claim_gates=(_claim_gate(), _claim_gate()))

    def test_non_claim_gate_input_is_rejected(self) -> None:
        draft_gate = GateEvaluation(
            gate=GateKind.DRAFT_READY,
            subject_id=WORK_PRODUCT_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            evaluated_at=T5,
            checks=(),
            outcome=ValidationOutcome.PASSED,
        )
        with self.assertRaisesRegex(ValueError, "claim_ready"):
            self._evaluate(claim_gates=(draft_gate,))

    def test_unfrozen_version_fails_the_gate(self) -> None:
        evaluation = self._evaluate(
            version=_frozen_version(status=WorkProductVersionStatus.DRAFT)
        )
        self.assertEqual(
            (GateFailureReason.VERSION_NOT_FROZEN,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_VERSION_FROZEN),
        )

    def test_superseded_version_fails_the_gate(self) -> None:
        evaluation = self._evaluate(
            version=_frozen_version(status=WorkProductVersionStatus.SUPERSEDED)
        )
        self.assertEqual(
            (GateFailureReason.VERSION_NOT_FROZEN,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_VERSION_FROZEN),
        )

    def test_version_that_is_not_current_fails_the_gate(self) -> None:
        evaluation = self._evaluate(
            work_product=_work_product(current_version_id=OTHER_VERSION_ID)
        )
        self.assertEqual(
            (GateFailureReason.VERSION_NOT_CURRENT,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_VERSION_FROZEN),
        )

    def test_version_from_another_work_product_crosses_scope(self) -> None:
        version = _frozen_version()
        version = version.model_dump(mode="python")
        version["work_product_id"] = OTHER_VERSION_ID
        evaluation = self._evaluate(version=WorkProductVersion.model_validate(version))
        self.assertEqual(
            (GateFailureReason.VERSION_SCOPE_CROSSING,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_VERSION_FROZEN),
        )

    def test_open_bracketed_unknown_blocks_the_draft(self) -> None:
        evaluation = self._evaluate(unknowns=(_open_unknown(),))
        self.assertEqual(
            (GateFailureReason.UNKNOWNS_UNRESOLVED,),
            self._failed_reasons(evaluation, GateCheckId.DRAFT_UNKNOWNS_RESOLVED),
        )

    def test_future_claim_gate_is_rejected(self) -> None:
        future = GateEvaluation(
            gate=GateKind.CLAIM_READY,
            subject_id=CLAIM_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            evaluated_at=T7,
            checks=(),
            outcome=ValidationOutcome.PASSED,
        )
        with self.assertRaisesRegex(ValueError, "postdate"):
            self._evaluate(claim_gates=(future,), at=T6)

    def test_naive_evaluation_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._evaluate(at=datetime(2026, 1, 1, 12, 0, 0))


class ReleaseReadyGateTests(unittest.TestCase):
    def _evaluate(self, **overrides: object) -> GateEvaluation:
        payload: dict[str, object] = {
            "work_product": _work_product(),
            "version": _frozen_version(),
            "approval": _approval(),
            "grounding_claim_ids": (CLAIM_ID,),
            "claim_gates": (_claim_gate(),),
            "at": T7,
        }
        payload.update(overrides)
        return evaluate_release_ready_gate(**payload)

    def _failed_reasons(self, evaluation: GateEvaluation, check_id: GateCheckId):
        failed = {check.check_id: check for check in evaluation.failed_checks}
        return failed[check_id].reasons

    def test_approved_unchanged_release_passes_every_check(self) -> None:
        evaluation = self._evaluate()
        self.assertEqual(GateKind.RELEASE_READY, evaluation.gate)
        self.assertEqual(ValidationOutcome.PASSED, evaluation.outcome)
        self.assertEqual((), evaluation.failed_checks)
        self.assertEqual(
            {
                GateCheckId.RELEASE_WORK_PRODUCT_APPROVED,
                GateCheckId.RELEASE_APPROVAL_VALID,
                GateCheckId.RELEASE_ARTIFACT_UNCHANGED,
                GateCheckId.RELEASE_CLAIMS_READY,
            },
            {check.check_id for check in evaluation.checks},
        )

    def test_unapproved_work_product_fails_the_gate(self) -> None:
        evaluation = self._evaluate(
            work_product=_work_product(status=WorkProductStatus.VALIDATED),
            approval=None,
        )
        self.assertEqual(
            (GateFailureReason.WORK_PRODUCT_NOT_APPROVED,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_WORK_PRODUCT_APPROVED),
        )

    def test_missing_approval_linkage_fails_the_gate(self) -> None:
        orphan = _approval(approval_id=UUID("10000000-0000-4000-8000-000000000899"))
        evaluation = self._evaluate(approval=orphan)
        self.assertEqual(
            (GateFailureReason.APPROVAL_LINKAGE_MISSING,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_WORK_PRODUCT_APPROVED),
        )

    def test_missing_approval_fails_closed(self) -> None:
        evaluation = self._evaluate(approval=None)
        self.assertEqual(
            (GateFailureReason.APPROVAL_MISSING,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_APPROVAL_VALID),
        )
        self.assertIn(
            GateFailureReason.APPROVAL_MISSING,
            self._failed_reasons(evaluation, GateCheckId.RELEASE_ARTIFACT_UNCHANGED),
        )

    def test_revoked_approval_fails_the_gate(self) -> None:
        evaluation = self._evaluate(approval=_approval(status=ApprovalStatus.REVOKED))
        self.assertEqual(
            (GateFailureReason.APPROVAL_REVOKED,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_APPROVAL_VALID),
        )

    def test_future_approval_decision_cannot_release_earlier(self) -> None:
        evaluation = self._evaluate(at=T5)
        self.assertEqual(
            (GateFailureReason.APPROVAL_FUTURE,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_APPROVAL_VALID),
        )

    def test_approval_from_another_matter_crosses_scope(self) -> None:
        approval = _approval()
        payload = approval.model_dump(mode="python")
        payload["matter_id"] = OTHER_MATTER_ID
        evaluation = self._evaluate(approval=Approval.model_validate(payload))
        self.assertEqual(
            (GateFailureReason.APPROVAL_SCOPE_CROSSING,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_APPROVAL_VALID),
        )

    def test_changed_artifact_after_approval_fails_the_gate(self) -> None:
        stale_binding = ArtifactBinding(
            artifact_id=VERSION_ID,
            artifact_version=1,
            content_sha256=OTHER_CONTENT_SHA256,
        )
        evaluation = self._evaluate(approval=_approval(subject=stale_binding))
        self.assertEqual(
            (GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_ARTIFACT_UNCHANGED),
        )

    def test_superseded_version_is_a_changed_artifact(self) -> None:
        evaluation = self._evaluate(
            version=_frozen_version(status=WorkProductVersionStatus.SUPERSEDED)
        )
        self.assertIn(
            GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL,
            self._failed_reasons(evaluation, GateCheckId.RELEASE_ARTIFACT_UNCHANGED),
        )

    def test_replaced_current_version_is_a_changed_artifact(self) -> None:
        evaluation = self._evaluate(
            work_product=_work_product(current_version_id=OTHER_VERSION_ID)
        )
        self.assertIn(
            GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL,
            self._failed_reasons(evaluation, GateCheckId.RELEASE_ARTIFACT_UNCHANGED),
        )

    def test_approval_for_another_version_is_a_changed_artifact(self) -> None:
        other_binding = ArtifactBinding(
            artifact_id=OTHER_VERSION_ID,
            artifact_version=1,
            content_sha256=VERSION_SHA256,
        )
        evaluation = self._evaluate(approval=_approval(subject=other_binding))
        self.assertEqual(
            (GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_ARTIFACT_UNCHANGED),
        )

    def test_claim_regression_fails_the_release(self) -> None:
        failed_claim_gate = _claim_gate(challenges=())
        evaluation = self._evaluate(claim_gates=(failed_claim_gate,))
        self.assertEqual(
            (GateFailureReason.CLAIM_NOT_READY,),
            self._failed_reasons(evaluation, GateCheckId.RELEASE_CLAIMS_READY),
        )

    def test_naive_evaluation_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._evaluate(at=datetime(2026, 1, 1, 12, 0, 0))


class ModelOutputCannotWaiveFailedGatesTests(unittest.TestCase):
    """Acceptance: no failed gate can be waived by model output."""

    def test_passing_evaluation_with_a_failed_check_is_unrepresentable(self) -> None:
        failed_check = GateCheck(
            check_id=GateCheckId.CHALLENGE_INDEPENDENT,
            subject_id=CLAIM_ID,
            outcome=ValidationOutcome.FAILED,
            reasons=(GateFailureReason.SAME_MODEL_CHALLENGE,),
        )
        with self.assertRaises(ValidationError):
            GateEvaluation(
                gate=GateKind.CLAIM_READY,
                subject_id=CLAIM_ID,
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                evaluated_at=T5,
                checks=(failed_check,),
                outcome=ValidationOutcome.PASSED,
            )

    def test_failed_evaluation_without_failed_checks_is_unrepresentable(self) -> None:
        with self.assertRaises(ValidationError):
            GateEvaluation(
                gate=GateKind.CLAIM_READY,
                subject_id=CLAIM_ID,
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                evaluated_at=T5,
                checks=(),
                outcome=ValidationOutcome.FAILED,
            )

    def test_a_no_defect_model_challenge_cannot_waive_failed_support(self) -> None:
        failed_support = _passing_verification(remedies=())
        evaluation = _claim_gate(verification=failed_support)
        self.assertEqual(ValidationOutcome.FAILED, evaluation.outcome)
        failed = {check.check_id for check in evaluation.failed_checks}
        self.assertEqual({GateCheckId.CLAIM_SUPPORT_VERIFIED}, failed)
        self.assertEqual(
            (GateFailureReason.AUTHORITY_SUPPORT_FAILED,),
            evaluation.failed_checks[0].reasons,
        )

    def test_a_same_model_no_defect_challenge_still_fails(self) -> None:
        evaluation = _claim_gate(challenges=(_challenge(challenger_model=QWEN),))
        failed = {check.check_id: check for check in evaluation.failed_checks}
        self.assertEqual(ValidationOutcome.FAILED, evaluation.outcome)
        self.assertEqual(
            (GateFailureReason.SAME_MODEL_CHALLENGE,),
            failed[GateCheckId.CHALLENGE_INDEPENDENT].reasons,
        )

    def test_clean_challenge_evidence_cannot_flip_a_failed_gate(self) -> None:
        """Every challenge record passes, but the deterministic check fails."""

        failed_support = _passing_verification(remedies=())
        pristine_challenges = (
            _challenge(challenge_id="ch-1"),
            _challenge(
                challenge_id="ch-2",
                challenger_model=ModelRunIdentity(
                    provider="openai",
                    model_name="o4",
                    model_revision="r2",
                ),
            ),
        )
        evaluation = _claim_gate(
            verification=failed_support, challenges=pristine_challenges
        )
        self.assertEqual(ValidationOutcome.FAILED, evaluation.outcome)
        failed = {check.check_id for check in evaluation.failed_checks}
        self.assertIn(GateCheckId.CLAIM_SUPPORT_VERIFIED, failed)
        self.assertNotIn(GateCheckId.CHALLENGE_INDEPENDENT, failed)
        self.assertNotIn(GateCheckId.CHALLENGE_NO_DEFECT, failed)

    def test_gate_signatures_admit_only_domain_typed_records(self) -> None:
        """No waiver channel exists: no free payload can enter a gate.

        Every parameter of every gate function resolves to datetime, None,
        or types defined inside ``sklegal_domain``. A raw dict, Any, or a
        model payload parameter cannot be added without failing here.
        """

        import inspect
        import types
        import typing

        from sklegal_domain import release_gates

        def referenced(annotation: object) -> set[object]:
            if typing.get_origin(annotation) is typing.Annotated:
                # Only the underlying type is a payload channel; Annotated
                # metadata (validators like AfterValidator) admits nothing.
                return referenced(typing.get_args(annotation)[0])
            args = typing.get_args(annotation)
            if not args:
                return {annotation}
            found: set[object] = set()
            for arg in args:
                found |= referenced(arg)
            return found

        for name in (
            "evaluate_claim_ready_gate",
            "evaluate_draft_ready_gate",
            "evaluate_release_ready_gate",
        ):
            function = getattr(release_gates, name)
            signature = inspect.signature(function, eval_str=True)
            self.assertTrue(signature.parameters, name)
            for parameter in signature.parameters.values():
                for item in referenced(parameter.annotation):
                    if item is types.NoneType or item is Ellipsis:
                        continue
                    module = getattr(item, "__module__", None)
                    self.assertTrue(
                        module == "datetime"
                        or module == "uuid"
                        or (module is not None and module.startswith("sklegal_domain")),
                        f"{name} parameter {parameter.name} admits {item!r}",
                    )


if __name__ == "__main__":
    unittest.main()
