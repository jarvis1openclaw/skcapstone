"""SKL-S3-05B tests: authority applicability, status, quotation, contrary search.

Deterministic verification over the SKL-S3-05A claim ledger. The named card
tests are wrong jurisdiction, stale authority, quotation mismatch, and
missing remedy. The acceptance test proves that a similarity score alone
never qualifies an authority.
"""

from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    Authority,
    AuthorityCheckId,
    AuthorityCitation,
    AuthorityFailureReason,
    AuthorityQuotation,
    AuthorityStatus,
    AuthoritySupportVerification,
    ClaimSupport,
    ClaimSupportKind,
    ContraryAuthoritySearchResult,
    ContraryLeadDisposition,
    ContrarySearchCandidate,
    EffectiveInterval,
    LedgerClaim,
    MatterAuthorityScope,
    Remedy,
    RetrievalPlaneBinding,
    SourceReference,
    SupportCheck,
    ValidationOutcome,
    evaluate_authority_citation,
    normalize_jurisdiction,
    run_contrary_authority_search,
    verify_authority_quotation,
    verify_authority_support,
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
AUTHORITY_ID = UUID("10000000-0000-4000-8000-000000000601")
CITATION_ID = UUID("10000000-0000-4000-8000-000000000602")
QUOTATION_ID = UUID("10000000-0000-4000-8000-000000000603")
VALIDATION_ID = UUID("10000000-0000-4000-8000-000000000604")
REMEDY_ID = UUID("10000000-0000-4000-8000-000000000605")
POLICY_REVISION = "a" * 64

SOURCE_TEXT = "Section 10 of the Act reaches deceptive practices in trade or commerce."
SPAN_START = 0
SPAN_END = 27
EXCERPT = SOURCE_TEXT[SPAN_START:SPAN_END]
SOURCE_SHA256 = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
EXCERPT_SHA256 = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()
OTHER_SHA256 = "d" * 64

SCOPE = MatterAuthorityScope(jurisdiction="US-IL", scope_keys=("consumer_fraud",))

PLANE = RetrievalPlaneBinding(
    plane_id="tenant-us-il-primary",
    route_label="embedding.serving.primary",
    embedding_model_revision="bge-m3-v1",
    qualification_verdict_sha256="e" * 64,
    alias_revision_sequence=3,
)


