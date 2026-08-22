"""Blind challenge labeling and the deterministic release gate stack.

Deterministic gate validators for the SKL-S3-05 claim ledger: no model calls,
no I/O, and no retrieval imports. The three gates are ``CLAIM_READY`` (the
claim ledger entry may ground drafting), ``DRAFT_READY`` (a frozen work
product version may be approved), and ``RELEASE_READY`` (an approved exact
version may be released). They are consumed by SKL-S4-03, SKL-S4-04, and
Sprint 5.

Every input is a typed deterministic record: an authority support
verification, entity states, exact artifact bindings, and typed blind
challenge records. Model output enters only as a ``BlindChallengeRecord``
whose model identities are compared to label same-model challenges, and a
same-model or non-blind challenge can never satisfy the independence
requirement. A failed gate cannot be waived by model output because no gate
function accepts a waiver, assertion, or model payload, and a passing
``GateEvaluation`` cannot even be constructed while any check failed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import model_validator

from .authority_verification import AuthoritySupportVerification
from .drafting import unresolved_blockers
from .entities.claim_ledger import LedgerClaim
from .entities.integration import Approval
from .entities.work_product import (
    WorkProduct,
    WorkProductUnknown,
    WorkProductVersion,
)
from .states import (
    ApprovalStatus,
    LedgerClaimStatus,
    ValidationOutcome,
    WorkProductStatus,
    WorkProductVersionStatus,
)
from .value_objects import (
    DomainId,
    FrozenValue,
    NonEmptyText,
    ShortText,
    UtcDateTime,
    require_utc,
)


def _normalize_identity_text(value: str) -> str:
    """Collapse whitespace and casefold so model identity equality is exact."""

    if not isinstance(value, str):
        raise TypeError("model identity components must be text")
    return " ".join(value.split()).casefold()


class ModelRunIdentity(FrozenValue):
    """The exact model that produced one proposal or challenge run.

    ``provider``, ``model_name``, and ``model_revision`` are the identity:
    two runs with the same normalized triple are the same model no matter
    which logical route, transport profile, or serving alias carried the
    call, so those fields stay trace metadata and never affect the
    same-model comparison.
    """

    provider: ShortText
    model_name: ShortText
    model_revision: ShortText
    route_id: ShortText | None = None
    served_alias: ShortText | None = None

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return (
            _normalize_identity_text(self.provider),
            _normalize_identity_text(self.model_name),
            _normalize_identity_text(self.model_revision),
        )

    def same_model_as(self, other: ModelRunIdentity) -> bool:
        """Whether both runs came from the same exact model."""

        return self.identity_key == other.identity_key


class ChallengeDefectKind(StrEnum):
    """The closed vocabulary of defect kinds a blind challenge may report."""

    UNSUPPORTED_ASSERTION = "unsupported_assertion"
    AUTHORITY_MISREAD = "authority_misread"
    QUOTATION_ERROR = "quotation_error"
    JURISDICTION_ERROR = "jurisdiction_error"
    CONTRARY_AUTHORITY_MISSED = "contrary_authority_missed"
    REMEDY_GAP = "remedy_gap"
    OTHER = "other"


class ChallengeDefect(FrozenValue):
    """One challenge finding, preserved verbatim for human review."""

    defect_kind: ChallengeDefectKind
    description: NonEmptyText


class ChallengeOutcome(StrEnum):
    """What one completed blind challenge found."""

    NO_DEFECT = "no_defect"
    DEFECT_FOUND = "defect_found"


class ChallengeIndependence(StrEnum):
    """The deterministic independence label of one blind challenge.

    ``SAME_MODEL`` means the challenger is the same exact model that
    produced the challenged proposal. ``NOT_BLIND`` means the challenger
    saw the challenged conclusion before answering. Neither can satisfy the
    independence requirement of the claim gate.
    """

    INDEPENDENT = "independent"
    SAME_MODEL = "same_model"
    NOT_BLIND = "not_blind"


class BlindChallengeRecord(FrozenValue):
    """One typed blind-challenge run against one ledger claim.

    The record carries both model identities so the same-model label is
    always reconstructible from persisted evidence, and the challenge
    outcome with its defects so findings are preserved, never silently
    reduced to a pass.
    """

    challenge_id: ShortText
    claim_id: DomainId
    tenant_id: DomainId
    matter_id: DomainId
    challenged_model: ModelRunIdentity
    challenger_model: ModelRunIdentity
    saw_challenged_conclusion: bool
    issued_at: UtcDateTime
    outcome: ChallengeOutcome
    defects: tuple[ChallengeDefect, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_defects(self) -> BlindChallengeRecord:
        if self.outcome is ChallengeOutcome.DEFECT_FOUND and not self.defects:
            raise ValueError("a defect-found challenge reports its defects")
        if self.outcome is ChallengeOutcome.NO_DEFECT and self.defects:
            raise ValueError("a no-defect challenge carries no defects")
        return self

    @property
    def same_model(self) -> bool:
        """Whether the challenger is the exact model it is challenging."""

        return self.challenger_model.same_model_as(self.challenged_model)

    @property
    def independence(self) -> ChallengeIndependence:
        return label_challenge_independence(self)


def label_challenge_independence(
    challenge: BlindChallengeRecord,
) -> ChallengeIndependence:
    """Reduce one challenge record to its deterministic independence label.

    A same-model challenge is labeled ``SAME_MODEL`` even when it is blind,
    because the same model reviewing its own output is not an independent
    challenge regardless of what it was shown.
    """

    if challenge.same_model:
        return ChallengeIndependence.SAME_MODEL
    if challenge.saw_challenged_conclusion:
        return ChallengeIndependence.NOT_BLIND
    return ChallengeIndependence.INDEPENDENT


class GateKind(StrEnum):
    """The three deterministic gates of the claim-to-release path."""

    CLAIM_READY = "claim_ready"
    DRAFT_READY = "draft_ready"
    RELEASE_READY = "release_ready"


class GateCheckId(StrEnum):
    """Stable identifiers for the deterministic gate checks."""

    CLAIM_SUPPORT_VERIFIED = "claim.support_verified"
    CLAIM_STATUS_SUPPORTED = "claim.status_supported"
    CHALLENGE_EXECUTED = "challenge.executed"
    CHALLENGE_CURRENT = "challenge.current"
    CHALLENGE_INDEPENDENT = "challenge.independent"
    CHALLENGE_NO_DEFECT = "challenge.no_defect"
    DRAFT_CLAIMS_READY = "draft.claims_ready"
    DRAFT_VERSION_FROZEN = "draft.version_frozen"
    DRAFT_UNKNOWNS_RESOLVED = "draft.unknowns_resolved"
    RELEASE_WORK_PRODUCT_APPROVED = "release.work_product_approved"
    RELEASE_APPROVAL_VALID = "release.approval_valid"
    RELEASE_ARTIFACT_UNCHANGED = "release.artifact_unchanged"
    RELEASE_CLAIMS_READY = "release.claims_ready"


class GateFailureReason(StrEnum):
    """The closed reason vocabulary for every failed gate check."""

    AUTHORITY_SUPPORT_MISSING = "authority_support_missing"
    AUTHORITY_SUPPORT_FAILED = "authority_support_failed"
    AUTHORITY_SUPPORT_STALE = "authority_support_stale"
    SUPPORT_SCOPE_CROSSING = "support_scope_crossing"
    CLAIM_NOT_SUPPORTED = "claim_not_supported"
    CHALLENGE_MISSING = "challenge_missing"
    CHALLENGE_SCOPE_CROSSING = "challenge_scope_crossing"
    STALE_CHALLENGE = "stale_challenge"
    SAME_MODEL_CHALLENGE = "same_model_challenge"
    NOT_BLIND_CHALLENGE = "not_blind_challenge"
    CHALLENGE_DEFECT_UNRESOLVED = "challenge_defect_unresolved"
    GROUNDING_MISSING = "grounding_missing"
    CLAIM_NOT_READY = "claim_not_ready"
    CLAIM_GATE_SCOPE_CROSSING = "claim_gate_scope_crossing"
    VERSION_SCOPE_CROSSING = "version_scope_crossing"
    VERSION_NOT_CURRENT = "version_not_current"
    VERSION_NOT_FROZEN = "version_not_frozen"
    UNKNOWNS_UNRESOLVED = "unknowns_unresolved"
    WORK_PRODUCT_NOT_APPROVED = "work_product_not_approved"
    APPROVAL_LINKAGE_MISSING = "approval_linkage_missing"
    APPROVAL_MISSING = "approval_missing"
    APPROVAL_NOT_APPROVED = "approval_not_approved"
    APPROVAL_REVOKED = "approval_revoked"
    APPROVAL_SCOPE_CROSSING = "approval_scope_crossing"
    APPROVAL_FUTURE = "approval_future"
    ARTIFACT_CHANGED_AFTER_APPROVAL = "artifact_changed_after_approval"


class GateCheck(FrozenValue):
    """One deterministic gate check outcome with its subject and reasons."""

    check_id: GateCheckId
    subject_id: DomainId | None
    outcome: ValidationOutcome
    reasons: tuple[GateFailureReason, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_reasons(self) -> GateCheck:
        if self.outcome is ValidationOutcome.INCOMPLETE:
            raise ValueError("a gate check is never incomplete")
        if self.outcome is ValidationOutcome.PASSED and self.reasons:
            raise ValueError("a passed gate check carries no reasons")
        if self.outcome is ValidationOutcome.FAILED and not self.reasons:
            raise ValueError("a failed gate check cites its reasons")
        return self

    @property
    def passed(self) -> bool:
        return self.outcome is ValidationOutcome.PASSED


class GateEvaluation(FrozenValue):
    """The deterministic evaluation of one gate over typed records only.

    The outcome is reduced from the checks and validated on construction: a
    passing evaluation with a failed check is unrepresentable, so no
    downstream code, model driven or not, can forge or waive a failed gate.
    """

    gate: GateKind
    subject_id: DomainId
    tenant_id: DomainId
    matter_id: DomainId
    evaluated_at: UtcDateTime
    checks: tuple[GateCheck, ...]
    outcome: ValidationOutcome

    @model_validator(mode="after")
    def validate_outcome(self) -> GateEvaluation:
        if self.outcome is ValidationOutcome.INCOMPLETE:
            raise ValueError("a gate evaluation is never incomplete")
        failed = any(check.outcome is ValidationOutcome.FAILED for check in self.checks)
        if self.outcome is ValidationOutcome.PASSED and failed:
            raise ValueError("a passed gate evaluation has no failed checks")
        if self.outcome is ValidationOutcome.FAILED and not failed:
            raise ValueError("a failed gate evaluation has at least one failed check")
        return self

    @property
    def failed_checks(self) -> tuple[GateCheck, ...]:
        return tuple(
            check for check in self.checks if check.outcome is ValidationOutcome.FAILED
        )


def _check(
    check_id: GateCheckId,
    subject_id: DomainId | None,
    reasons: tuple[GateFailureReason, ...],
) -> GateCheck:
    return GateCheck(
        check_id=check_id,
        subject_id=subject_id,
        outcome=(ValidationOutcome.FAILED if reasons else ValidationOutcome.PASSED),
        reasons=reasons,
    )


def evaluate_claim_ready_gate(
    *,
    claim: LedgerClaim,
    support_verification: AuthoritySupportVerification | None,
    challenges: tuple[BlindChallengeRecord, ...],
    at: datetime,
) -> GateEvaluation:
    """Evaluate ``CLAIM_READY`` for one ledger claim, fail closed.

    The claim is ready when its deterministic authority support verification
    passed for the current claim revision, the ledger status is supported,
    and the current revision carries an independent, blind, no-defect
    challenge. Same-model and non-blind challenges are labeled in the
    record and fail the independence check instead of waiving it.
    """

    instant = require_utc(at)
    if support_verification is not None and support_verification.evaluated_at > instant:
        raise ValueError("authority support verification cannot postdate the gate")
    for challenge in challenges:
        if challenge.issued_at > instant:
            raise ValueError("blind challenge cannot postdate the gate")

    support_reasons: list[GateFailureReason] = []
    if support_verification is None:
        support_reasons.append(GateFailureReason.AUTHORITY_SUPPORT_MISSING)
    else:
        if (
            support_verification.claim_id != claim.id
            or support_verification.tenant_id != claim.tenant_id
            or support_verification.matter_id != claim.matter_id
        ):
            support_reasons.append(GateFailureReason.SUPPORT_SCOPE_CROSSING)
        if support_verification.evaluated_at < claim.updated_at:
            support_reasons.append(GateFailureReason.AUTHORITY_SUPPORT_STALE)
        if support_verification.outcome is not ValidationOutcome.PASSED:
            support_reasons.append(GateFailureReason.AUTHORITY_SUPPORT_FAILED)

    status_reasons: list[GateFailureReason] = []
    if claim.status is not LedgerClaimStatus.SUPPORTED:
        status_reasons.append(GateFailureReason.CLAIM_NOT_SUPPORTED)

    in_scope = [
        challenge
        for challenge in challenges
        if challenge.claim_id == claim.id
        and challenge.tenant_id == claim.tenant_id
        and challenge.matter_id == claim.matter_id
    ]
    executed_reasons: list[GateFailureReason] = []
    if len(in_scope) != len(challenges):
        executed_reasons.append(GateFailureReason.CHALLENGE_SCOPE_CROSSING)
    if not in_scope:
        executed_reasons.append(GateFailureReason.CHALLENGE_MISSING)

    current = [
        challenge for challenge in in_scope if challenge.issued_at >= claim.updated_at
    ]
    current_reasons: list[GateFailureReason] = []
    if not current:
        current_reasons.append(GateFailureReason.STALE_CHALLENGE)

    independent = [
        challenge
        for challenge in current
        if challenge.independence is ChallengeIndependence.INDEPENDENT
    ]
    independence_reasons: list[GateFailureReason] = []
    if not independent:
        if any(
            challenge.independence is ChallengeIndependence.SAME_MODEL
            for challenge in current
        ):
            independence_reasons.append(GateFailureReason.SAME_MODEL_CHALLENGE)
        if any(
            challenge.independence is ChallengeIndependence.NOT_BLIND
            for challenge in current
        ):
            independence_reasons.append(GateFailureReason.NOT_BLIND_CHALLENGE)
        if not independence_reasons:
            independence_reasons.append(GateFailureReason.CHALLENGE_MISSING)

    defect_reasons: list[GateFailureReason] = []
    if not any(
        challenge.outcome is ChallengeOutcome.NO_DEFECT for challenge in independent
    ):
        defect_reasons.append(GateFailureReason.CHALLENGE_DEFECT_UNRESOLVED)

    checks = (
        _check(GateCheckId.CLAIM_SUPPORT_VERIFIED, claim.id, tuple(support_reasons)),
        _check(GateCheckId.CLAIM_STATUS_SUPPORTED, claim.id, tuple(status_reasons)),
        _check(GateCheckId.CHALLENGE_EXECUTED, claim.id, tuple(executed_reasons)),
        _check(GateCheckId.CHALLENGE_CURRENT, claim.id, tuple(current_reasons)),
        _check(
            GateCheckId.CHALLENGE_INDEPENDENT, claim.id, tuple(independence_reasons)
        ),
        _check(GateCheckId.CHALLENGE_NO_DEFECT, claim.id, tuple(defect_reasons)),
    )
    outcome = (
        ValidationOutcome.PASSED
        if all(check.passed for check in checks)
        else ValidationOutcome.FAILED
    )
    return GateEvaluation(
        gate=GateKind.CLAIM_READY,
        subject_id=claim.id,
        tenant_id=claim.tenant_id,
        matter_id=claim.matter_id,
        evaluated_at=instant,
        checks=checks,
        outcome=outcome,
    )


def _claim_readiness_reasons(
    *,
    grounding_claim_ids: tuple[DomainId, ...],
    claim_gates: tuple[GateEvaluation, ...],
    work_product: WorkProduct,
) -> list[tuple[DomainId, GateFailureReason]]:
    """Reduce grounding claim coverage to per-claim failure pairs."""

    pairs: list[tuple[DomainId, GateFailureReason]] = []
    if not grounding_claim_ids:
        pairs.append((work_product.id, GateFailureReason.GROUNDING_MISSING))
        return pairs
    by_subject: dict[DomainId, list[GateEvaluation]] = {}
    for gate in claim_gates:
        by_subject.setdefault(gate.subject_id, []).append(gate)
    for claim_id in grounding_claim_ids:
        matched = by_subject.get(claim_id, [])
        if len(matched) > 1:
            raise ValueError("duplicate claim gate evaluations for one claim")
        if not matched:
            pairs.append((claim_id, GateFailureReason.CLAIM_NOT_READY))
            continue
        gate = matched[0]
        if gate.gate is not GateKind.CLAIM_READY:
            raise ValueError("claim readiness requires claim_ready evaluations")
        if (
            gate.tenant_id != work_product.tenant_id
            or gate.matter_id != work_product.matter_id
        ):
            pairs.append((claim_id, GateFailureReason.CLAIM_GATE_SCOPE_CROSSING))
        elif gate.outcome is not ValidationOutcome.PASSED:
            pairs.append((claim_id, GateFailureReason.CLAIM_NOT_READY))
    return pairs


def _claim_readiness_checks(
    check_id: GateCheckId,
    pairs: list[tuple[DomainId, GateFailureReason]],
    grounding_claim_ids: tuple[DomainId, ...],
    work_product_id: DomainId,
) -> tuple[GateCheck, ...]:
    """Emit one check per grounding claim, passed or failed, never omitted."""

    by_claim: dict[DomainId, list[GateFailureReason]] = {}
    for claim_id, reason in pairs:
        by_claim.setdefault(claim_id, []).append(reason)
    subjects = grounding_claim_ids or (work_product_id,)
    return tuple(
        _check(check_id, claim_id, tuple(by_claim.get(claim_id, ())))
        for claim_id in subjects
    )


def _version_binding_reasons(
    work_product: WorkProduct, version: WorkProductVersion
) -> list[GateFailureReason]:
    reasons: list[GateFailureReason] = []
    if (
        version.work_product_id != work_product.id
        or version.tenant_id != work_product.tenant_id
        or version.matter_id != work_product.matter_id
    ):
        reasons.append(GateFailureReason.VERSION_SCOPE_CROSSING)
    if work_product.current_version_id != version.id:
        reasons.append(GateFailureReason.VERSION_NOT_CURRENT)
    return reasons


def evaluate_draft_ready_gate(
    *,
    work_product: WorkProduct,
    version: WorkProductVersion,
    unknowns: tuple[WorkProductUnknown, ...],
    grounding_claim_ids: tuple[DomainId, ...],
    claim_gates: tuple[GateEvaluation, ...],
    at: datetime,
) -> GateEvaluation:
    """Evaluate ``DRAFT_READY`` for one work product version, fail closed.

    The draft is ready when every grounding claim carries a passing
    ``CLAIM_READY`` evaluation in scope, the version is the frozen current
    version of the work product, and no bracketed unknown still blocks the
    exact version.
    """

    instant = require_utc(at)
    for gate in claim_gates:
        if gate.gate is not GateKind.CLAIM_READY:
            raise ValueError("claim readiness requires claim_ready evaluations")
        if gate.evaluated_at > instant:
            raise ValueError("claim gate evaluation cannot postdate the gate")

    claim_checks = _claim_readiness_checks(
        GateCheckId.DRAFT_CLAIMS_READY,
        _claim_readiness_reasons(
            grounding_claim_ids=grounding_claim_ids,
            claim_gates=claim_gates,
            work_product=work_product,
        ),
        grounding_claim_ids,
        work_product.id,
    )
    frozen_reasons = _version_binding_reasons(work_product, version)
    if version.status is not WorkProductVersionStatus.FROZEN:
        frozen_reasons.append(GateFailureReason.VERSION_NOT_FROZEN)
    frozen_checks = (
        _check(GateCheckId.DRAFT_VERSION_FROZEN, version.id, tuple(frozen_reasons)),
    )

    blockers = unresolved_blockers(version, unknowns)
    unknown_checks = (
        _check(
            GateCheckId.DRAFT_UNKNOWNS_RESOLVED,
            version.id,
            (GateFailureReason.UNKNOWNS_UNRESOLVED,) if blockers else (),
        ),
    )

    checks = claim_checks + frozen_checks + unknown_checks
    outcome = (
        ValidationOutcome.PASSED
        if all(check.passed for check in checks)
        else ValidationOutcome.FAILED
    )
    return GateEvaluation(
        gate=GateKind.DRAFT_READY,
        subject_id=work_product.id,
        tenant_id=work_product.tenant_id,
        matter_id=work_product.matter_id,
        evaluated_at=instant,
        checks=checks,
        outcome=outcome,
    )


def evaluate_release_ready_gate(
    *,
    work_product: WorkProduct,
    version: WorkProductVersion,
    approval: Approval | None,
    grounding_claim_ids: tuple[DomainId, ...],
    claim_gates: tuple[GateEvaluation, ...],
    at: datetime,
) -> GateEvaluation:
    """Evaluate ``RELEASE_READY`` for one approved version, fail closed.

    The release is ready when the work product is approved through a valid,
    scope-matching approval that still names the exact current frozen
    version bytes, and every grounding claim still carries a passing
    ``CLAIM_READY`` evaluation. Any change to the artifact after the
    approval, including a superseded or replaced current version, fails the
    changed-artifact check and cannot be waived.
    """

    instant = require_utc(at)
    for gate in claim_gates:
        if gate.gate is not GateKind.CLAIM_READY:
            raise ValueError("claim readiness requires claim_ready evaluations")
        if gate.evaluated_at > instant:
            raise ValueError("claim gate evaluation cannot postdate the gate")

    approved_reasons: list[GateFailureReason] = []
    if work_product.status is not WorkProductStatus.APPROVED:
        approved_reasons.append(GateFailureReason.WORK_PRODUCT_NOT_APPROVED)
    if approval is not None and work_product.approval_id != approval.id:
        approved_reasons.append(GateFailureReason.APPROVAL_LINKAGE_MISSING)
    approved_checks = (
        _check(
            GateCheckId.RELEASE_WORK_PRODUCT_APPROVED,
            work_product.id,
            tuple(approved_reasons),
        ),
    )

    validity_reasons: list[GateFailureReason] = []
    if approval is None:
        validity_reasons.append(GateFailureReason.APPROVAL_MISSING)
    else:
        if (
            approval.tenant_id != work_product.tenant_id
            or approval.matter_id != work_product.matter_id
        ):
            validity_reasons.append(GateFailureReason.APPROVAL_SCOPE_CROSSING)
        if approval.status is ApprovalStatus.REVOKED:
            validity_reasons.append(GateFailureReason.APPROVAL_REVOKED)
        elif approval.status is not ApprovalStatus.APPROVED:
            validity_reasons.append(GateFailureReason.APPROVAL_NOT_APPROVED)
        if approval.decided_at is not None and approval.decided_at > instant:
            validity_reasons.append(GateFailureReason.APPROVAL_FUTURE)
    validity_checks = (
        _check(
            GateCheckId.RELEASE_APPROVAL_VALID,
            approval.id if approval is not None else work_product.id,
            tuple(validity_reasons),
        ),
    )

    artifact_reasons: list[GateFailureReason] = []
    if approval is None:
        artifact_reasons.append(GateFailureReason.APPROVAL_MISSING)
    else:
        artifact_reasons.extend(_version_binding_reasons(work_product, version))
        if work_product.current_version_id != version.id:
            # The approved version was replaced as the current one after the
            # approval decision, so the artifact changed after approval.
            artifact_reasons.append(GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL)
        if version.status is WorkProductVersionStatus.SUPERSEDED:
            artifact_reasons.append(GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL)
        subject = approval.subject
        if (
            subject.artifact_id != version.id
            or subject.artifact_version != version.version_number
            or subject.content_sha256 != version.content_sha256
        ):
            artifact_reasons.append(GateFailureReason.ARTIFACT_CHANGED_AFTER_APPROVAL)
    artifact_checks = (
        _check(
            GateCheckId.RELEASE_ARTIFACT_UNCHANGED,
            version.id,
            tuple(artifact_reasons),
        ),
    )

    claim_checks = _claim_readiness_checks(
        GateCheckId.RELEASE_CLAIMS_READY,
        _claim_readiness_reasons(
            grounding_claim_ids=grounding_claim_ids,
            claim_gates=claim_gates,
            work_product=work_product,
        ),
        grounding_claim_ids,
        work_product.id,
    )

    checks = approved_checks + validity_checks + artifact_checks + claim_checks
    outcome = (
        ValidationOutcome.PASSED
        if all(check.passed for check in checks)
        else ValidationOutcome.FAILED
    )
    return GateEvaluation(
        gate=GateKind.RELEASE_READY,
        subject_id=work_product.id,
        tenant_id=work_product.tenant_id,
        matter_id=work_product.matter_id,
        evaluated_at=instant,
        checks=checks,
        outcome=outcome,
    )


__all__ = [
    "BlindChallengeRecord",
    "ChallengeDefect",
    "ChallengeDefectKind",
    "ChallengeIndependence",
    "ChallengeOutcome",
    "GateCheck",
    "GateCheckId",
    "GateEvaluation",
    "GateFailureReason",
    "GateKind",
    "ModelRunIdentity",
    "evaluate_claim_ready_gate",
    "evaluate_draft_ready_gate",
    "evaluate_release_ready_gate",
    "label_challenge_independence",
]
