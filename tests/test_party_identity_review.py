from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sklegal_policies import (
    ConflictDisposition,
    ConflictService,
    ExternalIdentifier,
    IdentityCandidateProposal,
    IdentityContext,
    IdentityProvenance,
    IdentityResolutionService,
    IdentityReviewDecision,
    IdentityReviewOutcome,
    KnownPartyAssociation,
    LabeledIdentityPair,
    MatchUncertainty,
    PartyCandidate,
    PartyObservation,
    PartyRelationship,
    SourceBackedAlias,
    evaluate_candidate_quality,
)

T0 = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("b3000000-0000-4000-8000-000000000001")
OTHER_TENANT_ID = UUID("b3000000-0000-4000-8000-000000000099")
MATTER_ID = UUID("b3000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("b3000000-0000-4000-8000-000000000003")


def _provenance(record_id: str) -> IdentityProvenance:
    return IdentityProvenance(
        source_system="synthetic-registry",
        source_record_id=record_id,
        content_sha256="d" * 64,
        retrieved_at=T0,
    )


def _observation(
    *,
    name: str,
    party_kind: str = "company",
    jurisdiction: str | None = "us-ca",
    tenant_id: UUID = TENANT_ID,
    aliases: tuple[SourceBackedAlias, ...] = (),
    external_identifiers: tuple[ExternalIdentifier, ...] = (),
    sources_available: bool = True,
) -> PartyObservation:
    return PartyObservation(
        party_id=uuid4(),
        tenant_id=tenant_id,
        display_name=name,
        context=IdentityContext(entity_kind=party_kind, jurisdiction=jurisdiction),
        aliases=aliases,
        external_identifiers=external_identifiers,
        provenance=(_provenance(f"rec-{uuid4().hex[:8]}"),),
        sources_available=sources_available,
    )


def _identifier(scheme: str, value: str) -> ExternalIdentifier:
    return ExternalIdentifier(
        scheme=scheme,
        value=value,
        license_basis="public-registry",
        provenance=_provenance(f"ext-{uuid4().hex[:8]}"),
    )


def _decision(
    *,
    proposal_id: UUID,
    outcome: IdentityReviewOutcome,
    version: int = 1,
    supersedes: UUID | None = None,
    decided_at: datetime = T0,
    tenant_id: UUID = TENANT_ID,
) -> IdentityReviewDecision:
    return IdentityReviewDecision(
        decision_id=uuid4(),
        proposal_id=proposal_id,
        tenant_id=tenant_id,
        outcome=outcome,
        rationale="synthetic reviewer rationale",
        decided_by_principal_id=PRINCIPAL_ID,
        decided_at=decided_at,
        decision_version=version,
        supersedes_decision_id=supersedes,
    )


class PartyIdentityProposalTests(unittest.TestCase):
    def test_proposal_is_explainable_deterministic_and_side_effect_free(self) -> None:
        service = IdentityResolutionService()
        subject = _observation(
            name="Synthetic Alpha Holdings, LLC",
            external_identifiers=(_identifier("lei", "SYNTH-LEI-0001"),),
        )
        target = _observation(
            name="Synthetic Alpha Holdings",
            aliases=(
                SourceBackedAlias(
                    display_name="Synthetic Alpha Holdings LLC",
                    provenance=_provenance("alias-1"),
                ),
            ),
            external_identifiers=(_identifier("lei", "SYNTH-LEI-0001"),),
        )
        proposal = service.propose(
            proposal_id=uuid4(), subject=subject, target=target, proposed_at=T0
        )
        self.assertEqual("propose_possible_match", proposal.effect)
        self.assertEqual("features", proposal.uncertainty.basis)
        self.assertGreater(proposal.uncertainty.score, 0.9)
        self.assertEqual((), proposal.contradictions)
        feature_names = {feature.feature for feature in proposal.features}
        self.assertEqual(
            {
                "normalized-name",
                "name-token-overlap",
                "source-backed-alias",
                "external-identifier-agreement",
                "jurisdiction-context",
                "entity-kind-context",
            },
            feature_names,
        )
        for feature in proposal.features:
            self.assertTrue(feature.detail)
        repeat = service.propose(
            proposal_id=proposal.proposal_id,
            subject=subject,
            target=target,
            proposed_at=T0,
        )
        self.assertEqual(proposal.uncertainty.score, repeat.uncertainty.score)

    def test_exact_normalized_name_still_only_proposes_a_possible_match(self) -> None:
        service = IdentityResolutionService()
        subject = _observation(name="A.C.M.E., Inc.")
        target = _observation(name="ACME Incorporated")
        proposal = service.propose(
            proposal_id=uuid4(), subject=subject, target=target, proposed_at=T0
        )
        self.assertEqual("propose_possible_match", proposal.effect)
        weights = {feature.feature: feature.weight for feature in proposal.features}
        self.assertEqual(1.0, weights["normalized-name"])

    def test_conflicting_external_identifiers_are_preserved_as_contradictions(
        self,
    ) -> None:
        service = IdentityResolutionService()
        subject = _observation(
            name="Synthetic Beta Co",
            external_identifiers=(_identifier("duns", "111"),),
        )
        target = _observation(
            name="Synthetic Beta Co",
            external_identifiers=(_identifier("duns", "222"),),
            jurisdiction="us-ny",
        )
        proposal = service.propose(
            proposal_id=uuid4(), subject=subject, target=target, proposed_at=T0
        )
        self.assertEqual(1, len(proposal.contradictions))
        self.assertIn("duns", proposal.contradictions[0])
        self.assertIn("preserved", proposal.contradictions[0])
        self.assertEqual("Synthetic Beta Co", proposal.subject.display_name)
        weights = {feature.feature: feature.weight for feature in proposal.features}
        self.assertEqual(0.0, weights["external-identifier-agreement"])
        self.assertEqual(0.0, weights["jurisdiction-context"])

    def test_entity_kind_mismatch_is_preserved_not_harmonized(self) -> None:
        service = IdentityResolutionService()
        subject = _observation(name="Synthetic Gamma", party_kind="company")
        target = _observation(name="Synthetic Gamma", party_kind="trust")
        proposal = service.propose(
            proposal_id=uuid4(), subject=subject, target=target, proposed_at=T0
        )
        self.assertTrue(any("entity" in item for item in proposal.contradictions))

    def test_missing_sources_and_outage_remain_unknown_and_fail_closed(self) -> None:
        service = IdentityResolutionService()
        subject = _observation(name="Synthetic Delta", sources_available=False)
        target = _observation(name="Synthetic Delta")
        proposal = service.propose(
            proposal_id=uuid4(), subject=subject, target=target, proposed_at=T0
        )
        self.assertEqual("unknown", proposal.uncertainty.basis)
        self.assertEqual(0.0, proposal.uncertainty.score)
        self.assertEqual((), proposal.features)
        decision = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
        )
        with self.assertRaises(ValueError):
            service.record_decision(proposal=proposal, decision=decision)

    def test_unknown_uncertainty_cannot_carry_a_score(self) -> None:
        with self.assertRaises(ValidationError):
            MatchUncertainty(basis="unknown", score=0.5, rationale="invalid")

    def test_proposal_requires_provenance_and_distinct_scoped_parties(self) -> None:
        with self.assertRaises(ValidationError):
            PartyObservation(
                party_id=uuid4(),
                tenant_id=TENANT_ID,
                display_name="Synthetic Orphan",
                context=IdentityContext(entity_kind="company"),
                provenance=(),
            )
        service = IdentityResolutionService()
        subject = _observation(name="Synthetic Epsilon")
        with self.assertRaises(ValueError):
            service.propose(
                proposal_id=uuid4(),
                subject=subject,
                target=subject,
                proposed_at=T0,
            )
        cross_tenant = _observation(name="Synthetic Epsilon", tenant_id=OTHER_TENANT_ID)
        with self.assertRaises(ValueError):
            service.propose(
                proposal_id=uuid4(),
                subject=subject,
                target=cross_tenant,
                proposed_at=T0,
            )

    def test_external_identifier_requires_license_basis_and_provenance(self) -> None:
        with self.assertRaises(ValidationError):
            ExternalIdentifier.model_validate(
                {
                    "scheme": "lei",
                    "value": "SYNTH-LEI-0002",
                    "provenance": _provenance("x").model_dump(mode="python"),
                }
            )
        with self.assertRaises(ValidationError):
            ExternalIdentifier.model_validate(
                {
                    "scheme": "lei",
                    "value": "SYNTH-LEI-0002",
                    "license_basis": "public-registry",
                }
            )