def _reference(content_sha256: str = SOURCE_SHA256) -> SourceReference:
    return SourceReference(
        source_reference_id=UUID("10000000-0000-4000-8000-000000000701"),
        source_system="synthetic",
        source_version="v1",
        content_sha256=content_sha256,
        locator="synthetic:authority-verification",
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


def _ledger_claim() -> LedgerClaim:
    return LedgerClaim.model_validate(
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


def _authority(**overrides: object) -> Authority:
    payload: dict[str, object] = {
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
    payload.update(overrides)
    return Authority.model_validate(payload)


def _quotation(**overrides: object) -> AuthorityQuotation:
    payload: dict[str, object] = {
        "quotation_id": QUOTATION_ID,
        "authority_id": AUTHORITY_ID,
        "quoted_text": EXCERPT,
        "span_start": SPAN_START,
        "span_end": SPAN_END,
        "excerpt_sha256": EXCERPT_SHA256,
        "source_content_sha256": SOURCE_SHA256,
    }
    payload.update(overrides)
    return AuthorityQuotation.model_validate(payload)


def _citation(**overrides: object) -> AuthorityCitation:
    payload: dict[str, object] = {
        "citation_id": CITATION_ID,
        "authority": _authority(),
        "asserted_scope_keys": ("consumer_fraud",),
        "quotations": (_quotation(),),
    }
    payload.update(overrides)
    return AuthorityCitation.model_validate(payload)


def _verified_quotation(quotation: AuthorityQuotation | None = None):
    quotation = quotation or _quotation()
    return verify_authority_quotation(
        quotation, source_text=SOURCE_TEXT, expected_source_sha256=SOURCE_SHA256
    )


def _remedy(**overrides: object) -> Remedy:
    payload: dict[str, object] = {
        "id": REMEDY_ID,
        "tenant_id": TENANT_ID,
        "matter_id": MATTER_ID,
        "claim_id": CLAIM_ID,
        "description": "Rescission and costs.",
        "authority_ids": (AUTHORITY_ID,),
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    return Remedy.model_validate(payload)


def _empty_search(at: datetime = T3) -> ContraryAuthoritySearchResult:
    return run_contrary_authority_search(SCOPE, plane=PLANE, candidates=(), at=at)


def _passed_candidate(**overrides: object) -> ContrarySearchCandidate:
    payload: dict[str, object] = {
        "candidate_id": "cand-1",
        "similarity": 0.42,
        "citation": "815 ILCS 505/10",
        "jurisdiction": "US-IL",
        "observed_authority_status": AuthorityStatus.VERIFIED,
        "asserted_scope_keys": ("consumer_fraud",),
        "quotation_verification": _verified_quotation(),
    }
    payload.update(overrides)
    return ContrarySearchCandidate.model_validate(payload)


class NormalizeJurisdictionTests(unittest.TestCase):
    def test_collapses_whitespace_and_case(self) -> None:
        self.assertEqual("us-il", normalize_jurisdiction("  US-IL  "))
        self.assertEqual("illinois", normalize_jurisdiction("Illinois"))
        self.assertNotEqual(
            normalize_jurisdiction("US-IL"), normalize_jurisdiction("US-NY")
        )

    def test_rejects_non_text(self) -> None:
        with self.assertRaises(TypeError):
            normalize_jurisdiction(7)  # type: ignore[arg-type]


class QuotationVerificationTests(unittest.TestCase):
    def test_exact_quotation_passes_with_observed_digests(self) -> None:
        verification = _verified_quotation()
        self.assertTrue(verification.passed)
        self.assertEqual(ValidationOutcome.PASSED, verification.outcome)
        self.assertEqual((), verification.failure_reasons)
        self.assertEqual(SOURCE_SHA256, verification.observed_source_sha256)
        self.assertEqual(EXCERPT_SHA256, verification.observed_excerpt_sha256)

    def test_changed_source_bytes_fail_the_document_digest(self) -> None:
        altered = SOURCE_TEXT.replace("Section", "Sections")
        verification = verify_authority_quotation(
            _quotation(),
            source_text=altered,
            expected_source_sha256=SOURCE_SHA256,
        )
        self.assertFalse(verification.passed)
        self.assertIn(
            AuthorityFailureReason.SOURCE_DIGEST_MISMATCH,
            verification.failure_reasons,
        )

    def test_wrong_expected_digest_fails_even_with_exact_text(self) -> None:
        verification = verify_authority_quotation(
            _quotation(),
            source_text=SOURCE_TEXT,
            expected_source_sha256=OTHER_SHA256,
        )
        self.assertIn(
            AuthorityFailureReason.SOURCE_DIGEST_MISMATCH,
            verification.failure_reasons,
        )

    def test_span_beyond_the_document_is_out_of_bounds(self) -> None:
        quotation = _quotation(span_end=len(SOURCE_TEXT) + 5)
        verification = verify_authority_quotation(
            quotation, source_text=SOURCE_TEXT, expected_source_sha256=SOURCE_SHA256
        )
        self.assertIn(
            AuthorityFailureReason.SPAN_OUT_OF_BOUNDS, verification.failure_reasons
        )

    def test_excerpt_digest_mismatch(self) -> None:
        quotation = _quotation(span_start=8, span_end=35, quoted_text=SOURCE_TEXT[8:35])
        verification = verify_authority_quotation(
            quotation, source_text=SOURCE_TEXT, expected_source_sha256=SOURCE_SHA256
        )
        self.assertIn(
            AuthorityFailureReason.EXCERPT_DIGEST_MISMATCH,
            verification.failure_reasons,
        )

    def test_quoted_text_mismatch_alone_fails(self) -> None:
        quotation = _quotation(quoted_text="Different wording entirely.")
        verification = verify_authority_quotation(
            quotation, source_text=SOURCE_TEXT, expected_source_sha256=SOURCE_SHA256
        )
        self.assertIn(
            AuthorityFailureReason.QUOTATION_TEXT_MISMATCH,
            verification.failure_reasons,
        )
        self.assertNotIn(
            AuthorityFailureReason.EXCERPT_DIGEST_MISMATCH,
            verification.failure_reasons,
        )

    def test_non_text_source_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            verify_authority_quotation(
                _quotation(),
                source_text=b"bytes are not text",  # type: ignore[arg-type]
                expected_source_sha256=SOURCE_SHA256,
            )


class CitationEvaluationTests(unittest.TestCase):
    def _check_reasons(self, citation: AuthorityCitation, check_id, reasons):
        evaluation = evaluate_authority_citation(
            citation,
            SCOPE,
            at=T3,
            expected_tenant_id=TENANT_ID,
            expected_matter_id=MATTER_ID,
            quotation_verifications={QUOTATION_ID: _verified_quotation()},
        )
        by_id = {check.check_id: check for check in evaluation.checks}
        self.assertEqual(
            {check.check_id for check in evaluation.checks},
            {
                AuthorityCheckId.AUTHORITY_BINDING,
                AuthorityCheckId.AUTHORITY_JURISDICTION,
                AuthorityCheckId.AUTHORITY_SCOPE,
                AuthorityCheckId.AUTHORITY_STATUS,
                AuthorityCheckId.AUTHORITY_EFFECTIVE_INTERVAL,
                AuthorityCheckId.AUTHORITY_QUOTATION,
            },
        )
        check = by_id[check_id]
        self.assertEqual(ValidationOutcome.FAILED, check.outcome)
        self.assertEqual(set(reasons), set(check.reasons))
        return evaluation

    def test_clean_citation_passes_every_check(self) -> None:
        evaluation = evaluate_authority_citation(
            _citation(),
            SCOPE,
            at=T3,
            expected_tenant_id=TENANT_ID,
            expected_matter_id=MATTER_ID,
            quotation_verifications={QUOTATION_ID: _verified_quotation()},
        )
        self.assertTrue(all(check.passed for check in evaluation.checks))

    def test_wrong_jurisdiction_fails(self) -> None:
        citation = _citation(authority=_authority(jurisdiction="US-NY"))
        evaluation = self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_JURISDICTION,
            [AuthorityFailureReason.WRONG_JURISDICTION],
        )
        self.assertEqual(CITATION_ID, evaluation.citation_id)

    def test_jurisdiction_match_survives_formatting_differences(self) -> None:
        citation = _citation(authority=_authority(jurisdiction=" us-il "))
        evaluation = evaluate_authority_citation(
            citation,
            SCOPE,
            at=T3,
            expected_tenant_id=TENANT_ID,
            expected_matter_id=MATTER_ID,
            quotation_verifications={QUOTATION_ID: _verified_quotation()},
        )
        by_id = {check.check_id: check for check in evaluation.checks}
        self.assertTrue(by_id[AuthorityCheckId.AUTHORITY_JURISDICTION].passed)

    def test_missing_scope_key_fails_scope_check(self) -> None:
        citation = _citation(asserted_scope_keys=())
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_SCOPE,
            [AuthorityFailureReason.SCOPE_KEY_MISSING],
        )

    def test_superseded_authority_fails_status_check(self) -> None:
        citation = _citation(
            authority=_authority(
                status=AuthorityStatus.SUPERSEDED, applicability_validation_id=None
            )
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_STATUS,
            [AuthorityFailureReason.SUPERSEDED_AUTHORITY],
        )

    def test_proposed_authority_is_not_verified(self) -> None:
        citation = _citation(
            authority=_authority(
                status=AuthorityStatus.PROPOSED, applicability_validation_id=None
            )
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_STATUS,
            [AuthorityFailureReason.AUTHORITY_NOT_VERIFIED],
        )

    def test_challenged_authority_requires_review(self) -> None:
        citation = _citation(
            authority=_authority(
                status=AuthorityStatus.CHALLENGED, applicability_validation_id=None
            )
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_STATUS,
            [AuthorityFailureReason.AUTHORITY_CHALLENGED],
        )

    def test_authority_marked_not_applicable_fails(self) -> None:
        citation = _citation(
            authority=_authority(status=AuthorityStatus.NOT_APPLICABLE)
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_STATUS,
            [AuthorityFailureReason.AUTHORITY_NOT_APPLICABLE],
        )

    def test_expired_effective_interval_is_stale(self) -> None:
        citation = _citation(
            authority=_authority(
                effective_interval=EffectiveInterval(valid_from=T0, valid_to=T1)
            )
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_EFFECTIVE_INTERVAL,
            [AuthorityFailureReason.AUTHORITY_NOT_EFFECTIVE],
        )

    def test_unknown_effective_interval_cannot_establish_currency(self) -> None:
        citation = _citation(
            authority=_authority(effective_interval=EffectiveInterval())
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_EFFECTIVE_INTERVAL,
            [AuthorityFailureReason.EFFECTIVE_INTERVAL_UNKNOWN],
        )

    def test_missing_quotation_fails(self) -> None:
        citation = _citation(quotations=())
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_QUOTATION,
            [AuthorityFailureReason.QUOTATION_MISSING],
        )

    def test_unverified_quotation_fails(self) -> None:
        evaluation = evaluate_authority_citation(
            _citation(),
            SCOPE,
            at=T3,
            expected_tenant_id=TENANT_ID,
            expected_matter_id=MATTER_ID,
        )
        by_id = {check.check_id: check for check in evaluation.checks}
        self.assertEqual(
            (AuthorityFailureReason.QUOTATION_UNVERIFIED,),
            by_id[AuthorityCheckId.AUTHORITY_QUOTATION].reasons,
        )

    def test_failed_quotation_verification_is_a_mismatch(self) -> None:
        failed = verify_authority_quotation(
            _quotation(),
            source_text=SOURCE_TEXT.replace("deceptive", "deceptve"),
            expected_source_sha256=SOURCE_SHA256,
        )
        evaluation = evaluate_authority_citation(
            _citation(),
            SCOPE,
            at=T3,
            expected_tenant_id=TENANT_ID,
            expected_matter_id=MATTER_ID,
            quotation_verifications={QUOTATION_ID: failed},
        )
        by_id = {check.check_id: check for check in evaluation.checks}
        self.assertIn(
            AuthorityFailureReason.QUOTATION_MISMATCH,
            by_id[AuthorityCheckId.AUTHORITY_QUOTATION].reasons,
        )

    def test_quotation_unpinned_to_authority_source_fails(self) -> None:
        citation = _citation(
            quotations=(_quotation(source_content_sha256=OTHER_SHA256),)
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_QUOTATION,
            [AuthorityFailureReason.QUOTATION_SOURCE_UNPINNED],
        )

    def test_authority_from_another_matter_crosses_scope(self) -> None:
        citation = _citation(authority=_authority(matter_id=OTHER_MATTER_ID))
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_BINDING,
            [AuthorityFailureReason.AUTHORITY_SCOPE_CROSSING],
        )

    def test_authority_from_another_tenant_crosses_scope(self) -> None:
        citation = _citation(
            authority=_authority(
                tenant_id=OTHER_TENANT_ID,
                source_reference=_reference(),
            )
        )
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_BINDING,
            [AuthorityFailureReason.AUTHORITY_SCOPE_CROSSING],
        )

    def test_quotation_bound_to_another_authority_fails_binding(self) -> None:
        citation = _citation(quotations=(_quotation(authority_id=OTHER_MATTER_ID),))
        self._check_reasons(
            citation,
            AuthorityCheckId.AUTHORITY_BINDING,
            [AuthorityFailureReason.QUOTATION_AUTHORITY_MISMATCH],
        )


