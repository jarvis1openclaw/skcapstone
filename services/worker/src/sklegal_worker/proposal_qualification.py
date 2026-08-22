"""Blind challenge, human decision, and cross-boundary replay evidence.

This module composes the already qualified proposal, claim gate, review
surface, and human decision boundaries. It performs no external action and
stores no protected content. Every replay event carries identifiers, digests,
policy revisions, and exact outcomes only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import Field, model_validator
from sklegal_domain import (
    AuthoritySupportVerification,
    BlindChallengeRecord,
    GateEvaluation,
    LedgerClaim,
    ModelRunIdentity,
    ValidationOutcome,
    evaluate_claim_ready_gate,
)

from .errors import PolicyDeniedError, WorkflowInvariantError
from .models import Sha256Digest, UtcDateTime, WorkflowPayload
from .proposal_run import ProposalRunRecord


def _digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class QualificationAction(StrEnum):
    """Current-policy checkpoints in the qualification run."""

    RUN_BLIND_CHALLENGE = "run_blind_challenge"
    RECORD_HUMAN_DECISION = "record_human_decision"


class HumanClaimDecision(StrEnum):
    """Human outcomes accepted by the claim decision API."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PolicyCheckpoint(WorkflowPayload):
    """Sanitized proof that current policy authorized one boundary."""

    action: QualificationAction
    authorization_decision_id: UUID
    policy_revision: Sha256Digest
    checked_at: UtcDateTime


class CurrentPolicyGate(Protocol):
    """Recheck current authorization at each effect boundary."""

    def authorize(
        self,
        *,
        run_key: str,
        tenant_id: UUID,
        matter_id: UUID,
        action: QualificationAction,
        at: datetime,
    ) -> PolicyCheckpoint: ...


class BlindChallengeRequest(WorkflowPayload):
    """Conclusion-free request sent to the independent challenger."""

    run_key: str = Field(min_length=1, max_length=160)
    proposal_id: str = Field(min_length=1, max_length=255)
    claim_id: UUID
    tenant_id: UUID
    matter_id: UUID
    challenged_model: ModelRunIdentity
    proposal_payload_sha256: Sha256Digest
    context_source_sha256: tuple[Sha256Digest, ...] = Field(min_length=1)


class BlindChallengeRunner(Protocol):
    """Run an independent challenge without the challenged conclusion."""

    def run(
        self, request: BlindChallengeRequest, *, at: datetime
    ) -> BlindChallengeRecord: ...


class ChallengeReview(WorkflowPayload):
    """Read-only S4-03 projection input for one qualification review."""

    run_key: str = Field(min_length=1, max_length=160)
    proposal_id: str = Field(min_length=1, max_length=255)
    claim_id: UUID
    claim_version: int = Field(ge=1)
    challenge: BlindChallengeRecord
    gate: GateEvaluation
    policy_revision: Sha256Digest


class ReviewPresentationReceipt(WorkflowPayload):
    """Proof that the exact review was presented on the S4-03 surface."""

    review_id: UUID
    review_sha256: Sha256Digest
    presented_at: UtcDateTime


class ClaimReviewSurface(Protocol):
    """Publish a read-only challenge review to the S4-03 surface."""

    def present(
        self, review: ChallengeReview, *, at: datetime
    ) -> ReviewPresentationReceipt: ...


class HumanDecisionRequest(WorkflowPayload):
    """Exact-version command for the S1-04A human decision API."""

    idempotency_key: UUID
    run_key: str = Field(min_length=1, max_length=160)
    tenant_id: UUID
    matter_id: UUID
    claim_id: UUID
    claim_version: int = Field(ge=1)
    review_id: UUID
    review_sha256: Sha256Digest
    gate_sha256: Sha256Digest
    decision: HumanClaimDecision
    note: str = Field(default="", max_length=2000)


class HumanDecisionReceipt(WorkflowPayload):
    """Attributable append-only receipt returned by the S1-04A API."""

    decision_id: UUID
    idempotency_key: UUID
    reviewer_principal_id: UUID
    decision: HumanClaimDecision
    claim_id: UUID
    claim_version: int = Field(ge=1)
    policy_revision: Sha256Digest
    authorization_decision_id: UUID
    decided_at: UtcDateTime
    receipt_sha256: Sha256Digest


class HumanDecisionApi(Protocol):
    """Record one human decision through the governed decision boundary."""

    def record(self, request: HumanDecisionRequest) -> HumanDecisionReceipt: ...


class ReplayStage(StrEnum):
    PROPOSAL_PINNED = "proposal_pinned"
    CHALLENGE_AUTHORIZED = "challenge_authorized"
    CHALLENGE_RECORDED = "challenge_recorded"
    CLAIM_GATE_EVALUATED = "claim_gate_evaluated"
    REVIEW_PRESENTED = "review_presented"
    DECISION_AUTHORIZED = "decision_authorized"
    HUMAN_DECISION_RECORDED = "human_decision_recorded"
    REPLAY_COMPLETED = "replay_completed"