class PartyIdentityReviewDecisionTests(unittest.TestCase):
    def _proposal(
        self,
    ) -> tuple[IdentityResolutionService, IdentityCandidateProposal]:
        service = IdentityResolutionService()
        proposal = service.propose(
            proposal_id=uuid4(),
            subject=_observation(name="Synthetic Zeta, Inc."),
            target=_observation(name="Synthetic Zeta Incorporated"),
            proposed_at=T0,
        )
        return service, proposal

    def test_decisions_are_versioned_and_reversible_by_supersession(self) -> None:
        service, proposal = self._proposal()
        first = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
        )
        service.record_decision(proposal=proposal, decision=first)
        second = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.REJECTED_DISTINCT,
            version=2,
            supersedes=first.decision_id,
            decided_at=T0 + timedelta(hours=1),
        )
        service.record_decision(proposal=proposal, decision=second, prior=first)
        head = service.current_decision((first, second))
        self.assertEqual(second.decision_id, head.decision_id)
        self.assertEqual(IdentityReviewOutcome.REJECTED_DISTINCT, head.outcome)
        self.assertEqual("review_record_only", head.effect)

    def test_decision_chain_rejects_gaps_wrong_supersession_and_rewind(self) -> None:
        service, proposal = self._proposal()
        first = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.INCONCLUSIVE,
        )
        with self.assertRaises(ValueError):
            service.record_decision(proposal=proposal, decision=first, prior=first)
        bad_version = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
            version=3,
            supersedes=first.decision_id,
        )
        with self.assertRaises(ValueError):
            service.record_decision(
                proposal=proposal, decision=bad_version, prior=first
            )
        bad_link = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
            version=2,
            supersedes=uuid4(),
        )
        with self.assertRaises(ValueError):
            service.record_decision(proposal=proposal, decision=bad_link, prior=first)
        rewound = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
            version=2,
            supersedes=first.decision_id,
            decided_at=T0 - timedelta(hours=1),
        )
        with self.assertRaises(ValueError):
            service.record_decision(proposal=proposal, decision=rewound, prior=first)

    def test_decision_must_bind_exact_proposal_and_tenant(self) -> None:
        service, proposal = self._proposal()
        stray = _decision(
            proposal_id=uuid4(), outcome=IdentityReviewOutcome.INCONCLUSIVE
        )
        with self.assertRaises(ValueError):
            service.record_decision(proposal=proposal, decision=stray)
        cross_tenant = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.INCONCLUSIVE,
            tenant_id=OTHER_TENANT_ID,
        )
        with self.assertRaises(ValueError):
            service.record_decision(proposal=proposal, decision=cross_tenant)

    def test_decision_cannot_carry_conflict_waiver_or_access_effects(self) -> None:
        service, proposal = self._proposal()
        payload = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
        ).model_dump(mode="python")
        for forbidden in (
            "waiver_reference",
            "conflict_disposition",
            "access_grant",
            "merged_party_id",
        ):
            with self.assertRaises(ValidationError):
                IdentityReviewDecision.model_validate(
                    {**payload, forbidden: uuid4().hex}
                )

    def test_confirmed_identity_does_not_clear_an_exact_conflict_hold(self) -> None:
        service, proposal = self._proposal()
        confirmed = _decision(
            proposal_id=proposal.proposal_id,
            outcome=IdentityReviewOutcome.CONFIRMED_SAME_PARTY,
        )
        service.record_decision(proposal=proposal, decision=confirmed)

        conflict = ConflictService()
        candidate = PartyCandidate(
            party_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            display_name="Synthetic Zeta, Inc.",
            party_kind="company",
            proposed_relationship=PartyRelationship.CLIENT,
        )
        known = KnownPartyAssociation(
            association_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=uuid4(),
            party_id=uuid4(),
            display_name="Synthetic Zeta Incorporated",
            party_kind="company",
            relationship=PartyRelationship.ADVERSE_PARTY,
            active=True,
        )
        result = conflict.check(
            check_id=uuid4(),
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            candidates=(candidate,),
            known_associations=(known,),
            checked_at=T0,
        )
        self.assertEqual(ConflictDisposition.HOLD, result.recommended_disposition)
        self.assertEqual("review_record_only", confirmed.effect)

    def test_current_decision_rejects_empty_or_mixed_chains(self) -> None:
        service = IdentityResolutionService()
        with self.assertRaises(ValueError):
            service.current_decision(())
        first = _decision(
            proposal_id=uuid4(), outcome=IdentityReviewOutcome.INCONCLUSIVE
        )
        other = _decision(
            proposal_id=uuid4(), outcome=IdentityReviewOutcome.INCONCLUSIVE
        )
        with self.assertRaises(ValueError):
            service.current_decision((first, other))