class ContraryAuthoritySearchTests(unittest.TestCase):
    def test_empty_plane_search_records_no_leads(self) -> None:
        result = _empty_search()
        self.assertEqual((), result.evaluations)
        self.assertEqual((), result.unresolved_leads)
        self.assertEqual(PLANE, result.plane)
        self.assertEqual(T3, result.searched_at)

    def test_plane_binding_requires_qualification_verdict(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalPlaneBinding(
                plane_id="tenant-us-il-primary",
                route_label="embedding.serving.primary",
                embedding_model_revision="bge-m3-v1",
            )

    def test_duplicate_candidate_identifiers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique identifiers"):
            run_contrary_authority_search(
                SCOPE,
                plane=PLANE,
                candidates=(
                    _passed_candidate(),
                    _passed_candidate(),
                ),
                at=T3,
            )

    def test_wrong_jurisdiction_candidate_is_not_applicable(self) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(_passed_candidate(jurisdiction="US-NY", similarity=0.99),),
            at=T3,
        )
        evaluation = result.evaluations[0]
        self.assertEqual(ContraryLeadDisposition.NOT_APPLICABLE, evaluation.disposition)
        self.assertEqual(
            (AuthorityFailureReason.WRONG_JURISDICTION,), evaluation.reasons
        )
        self.assertNotIn(evaluation, result.unresolved_leads)

    def test_superseded_candidate_is_not_applicable(self) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(
                _passed_candidate(observed_authority_status=AuthorityStatus.SUPERSEDED),
            ),
            at=T3,
        )
        self.assertEqual(
            ContraryLeadDisposition.NOT_APPLICABLE,
            result.evaluations[0].disposition,
        )

    def test_unverified_status_candidate_stays_similarity_only(self) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(
                _passed_candidate(observed_authority_status=AuthorityStatus.PROPOSED),
            ),
            at=T3,
        )
        evaluation = result.evaluations[0]
        self.assertEqual(
            ContraryLeadDisposition.SIMILARITY_ONLY, evaluation.disposition
        )
        self.assertIn(AuthorityFailureReason.AUTHORITY_NOT_VERIFIED, evaluation.reasons)
        self.assertIn(evaluation, result.similarity_only_leads)
        self.assertIn(evaluation, result.unresolved_leads)

    def test_candidate_without_citation_or_jurisdiction_stays_similarity_only(
        self,
    ) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(
                ContrarySearchCandidate(candidate_id="cand-x", similarity=1.0),
            ),
            at=T3,
        )
        evaluation = result.evaluations[0]
        self.assertEqual(
            ContraryLeadDisposition.SIMILARITY_ONLY, evaluation.disposition
        )
        self.assertIn(AuthorityFailureReason.CITATION_MISSING, evaluation.reasons)
        self.assertIn(AuthorityFailureReason.JURISDICTION_MISSING, evaluation.reasons)

    def test_candidate_with_failed_quotation_stays_similarity_only(self) -> None:
        failed = verify_authority_quotation(
            _quotation(),
            source_text=SOURCE_TEXT.replace("Section", "Sektion"),
            expected_source_sha256=SOURCE_SHA256,
        )
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(_passed_candidate(quotation_verification=failed),),
            at=T3,
        )
        evaluation = result.evaluations[0]
        self.assertEqual(
            ContraryLeadDisposition.SIMILARITY_ONLY, evaluation.disposition
        )
        self.assertIn(AuthorityFailureReason.QUOTATION_MISMATCH, evaluation.reasons)

    def test_candidate_with_missing_scope_key_stays_similarity_only(self) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(_passed_candidate(asserted_scope_keys=()),),
            at=T3,
        )
        evaluation = result.evaluations[0]
        self.assertEqual(
            ContraryLeadDisposition.SIMILARITY_ONLY, evaluation.disposition
        )
        self.assertIn(AuthorityFailureReason.SCOPE_KEY_MISSING, evaluation.reasons)

    def test_verified_candidate_becomes_a_qualified_lead(self) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(_passed_candidate(similarity=0.01),),
            at=T3,
        )
        evaluation = result.evaluations[0]
        self.assertEqual(ContraryLeadDisposition.QUALIFIED_LEAD, evaluation.disposition)
        self.assertEqual((), evaluation.reasons)
        self.assertIn(evaluation, result.qualified_leads)
        self.assertIn(evaluation, result.unresolved_leads)

    def test_naive_evaluation_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_contrary_authority_search(
                SCOPE, plane=PLANE, candidates=(), at=datetime(2026, 1, 1)
            )