class ReplayEvent(WorkflowPayload):
    """One content-free event in the tamper-evident qualification chain."""

    sequence: int = Field(ge=1)
    stage: ReplayStage
    at: UtcDateTime
    evidence_sha256: Sha256Digest
    previous_event_sha256: Sha256Digest
    event_sha256: Sha256Digest


class QualificationReplay(WorkflowPayload):
    """Complete cross-boundary evidence for one terminal human decision."""

    run_key: str = Field(min_length=1, max_length=160)
    proposal_id: str = Field(min_length=1, max_length=255)
    tenant_id: UUID
    matter_id: UUID
    claim_id: UUID
    claim_version: int = Field(ge=1)
    decision: HumanClaimDecision
    gate_outcome: ValidationOutcome
    challenge_id: str = Field(min_length=1, max_length=255)
    review_id: UUID
    human_decision_id: UUID
    events: tuple[ReplayEvent, ...] = Field(min_length=8, max_length=8)
    chain_head_sha256: Sha256Digest
    completed_at: UtcDateTime

    @model_validator(mode="after")
    def validate_chain(self) -> QualificationReplay:
        expected_stages = tuple(ReplayStage)
        if tuple(event.stage for event in self.events) != expected_stages:
            raise ValueError(
                "qualification replay stages are incomplete or out of order"
            )
        previous = "0" * 64
        for sequence, event in enumerate(self.events, start=1):
            if event.sequence != sequence or event.previous_event_sha256 != previous:
                raise ValueError("qualification replay chain linkage is invalid")
            expected = _event_hash(
                sequence=event.sequence,
                stage=event.stage,
                at=event.at,
                evidence_sha256=event.evidence_sha256,
                previous_event_sha256=event.previous_event_sha256,
            )
            if event.event_sha256 != expected:
                raise ValueError("qualification replay event digest is invalid")
            previous = event.event_sha256
        if self.chain_head_sha256 != previous:
            raise ValueError("qualification replay chain head is invalid")
        return self


def _event_hash(
    *,
    sequence: int,
    stage: ReplayStage,
    at: datetime,
    evidence_sha256: str,
    previous_event_sha256: str,
) -> str:
    return _digest(
        {
            "sequence": sequence,
            "stage": stage.value,
            "at": at.isoformat(),
            "evidence_sha256": evidence_sha256,
            "previous_event_sha256": previous_event_sha256,
        }
    )


class InMemoryQualificationReplayLedger:
    """Append-only first-write-wins store for complete replays."""

    def __init__(self) -> None:
        self._replays: dict[str, QualificationReplay] = {}

    def find(self, run_key: str) -> QualificationReplay | None:
        return self._replays.get(run_key)

    def record(self, replay: QualificationReplay) -> QualificationReplay:
        existing = self._replays.get(replay.run_key)
        if existing is not None:
            if _digest(existing) != _digest(replay):
                raise WorkflowInvariantError(
                    "qualification run key reused with different replay evidence"
                )
            return existing
        self._replays[replay.run_key] = replay
        return replay


class _ReplayBuilder:
    def __init__(self) -> None:
        self.events: list[ReplayEvent] = []

    def append(self, stage: ReplayStage, evidence: object, *, at: datetime) -> None:
        previous = self.events[-1].event_sha256 if self.events else "0" * 64
        evidence_sha256 = _digest(evidence)
        sequence = len(self.events) + 1
        event_sha256 = _event_hash(
            sequence=sequence,
            stage=stage,
            at=at,
            evidence_sha256=evidence_sha256,
            previous_event_sha256=previous,
        )
        self.events.append(
            ReplayEvent(
                sequence=sequence,
                stage=stage,
                at=at,
                evidence_sha256=evidence_sha256,
                previous_event_sha256=previous,
                event_sha256=event_sha256,
            )
        )