class PartyIdentityEvaluationTests(unittest.TestCase):
    def _scored_proposal(self, score_name: str) -> IdentityCandidateProposal:
        service = IdentityResolutionService()
        subject = _observation(name=f"Synthetic {score_name} One")
        target = _observation(name=f"Synthetic {score_name} Two")
        proposal = service.propose(
            proposal_id=uuid4(), subject=subject, target=target, proposed_at=T0
        )
        return proposal

    def test_evaluation_counts_false_positives_and_negatives(self) -> None:
        service = IdentityResolutionService()
        strong = service.propose(
            proposal_id=uuid4(),
            subject=_observation(name="Synthetic Eta Holdings"),
            target=_observation(name="Synthetic Eta Holdings"),
            proposed_at=T0,
        )
        weak_subject = _observation(name="Synthetic Theta One")
        weak_target = _observation(
            name="Completely Different Theta", jurisdiction="us-ny"
        )
        weak = service.propose(
            proposal_id=uuid4(),
            subject=weak_subject,
            target=weak_target,
            proposed_at=T0,
        )
        unknown = service.propose(
            proposal_id=uuid4(),
            subject=_observation(name="Synthetic Iota", sources_available=False),
            target=_observation(name="Synthetic Iota"),
            proposed_at=T0,
        )
        pairs = (
            LabeledIdentityPair(
                pair_id=uuid4(), proposal_id=strong.proposal_id, same_party=True
            ),
            LabeledIdentityPair(
                pair_id=uuid4(), proposal_id=weak.proposal_id, same_party=False
            ),
            LabeledIdentityPair(
                pair_id=uuid4(), proposal_id=unknown.proposal_id, same_party=True
            ),
        )
        report = evaluate_candidate_quality(
            labeled_pairs=pairs,
            proposals=(strong, weak, unknown),
            threshold=0.5,
        )
        self.assertEqual(3, report.evaluated_pair_count)
        self.assertEqual(1, report.true_positive_count)
        self.assertEqual(1, report.false_negative_count)
        self.assertEqual(1, report.true_negative_count)
        self.assertEqual(0, report.false_positive_count)

        forced = evaluate_candidate_quality(
            labeled_pairs=pairs,
            proposals=(strong, weak, unknown),
            threshold=0.0,
        )
        self.assertEqual(1, forced.false_positive_count)

    def test_evaluation_accepts_only_synthetic_labels(self) -> None:
        proposal = self._scored_proposal("Kappa")
        with self.assertRaises(ValidationError):
            LabeledIdentityPair.model_validate(
                {
                    "pair_id": uuid4(),
                    "proposal_id": proposal.proposal_id,
                    "same_party": True,
                    "synthetic": False,
                }
            )

    def test_evaluation_requires_exact_proposals_and_valid_threshold(self) -> None:
        proposal = self._scored_proposal("Lambda")
        pair = LabeledIdentityPair(
            pair_id=uuid4(), proposal_id=proposal.proposal_id, same_party=True
        )
        with self.assertRaises(ValueError):
            evaluate_candidate_quality(
                labeled_pairs=(pair,), proposals=(), threshold=0.5
            )
        with self.assertRaises(ValueError):
            evaluate_candidate_quality(
                labeled_pairs=(pair,), proposals=(proposal,), threshold=1.5
            )


if __name__ == "__main__":
    unittest.main()