class SimilarityNeverQualifiesTests(unittest.TestCase):
    """Acceptance: a similarity score alone never qualifies authority."""

    def test_maximum_similarity_without_evidence_never_qualifies(self) -> None:
        result = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(
                ContrarySearchCandidate(candidate_id="perfect", similarity=1.0),
                _passed_candidate(candidate_id="weak", similarity=0.0),
            ),
            at=T3,
        )
        by_candidate = {
            evaluation.candidate_id: evaluation for evaluation in result.evaluations
        }
        self.assertEqual(
            ContraryLeadDisposition.SIMILARITY_ONLY,
            by_candidate["perfect"].disposition,
        )
        self.assertEqual(
            ContraryLeadDisposition.QUALIFIED_LEAD, by_candidate["weak"].disposition
        )
        self.assertEqual(
            ("weak",),
            tuple(lead.candidate_id for lead in result.qualified_leads),
        )

    def test_similarity_only_lead_fails_the_support_gate(self) -> None:
        search = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(
                ContrarySearchCandidate(candidate_id="perfect", similarity=0.999),
            ),
            at=T3,
        )
        verification = verify_authority_support(
            claim=_ledger_claim(),
            scope=SCOPE,
            citations=(_citation(),),
            quotation_verifications={QUOTATION_ID: _verified_quotation()},
            remedies=(_remedy(),),
            contrary_search=search,
            at=T3,
        )
        self.assertEqual(ValidationOutcome.FAILED, verification.outcome)
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.CONTRARY_LEADS_REQUIRE_REVIEW,),
            failed[AuthorityCheckId.CONTRARY_LEADS_REVIEWED].reasons,
        )