class ProposalQualificationCoordinator:
    """Coordinate the challenge, gate, review, and human decision boundaries."""

    def __init__(
        self,
        *,
        policy: CurrentPolicyGate,
        challenger: BlindChallengeRunner,
        review_surface: ClaimReviewSurface,
        decisions: HumanDecisionApi,
        replay_ledger: InMemoryQualificationReplayLedger,
    ) -> None:
        self._policy = policy
        self._challenger = challenger
        self._review_surface = review_surface
        self._decisions = decisions
        self._replay_ledger = replay_ledger

    def run(
        self,
        *,
        proposal: ProposalRunRecord,
        claim: LedgerClaim,
        support_verification: AuthoritySupportVerification,
        decision: HumanClaimDecision,
        decision_idempotency_key: UUID,
        note: str,
        at: datetime,
    ) -> QualificationReplay:
        existing = self._replay_ledger.find(proposal.run_key)
        if existing is not None:
            return existing

        builder = _ReplayBuilder()
        builder.append(ReplayStage.PROPOSAL_PINNED, proposal, at=at)

        challenge_policy = self._authorize(
            proposal, claim, QualificationAction.RUN_BLIND_CHALLENGE, at
        )
        builder.append(ReplayStage.CHALLENGE_AUTHORIZED, challenge_policy, at=at)

        challenged_model = ModelRunIdentity(
            provider=proposal.provider,
            model_name=proposal.model_name,
            model_revision=proposal.model_revision,
            route_id=proposal.route_id,
        )
        challenge_request = BlindChallengeRequest(
            run_key=proposal.run_key,
            proposal_id=proposal.proposal_id,
            claim_id=claim.id,
            tenant_id=claim.tenant_id,
            matter_id=claim.matter_id,
            challenged_model=challenged_model,
            proposal_payload_sha256=proposal.payload_sha256,
            context_source_sha256=tuple(
                source.source_sha256 for source in proposal.context_sources
            ),
        )
        challenge = self._challenger.run(challenge_request, at=at)
        self._validate_challenge(challenge_request, challenge)
        builder.append(ReplayStage.CHALLENGE_RECORDED, challenge, at=at)

        gate = evaluate_claim_ready_gate(
            claim=claim,
            support_verification=support_verification,
            challenges=(challenge,),
            at=at,
        )
        builder.append(ReplayStage.CLAIM_GATE_EVALUATED, gate, at=at)

        review = ChallengeReview(
            run_key=proposal.run_key,
            proposal_id=proposal.proposal_id,
            claim_id=claim.id,
            claim_version=claim.version,
            challenge=challenge,
            gate=gate,
            policy_revision=challenge_policy.policy_revision,
        )
        presentation = self._review_surface.present(review, at=at)
        if presentation.review_sha256 != _digest(review):
            raise WorkflowInvariantError("review surface receipt digest disagrees")
        builder.append(ReplayStage.REVIEW_PRESENTED, presentation, at=at)

        decision_policy = self._authorize(
            proposal, claim, QualificationAction.RECORD_HUMAN_DECISION, at
        )
        builder.append(ReplayStage.DECISION_AUTHORIZED, decision_policy, at=at)

        if (
            decision is HumanClaimDecision.ACCEPTED
            and gate.outcome is not ValidationOutcome.PASSED
        ):
            raise WorkflowInvariantError("a failed claim gate cannot be accepted")
        decision_request = HumanDecisionRequest(
            idempotency_key=decision_idempotency_key,
            run_key=proposal.run_key,
            tenant_id=claim.tenant_id,
            matter_id=claim.matter_id,
            claim_id=claim.id,
            claim_version=claim.version,
            review_id=presentation.review_id,
            review_sha256=presentation.review_sha256,
            gate_sha256=_digest(gate),
            decision=decision,
            note=note,
        )
        receipt = self._decisions.record(decision_request)
        self._validate_decision_receipt(decision_request, decision_policy, receipt)
        builder.append(ReplayStage.HUMAN_DECISION_RECORDED, receipt, at=at)
        builder.append(
            ReplayStage.REPLAY_COMPLETED,
            {
                "decision_id": str(receipt.decision_id),
                "external_effect": False,
                "hammertime_mutation": False,
            },
            at=at,
        )

        replay = QualificationReplay(
            run_key=proposal.run_key,
            proposal_id=proposal.proposal_id,
            tenant_id=claim.tenant_id,
            matter_id=claim.matter_id,
            claim_id=claim.id,
            claim_version=claim.version,
            decision=receipt.decision,
            gate_outcome=gate.outcome,
            challenge_id=challenge.challenge_id,
            review_id=presentation.review_id,
            human_decision_id=receipt.decision_id,
            events=tuple(builder.events),
            chain_head_sha256=builder.events[-1].event_sha256,
            completed_at=at,
        )
        return self._replay_ledger.record(replay)

    def _authorize(
        self,
        proposal: ProposalRunRecord,
        claim: LedgerClaim,
        action: QualificationAction,
        at: datetime,
    ) -> PolicyCheckpoint:
        try:
            checkpoint = self._policy.authorize(
                run_key=proposal.run_key,
                tenant_id=claim.tenant_id,
                matter_id=claim.matter_id,
                action=action,
                at=at,
            )
        except PolicyDeniedError:
            raise
        except Exception as exc:
            raise PolicyDeniedError("current policy checkpoint failed closed") from exc
        if checkpoint.action is not action:
            raise PolicyDeniedError("current policy checkpoint action disagrees")
        return checkpoint

    @staticmethod
    def _validate_challenge(
        request: BlindChallengeRequest, challenge: BlindChallengeRecord
    ) -> None:
        if (
            challenge.claim_id != request.claim_id
            or challenge.tenant_id != request.tenant_id
            or challenge.matter_id != request.matter_id
            or not challenge.challenged_model.same_model_as(request.challenged_model)
        ):
            raise WorkflowInvariantError("blind challenge crosses its pinned scope")

    @staticmethod
    def _validate_decision_receipt(
        request: HumanDecisionRequest,
        policy: PolicyCheckpoint,
        receipt: HumanDecisionReceipt,
    ) -> None:
        if (
            receipt.idempotency_key != request.idempotency_key
            or receipt.claim_id != request.claim_id
            or receipt.claim_version != request.claim_version
            or receipt.decision is not request.decision
            or receipt.policy_revision != policy.policy_revision
            or receipt.authorization_decision_id != policy.authorization_decision_id
        ):
            raise WorkflowInvariantError("human decision receipt disagrees")
