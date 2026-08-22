"""Claim-grounded drafting domain tests (SKL-S4-04B).

Covers the matter Documents tab editor contracts: deterministic sentence
splitting, claim ledger grounding with ungrounded-sentence warnings,
bracketed-unknown rendering segments, sentence-key version compare, and
changed-after-approval invalidation. All fixtures are synthetic.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import (
    ArtifactBinding,
    ClaimSupport,
    ClaimSupportKind,
    DomainTransitionError,
    DraftCompareRow,
    DraftGroundingStatus,
    DraftSegment,
    LedgerClaim,
    LedgerClaimStatus,
    SentenceGrounding,
    SourceReference,
    WorkProduct,
    WorkProductStatus,
    WorkProductVersion,
    WorkProductVersionStatus,
    applicable_groundings,
    approval_invalidation_state,
    assert_approval_matches_version,
    assert_draft_grounded,
    build_successor_version,
    compare_draft_versions,
    evaluate_draft_ready_gate,
    evaluate_sentence_grounding,
    extract_unknown_occurrences,
    grounding_claim_ids,
    grounding_defects,
    invalidate_approval_after_edit,
    render_draft_segments,
    sentence_key,
    split_draft_sentences,
    stale_groundings,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(seconds=1)


def uid(value: int) -> UUID:
    return UUID(int=(1 << 127) | value)


def sha(value: str = "a") -> str:
    return value * 64


def audit(identifier: int) -> dict[str, object]:
    return {"id": uid(identifier), "created_at": T0, "updated_at": T0}


def scoped(identifier: int) -> dict[str, object]:
    return {**audit(identifier), "tenant_id": uid(1), "matter_id": uid(4)}


def work_product_version(
    identifier: int = 101,
    *,
    number: int = 1,
    digest: str = "a",
    work_product_id: UUID = uid(100),
    status: WorkProductVersionStatus = WorkProductVersionStatus.DRAFT,
) -> WorkProductVersion:
    artifact = WorkProductVersion(
        **scoped(identifier),
        work_product_id=work_product_id,
        version_number=number,
        content_sha256=sha(digest),
        source_artifact_id=uid(600),
    )
    if status is not WorkProductVersionStatus.DRAFT:
        artifact = artifact.transition_to(status, at=T0 + STEP)
    return artifact


def binding_for(artifact: WorkProductVersion) -> ArtifactBinding:
    return ArtifactBinding(
        artifact_id=artifact.id,
        artifact_version=artifact.version_number,
        content_sha256=artifact.content_sha256,
    )


def support(claim_id: UUID) -> ClaimSupport:
    return ClaimSupport(
        **scoped(910),
        claim_id=claim_id,
        kind=ClaimSupportKind.SUPPORT,
        source_reference=SourceReference(
            source_reference_id=uid(911),
            source_system="synthetic-store",
            source_version="1",
            content_sha256=sha("e"),
            locator="synthetic/evidence/record-1",
            observed_at=T0,
        ),
        span_start=0,
        span_end=40,
        excerpt_sha256=sha("f"),
        recorded_by_principal_id=uid(700),
        policy_revision=sha("9"),
    )


def ledger_claim(
    claim_id: UUID,
    *,
    status: LedgerClaimStatus = LedgerClaimStatus.SUPPORTED,
    statement: str = "The vehicle failed to perform as warranted.",
) -> LedgerClaim:
    claim = LedgerClaim(
        **scoped_from(claim_id, claim_id),
        statement=statement,
        policy_revision=sha("9"),
        support=(support(claim_id),),
    )
    if status is LedgerClaimStatus.SUPPORTED:
        claim = claim.transition_to(
            LedgerClaimStatus.UNDER_REVIEW, at=T0 + STEP
        ).transition_to(LedgerClaimStatus.SUPPORTED, at=T0 + 2 * STEP)
    elif status is LedgerClaimStatus.WITHDRAWN:
        claim = claim.transition_to(LedgerClaimStatus.WITHDRAWN, at=T0 + STEP)
    return claim


def scoped_from(identifier: UUID, _: UUID) -> dict[str, object]:
    payload = scoped(identifier.int & 0xFFFFFFFF)
    payload["id"] = identifier
    return payload


def grounding_for(
    artifact: WorkProductVersion,
    key: str,
    claim_id: UUID,
    *,
    tenant_id: UUID | None = None,
    matter_id: UUID | None = None,
) -> SentenceGrounding:
    payload = {**audit(950), "tenant_id": tenant_id, "matter_id": matter_id}
    if tenant_id is None:
        payload["tenant_id"] = artifact.tenant_id
    if matter_id is None:
        payload["matter_id"] = artifact.matter_id
    return SentenceGrounding(
        **payload,
        version_binding=binding_for(artifact),
        sentence_key=key,
        claim_id=claim_id,
    )


class SentenceSplittingTests(unittest.TestCase):
    def test_sentences_split_in_document_order_with_exact_spans(self) -> None:
        content = "Alpha one. Beta two! Gamma three?"
        sentences = split_draft_sentences(content)
        self.assertEqual(
            ("Alpha one.", "Beta two!", "Gamma three?"),
            tuple(sentence.text for sentence in sentences),
        )
        for sentence in sentences:
            self.assertEqual(sentence.text, content[sentence.start : sentence.end])

    def test_equal_sentences_share_one_identity_key(self) -> None:
        first = split_draft_sentences("Alpha   one.")[0]
        self.assertEqual(first.key, sentence_key("Alpha\n one."))
        self.assertEqual(sentence_key("Alpha one."), first.key)

    def test_terminators_inside_unknown_markers_do_not_cut_sentences(self) -> None:
        content = "On [?purchase_date: unresolved tension] the vehicle failed. Next."
        sentences = split_draft_sentences(content)
        self.assertEqual(
            (
                "On [?purchase_date: unresolved tension] the vehicle failed.",
                "Next.",
            ),
            tuple(sentence.text for sentence in sentences),
        )

    def test_newlines_and_headings_stay_separate_segments(self) -> None:
        content = "Intro heading\nAlpha one.\n\nBeta two."
        sentences = split_draft_sentences(content)
        self.assertEqual(
            ("Intro heading", "Alpha one.", "Beta two."),
            tuple(sentence.text for sentence in sentences),
        )

    def test_empty_content_yields_no_sentences(self) -> None:
        self.assertEqual((), split_draft_sentences("   \n  "))

    def test_splitting_rejects_non_text(self) -> None:
        with self.assertRaises(TypeError):
            split_draft_sentences(42)  # type: ignore[arg-type]


class GroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact = work_product_version()
        self.claim = ledger_claim(uid(800))
        self.content = (
            "Intro heading\n"
            "On [?purchase_date: unresolved tension] the vehicle failed. "
            "The engine stalled twice. "
            "See warranty terms [not a marker] in full."
        )
        self.failed = split_draft_sentences(self.content)[1]
        self.stalled = split_draft_sentences(self.content)[2]

    def test_grounded_sentence_evaluates_with_no_warning(self) -> None:
        groundings = (grounding_for(self.artifact, self.stalled.key, self.claim.id),)
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=groundings,
            ledger_claims=(self.claim,),
        )
        stalled = next(e for e in evaluations if e.sentence.key == self.stalled.key)
        self.assertIs(DraftGroundingStatus.GROUNDED, stalled.status)
        self.assertIsNone(stalled.warning)
        self.assertEqual(self.claim.id, stalled.grounding.claim_id)
        self.assertIs(LedgerClaimStatus.SUPPORTED, stalled.claim_status)

    def test_ungrounded_sentence_renders_its_warning(self) -> None:
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=(),
            ledger_claims=(self.claim,),
        )
        ungrounded = [
            e for e in evaluations if e.status is DraftGroundingStatus.UNGROUNDED
        ]
        self.assertEqual(2, len(ungrounded))
        for evaluation in ungrounded:
            self.assertIn("not grounded", evaluation.warning)
        self.assertEqual(
            (
                "The engine stalled twice.",
                "See warranty terms [not a marker] in full.",
            ),
            tuple(e.sentence.text for e in ungrounded),
        )

    def test_unknown_sentence_defers_grounding_with_its_own_warning(self) -> None:
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=(),
            ledger_claims=(self.claim,),
        )
        deferred = [e for e in evaluations if e.sentence.contains_unknown()]
        self.assertEqual(1, len(deferred))
        self.assertIs(DraftGroundingStatus.DEFERRED_UNKNOWN, deferred[0].status)
        self.assertIn("bracketed unknown", deferred[0].warning)

    def test_withdrawn_claim_grounding_warns(self) -> None:
        withdrawn = ledger_claim(uid(801), status=LedgerClaimStatus.WITHDRAWN)
        groundings = (grounding_for(self.artifact, self.stalled.key, withdrawn.id),)
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=groundings,
            ledger_claims=(withdrawn,),
        )
        stalled = next(e for e in evaluations if e.sentence.key == self.stalled.key)
        self.assertIs(DraftGroundingStatus.CLAIM_WITHDRAWN, stalled.status)
        self.assertIn("withdrawn", stalled.warning)
        self.assertTrue(stalled.is_defect)

    def test_missing_ledger_claim_grounding_warns(self) -> None:
        groundings = (grounding_for(self.artifact, self.stalled.key, uid(802)),)
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=groundings,
            ledger_claims=(self.claim,),
        )
        stalled = next(e for e in evaluations if e.sentence.key == self.stalled.key)
        self.assertIs(DraftGroundingStatus.CLAIM_MISSING, stalled.status)
        self.assertTrue(stalled.is_defect)

    def test_grounding_binds_only_its_exact_version_triple(self) -> None:
        edited = work_product_version(
            102, number=1, digest="b", work_product_id=uid(100)
        )
        other_work_product = work_product_version(
            103, digest="a", work_product_id=uid(105)
        )
        groundings = (
            grounding_for(self.artifact, self.stalled.key, self.claim.id),
            grounding_for(edited, sentence_key("irrelevant"), self.claim.id),
            grounding_for(
                other_work_product, sentence_key("irrelevant"), self.claim.id
            ),
        )
        self.assertEqual(1, len(applicable_groundings(self.artifact, groundings)))
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=groundings,
            ledger_claims=(self.claim,),
        )
        self.assertEqual(
            1,
            sum(1 for e in evaluations if e.status is DraftGroundingStatus.GROUNDED),
        )

    def test_cross_scope_grounding_reports_missing_claim(self) -> None:
        foreign_claim_id = uid(803)
        foreign_support = support(foreign_claim_id)
        foreign_support = ClaimSupport.model_validate(
            {
                **foreign_support.model_dump(mode="python"),
                "tenant_id": uid(99),
                "matter_id": uid(98),
            },
            strict=True,
        )
        foreign = LedgerClaim(
            **{
                **scoped_from(foreign_claim_id, foreign_claim_id),
                "tenant_id": uid(99),
                "matter_id": uid(98),
            },
            statement="Foreign synthetic claim.",
            policy_revision=sha("9"),
            support=(foreign_support,),
        )
        groundings = (grounding_for(self.artifact, self.stalled.key, foreign.id),)
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=groundings,
            ledger_claims=(foreign,),
        )
        stalled = next(e for e in evaluations if e.sentence.key == self.stalled.key)
        self.assertIs(DraftGroundingStatus.CLAIM_MISSING, stalled.status)

    def test_contradictory_groundings_for_one_sentence_are_rejected(self) -> None:
        other = ledger_claim(uid(804))
        groundings = (
            grounding_for(self.artifact, self.stalled.key, self.claim.id),
            grounding_for(self.artifact, self.stalled.key, other.id),
        )
        with self.assertRaises(ValueError):
            evaluate_sentence_grounding(
                version=self.artifact,
                content=self.content,
                groundings=groundings,
                ledger_claims=(self.claim, other),
            )

    def test_duplicate_ledger_records_for_one_claim_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_sentence_grounding(
                version=self.artifact,
                content=self.content,
                groundings=(),
                ledger_claims=(self.claim, self.claim),
            )

    def test_grounding_defects_collect_only_blocking_evaluations(self) -> None:
        evaluations = evaluate_sentence_grounding(
            version=self.artifact,
            content=self.content,
            groundings=(),
            ledger_claims=(self.claim,),
        )
        defects = grounding_defects(evaluations)
        self.assertEqual(2, len(defects))
        self.assertTrue(all(defect.is_defect for defect in defects))

    def test_stale_groundings_surface_removed_sentences(self) -> None:
        groundings = (grounding_for(self.artifact, self.stalled.key, self.claim.id),)
        edited_content = "Intro heading\nOnly this sentence remains."
        self.assertEqual(
            (self.stalled.key,),
            tuple(
                g.sentence_key
                for g in stale_groundings(
                    version=self.artifact,
                    content=edited_content,
                    groundings=groundings,
                )
            ),
        )

    def test_grounding_claim_ids_deduplicate_in_document_order(self) -> None:
        other = ledger_claim(uid(805))
        groundings = (
            grounding_for(self.artifact, self.stalled.key, self.claim.id),
            grounding_for(self.artifact, self.failed.key, self.claim.id),
            grounding_for(self.artifact, sentence_key("removed"), other.id),
        )
        self.assertEqual(
            (self.claim.id,),
            grounding_claim_ids(
                version=self.artifact,
                content=self.content,
                groundings=groundings,
            ),
        )

    def test_assert_draft_grounded_fails_closed_on_defects(self) -> None:
        with self.assertRaises(DomainTransitionError) as raised:
            assert_draft_grounded(
                version=self.artifact,
                content=self.content,
                groundings=(),
                ledger_claims=(self.claim,),
            )
        self.assertIn("grounding defects", str(raised.exception))

    def test_assert_draft_grounded_fails_closed_on_stale_bindings(self) -> None:
        groundings = (
            grounding_for(self.artifact, sentence_key("gone"), self.claim.id),
        )
        with self.assertRaises(DomainTransitionError) as raised:
            assert_draft_grounded(
                version=self.artifact,
                content=self.content,
                groundings=groundings,
                ledger_claims=(self.claim,),
            )
        self.assertIn("stale groundings", str(raised.exception))

    def test_assert_draft_grounded_passes_when_grounded_and_no_stale(self) -> None:
        groundings = (
            grounding_for(self.artifact, self.stalled.key, self.claim.id),
            grounding_for(self.artifact, sentence_key("Intro heading"), self.claim.id),
            grounding_for(
                self.artifact,
                sentence_key("See warranty terms [not a marker] in full."),
                self.claim.id,
            ),
        )
        assert_draft_grounded(
            version=self.artifact,
            content=self.content,
            groundings=groundings,
            ledger_claims=(self.claim,),
        )

    def test_ungrounded_draft_blocks_draft_ready_through_missing_grounding(
        self,
    ) -> None:
        frozen = work_product_version(status=WorkProductVersionStatus.FROZEN)
        product = WorkProduct(
            **scoped(100),
            title="Synthetic demand letter",
            work_product_kind="letter",
            current_version_id=frozen.id,
        )
        gate = evaluate_draft_ready_gate(
            work_product=product,
            version=frozen,
            unknowns=(),
            grounding_claim_ids=grounding_claim_ids(
                version=frozen, content=self.content, groundings=()
            ),
            claim_gates=(),
            at=T0,
        )
        from sklegal_domain import GateCheckId, GateFailureReason, ValidationOutcome

        self.assertIs(ValidationOutcome.FAILED, gate.outcome)
        claims_check = next(
            c for c in gate.checks if c.check_id is GateCheckId.DRAFT_CLAIMS_READY
        )
        self.assertIs(ValidationOutcome.FAILED, claims_check.outcome)
        self.assertIn(GateFailureReason.GROUNDING_MISSING, claims_check.reasons)


class RenderSegmentsTests(unittest.TestCase):
    def test_unknowns_render_as_first_class_segments(self) -> None:
        content = "On [?purchase_date: unresolved tension] the vehicle failed."
        segments = render_draft_segments(content)
        self.assertEqual(3, len(segments))
        unknown = segments[1]
        self.assertEqual("unknown", unknown.kind)
        self.assertEqual("[?purchase_date: unresolved tension]", unknown.text)
        self.assertEqual("purchase_date", unknown.unknown.placeholder_key)
        self.assertEqual(
            "unresolved tension",
            unknown.unknown.hint,
        )

    def test_plain_brackets_never_render_as_unknown_segments(self) -> None:
        content = "See [not a marker] and [also not] markers."
        segments = render_draft_segments(content)
        self.assertEqual(1, len(segments))
        self.assertEqual("text", segments[0].kind)
        self.assertEqual(0, len(extract_unknown_occurrences(content)))

    def test_segments_cover_the_whole_content_in_order(self) -> None:
        content = "Intro [?a] middle [?b] tail."
        segments = render_draft_segments(content)
        kinds = tuple(segment.kind for segment in segments)
        self.assertEqual(("text", "unknown", "text", "unknown", "text"), kinds)

    def test_unknown_segment_must_carry_its_occurrence(self) -> None:
        with self.assertRaises(ValidationError):
            DraftSegment(kind="unknown", text="[?a]")

    def test_text_segment_rejects_an_occurrence(self) -> None:
        occurrence = extract_unknown_occurrences("[?a]")[0]
        with self.assertRaises(ValidationError):
            DraftSegment(kind="text", text="hello", unknown=occurrence)

    def test_rendering_rejects_non_text(self) -> None:
        with self.assertRaises(TypeError):
            render_draft_segments(None)  # type: ignore[arg-type]


class VersionCompareTests(unittest.TestCase):
    def test_compare_marks_unchanged_added_and_removed_rows(self) -> None:
        previous = "Alpha one. Beta two. Gamma three."
        current = "Alpha one. Beta two revised. Gamma three. Delta four."
        rows = compare_draft_versions(previous, current)
        self.assertEqual(
            (
                ("unchanged", "Alpha one."),
                ("removed", "Beta two."),
                ("added", "Beta two revised."),
                ("unchanged", "Gamma three."),
                ("added", "Delta four."),
            ),
            tuple((row.change, row.sentence.text) for row in rows),
        )

    def test_row_texts_come_from_their_exact_source_version(self) -> None:
        rows = compare_draft_versions("Alpha one.", "Alpha one revised.")
        removed, added = rows
        self.assertIsNone(removed.current_text)
        self.assertEqual("Alpha one.", removed.previous_text)
        self.assertIsNone(added.previous_text)
        self.assertEqual("Alpha one revised.", added.current_text)

    def test_insertion_at_the_top_leaves_later_sentences_unchanged(self) -> None:
        previous = "Alpha one. Beta two. Gamma three."
        current = "New opening. Alpha one. Beta two. Gamma three."
        rows = compare_draft_versions(previous, current)
        self.assertEqual(
            (("added", "New opening."),)
            + tuple(
                ("unchanged", text)
                for text in ("Alpha one.", "Beta two.", "Gamma three.")
            ),
            tuple((row.change, row.sentence.text) for row in rows),
        )

    def test_removal_of_every_sentence_reports_all_removed(self) -> None:
        rows = compare_draft_versions("Alpha one. Beta two.", "")
        self.assertEqual(
            (("removed", "Alpha one."), ("removed", "Beta two.")),
            tuple((row.change, row.sentence.text) for row in rows),
        )

    def test_identical_versions_compare_all_unchanged(self) -> None:
        content = "Alpha one. Beta two."
        rows = compare_draft_versions(content, content)
        self.assertEqual(
            (("unchanged", "Alpha one."), ("unchanged", "Beta two.")),
            tuple((row.change, row.sentence.text) for row in rows),
        )
        self.assertEqual(DraftCompareRow, type(rows[0]))


class ApprovalInvalidationTests(unittest.TestCase):
    def _approved_product(self, approved: WorkProductVersion) -> WorkProduct:
        product = WorkProduct(
            **scoped(100),
            title="Synthetic demand letter",
            work_product_kind="letter",
            current_version_id=approved.id,
        )
        return (
            product.transition_to(WorkProductStatus.IN_REVIEW, at=T0 + STEP)
            .transition_to(
                WorkProductStatus.VALIDATED,
                at=T0 + 2 * STEP,
                validation_result_id=uid(650),
            )
            .transition_to(
                WorkProductStatus.APPROVED,
                at=T0 + 3 * STEP,
                approval_id=uid(660),
            )
        )

    def setUp(self) -> None:
        self.approved = work_product_version(status=WorkProductVersionStatus.FROZEN)
        self.product = self._approved_product(self.approved)
        self.approval_subject = binding_for(self.approved)

    def _successor(self, digest: str = "b") -> WorkProductVersion:
        successor = build_successor_version(
            self.product,
            self.approved,
            version_id=uid(102),
            content_sha256=sha(digest),
            source_artifact_id=uid(601),
            at=T0 + 4 * STEP,
        )
        return successor

    def test_edit_after_approval_resets_gates_and_supersedes(self) -> None:
        successor = self._successor()
        reset, superseded = invalidate_approval_after_edit(
            work_product=self.product,
            approved_version=self.approved,
            approval_subject=self.approval_subject,
            successor=successor,
            at=T0 + 5 * STEP,
        )
        self.assertIs(WorkProductStatus.IN_REVIEW, reset.status)
        self.assertIsNone(reset.validation_result_id)
        self.assertIsNone(reset.approval_id)
        self.assertEqual(successor.id, reset.current_version_id)
        self.assertIs(WorkProductVersionStatus.SUPERSEDED, superseded.status)
        self.assertEqual(self.approved.id, superseded.id)

    def test_invalidating_a_reset_work_product_fails(self) -> None:
        successor = self._successor()
        with self.assertRaises(DomainTransitionError):
            invalidate_approval_after_edit(
                work_product=WorkProduct(
                    **scoped(100),
                    title="Synthetic demand letter",
                    work_product_kind="letter",
                    current_version_id=self.approved.id,
                ),
                approved_version=self.approved,
                approval_subject=self.approval_subject,
                successor=successor,
                at=T0 + 5 * STEP,
            )

    def test_approval_that_never_matched_is_rejected(self) -> None:
        successor = self._successor()
        mismatched = ArtifactBinding(
            artifact_id=self.approved.id,
            artifact_version=self.approved.version_number,
            content_sha256=sha("b"),
        )
        with self.assertRaises(DomainTransitionError) as raised:
            invalidate_approval_after_edit(
                work_product=self.product,
                approved_version=self.approved,
                approval_subject=mismatched,
                successor=successor,
                at=T0 + 5 * STEP,
            )
        self.assertIn("content changed after approval", str(raised.exception))
        with self.assertRaises(DomainTransitionError):
            assert_approval_matches_version(mismatched, self.approved)

    def test_unchanged_successor_digest_is_rejected(self) -> None:
        successor = work_product_version(
            identifier=102,
            number=2,
            digest="a",
            work_product_id=self.approved.work_product_id,
        )
        with self.assertRaises(DomainTransitionError) as raised:
            invalidate_approval_after_edit(
                work_product=self.product,
                approved_version=self.approved,
                approval_subject=self.approval_subject,
                successor=successor,
                at=T0 + 5 * STEP,
            )
        self.assertIn("must change the exact content digest", str(raised.exception))

    def test_frozen_successor_is_rejected(self) -> None:
        successor = self._successor().transition_to(
            WorkProductVersionStatus.FROZEN, at=T0 + 6 * STEP
        )
        with self.assertRaises(DomainTransitionError):
            invalidate_approval_after_edit(
                work_product=self.product,
                approved_version=self.approved,
                approval_subject=self.approval_subject,
                successor=successor,
                at=T0 + 7 * STEP,
            )

    def test_read_side_detection_of_changed_after_approval(self) -> None:
        successor = self._successor()
        self.assertFalse(
            approval_invalidation_state(
                approval_subject=None, current_version=self.approved
            )
        )
        self.assertFalse(
            approval_invalidation_state(
                approval_subject=self.approval_subject,
                current_version=self.approved,
            )
        )
        self.assertTrue(
            approval_invalidation_state(
                approval_subject=self.approval_subject,
                current_version=successor,
            )
        )


if __name__ == "__main__":
    unittest.main()