class AuthoritySupportVerificationTests(unittest.TestCase):
    def _verify(self, **overrides: object):
        payload: dict[str, object] = {
            "claim": _ledger_claim(),
            "scope": SCOPE,
            "citations": (_citation(),),
            "quotation_verifications": {QUOTATION_ID: _verified_quotation()},
            "remedies": (_remedy(),),
            "contrary_search": _empty_search(),
            "at": T3,
        }
        payload.update(overrides)
        return verify_authority_support(**payload)

    def test_supported_claim_passes_every_gate(self) -> None:
        verification = self._verify()
        self.assertEqual(ValidationOutcome.PASSED, verification.outcome)
        self.assertEqual((), verification.failed_checks)
        self.assertEqual(CLAIM_ID, verification.claim_id)
        self.assertEqual(TENANT_ID, verification.tenant_id)
        self.assertEqual(MATTER_ID, verification.matter_id)
        self.assertEqual(T3, verification.evaluated_at)
        expected_checks = {
            AuthorityCheckId.CITATION_PRESENT,
            AuthorityCheckId.AUTHORITY_BINDING,
            AuthorityCheckId.AUTHORITY_JURISDICTION,
            AuthorityCheckId.AUTHORITY_SCOPE,
            AuthorityCheckId.AUTHORITY_STATUS,
            AuthorityCheckId.AUTHORITY_EFFECTIVE_INTERVAL,
            AuthorityCheckId.AUTHORITY_QUOTATION,
            AuthorityCheckId.CONTRARY_SEARCH_EXECUTED,
            AuthorityCheckId.CONTRARY_LEADS_REVIEWED,
            AuthorityCheckId.REMEDY_PRESENT,
            AuthorityCheckId.REMEDY_AUTHORITY,
        }
        self.assertEqual(
            expected_checks, {check.check_id for check in verification.checks}
        )

    def test_wrong_jurisdiction_fails_the_gate(self) -> None:
        verification = self._verify(
            citations=(_citation(authority=_authority(jurisdiction="US-NY")),)
        )
        self.assertEqual(ValidationOutcome.FAILED, verification.outcome)
        failed = verification.failed_checks
        self.assertEqual(
            (AuthorityCheckId.AUTHORITY_JURISDICTION,),
            tuple(check.check_id for check in failed),
        )
        self.assertEqual(
            (AuthorityFailureReason.WRONG_JURISDICTION,), failed[0].reasons
        )
        self.assertEqual(CITATION_ID, failed[0].subject_id)

    def test_stale_superseded_authority_fails_the_gate(self) -> None:
        verification = self._verify(
            citations=(
                _citation(
                    authority=_authority(
                        status=AuthorityStatus.SUPERSEDED,
                        applicability_validation_id=None,
                    )
                ),
            )
        )
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.SUPERSEDED_AUTHORITY,),
            failed[AuthorityCheckId.AUTHORITY_STATUS].reasons,
        )

    def test_expired_authority_fails_the_gate(self) -> None:
        verification = self._verify(
            citations=(
                _citation(
                    authority=_authority(
                        effective_interval=EffectiveInterval(valid_from=T0, valid_to=T2)
                    )
                ),
            )
        )
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.AUTHORITY_NOT_EFFECTIVE,),
            failed[AuthorityCheckId.AUTHORITY_EFFECTIVE_INTERVAL].reasons,
        )

    def test_quotation_mismatch_fails_the_gate(self) -> None:
        mismatch = verify_authority_quotation(
            _quotation(),
            source_text=SOURCE_TEXT.replace("trade or commerce", "trasde or commerce"),
            expected_source_sha256=SOURCE_SHA256,
        )
        verification = self._verify(quotation_verifications={QUOTATION_ID: mismatch})
        self.assertEqual(ValidationOutcome.FAILED, verification.outcome)
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertIn(
            AuthorityFailureReason.QUOTATION_MISMATCH,
            failed[AuthorityCheckId.AUTHORITY_QUOTATION].reasons,
        )
        self.assertIn(
            AuthorityFailureReason.SOURCE_DIGEST_MISMATCH, mismatch.failure_reasons
        )

    def test_missing_remedy_fails_the_gate(self) -> None:
        verification = self._verify(remedies=())
        self.assertEqual(ValidationOutcome.FAILED, verification.outcome)
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.REMEDY_MISSING,),
            failed[AuthorityCheckId.REMEDY_PRESENT].reasons,
        )

    def test_remedy_without_authority_support_fails_the_gate(self) -> None:
        verification = self._verify(remedies=(_remedy(authority_ids=()),))
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.REMEDY_AUTHORITY_MISSING,),
            failed[AuthorityCheckId.REMEDY_AUTHORITY].reasons,
        )

    def test_remedy_from_another_claim_crosses_scope(self) -> None:
        verification = self._verify(
            remedies=(
                _remedy(
                    claim_id=UUID("10000000-0000-4000-8000-000000000499"),
                ),
            )
        )
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertIn(
            AuthorityFailureReason.REMEDY_SCOPE_CROSSING,
            failed[AuthorityCheckId.REMEDY_AUTHORITY].reasons,
        )

    def test_missing_contrary_search_fails_closed(self) -> None:
        verification = self._verify(contrary_search=None)
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.CONTRARY_SEARCH_MISSING,),
            failed[AuthorityCheckId.CONTRARY_SEARCH_EXECUTED].reasons,
        )

    def test_qualified_contrary_lead_still_requires_review(self) -> None:
        search = run_contrary_authority_search(
            SCOPE,
            plane=PLANE,
            candidates=(_passed_candidate(),),
            at=T3,
        )
        verification = self._verify(contrary_search=search)
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.CONTRARY_LEADS_REQUIRE_REVIEW,),
            failed[AuthorityCheckId.CONTRARY_LEADS_REVIEWED].reasons,
        )

    def test_no_authority_citation_fails_the_gate(self) -> None:
        verification = self._verify(citations=())
        failed = {check.check_id: check for check in verification.failed_checks}
        self.assertEqual(
            (AuthorityFailureReason.AUTHORITY_CITATION_MISSING,),
            failed[AuthorityCheckId.CITATION_PRESENT].reasons,
        )

    def test_duplicate_citation_identifiers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique identifiers"):
            self._verify(citations=(_citation(), _citation()))

    def test_future_contrary_search_is_rejected(self) -> None:
        future = _empty_search(at=T3 + timedelta(minutes=5))
        with self.assertRaisesRegex(ValueError, "postdate"):
            self._verify(contrary_search=future)

    def test_verification_is_deterministic(self) -> None:
        self.assertEqual(self._verify(), self._verify())

    def test_passed_verification_cannot_hide_a_failed_check(self) -> None:
        failed_check = SupportCheck(
            check_id=AuthorityCheckId.CITATION_PRESENT,
            subject_id=None,
            outcome=ValidationOutcome.FAILED,
            reasons=(AuthorityFailureReason.AUTHORITY_CITATION_MISSING,),
        )
        with self.assertRaises(ValidationError):
            AuthoritySupportVerification(
                claim_id=CLAIM_ID,
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                evaluated_at=T3,
                checks=(failed_check,),
                outcome=ValidationOutcome.PASSED,
            )

    def test_naive_evaluation_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._verify(at=datetime(2026, 1, 1, 12, 0, 0))


if __name__ == "__main__":
    unittest.main()
