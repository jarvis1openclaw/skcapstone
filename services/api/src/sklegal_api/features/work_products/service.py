"""Fail-closed Work Product and exact Approval application service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from difflib import unified_diff
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sklegal_domain.claim_grounded import (
    split_draft_sentences,
)
from sklegal_persistence.features.work_products.models import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalValidity,
    AuthorizationEvidence,
    SentenceGroundingRecord,
    ValidationOutcome,
    ValidationRecord,
    VersionBinding,
    VersionStatus,
    WorkProductAggregate,
    WorkProductAuditEvent,
    WorkProductComparison,
    WorkProductOutboxEvent,
    WorkProductStatus,
    WorkProductVersionRecord,
    canonical_sha256,
    content_sha256,
)
from sklegal_persistence.features.work_products.repository import (
    WorkProductIdempotencyConflict,
    WorkProductNotFound,
    WorkProductRepository,
    WorkProductRepositoryUnavailable,
    WorkProductVersionConflict,
)

from .contracts import (
    CreateVersionBody,
    CreateWorkProductBody,
    DecideApprovalBody,
    GroundSentenceBody,
    RequestApprovalBody,
    RevokeApprovalBody,
    SupersedeApprovalBody,
    ValidateWorkProductBody,
)


class WorkProductServiceError(RuntimeError):
    """Stable, sanitized service failure."""

    def __init__(self, code: str, *, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


WorkProductCapability = Literal[
    "work_product.draft", "work_product.approve", "matter.read"
]
WorkProductPurpose = Literal[
    "work_product_preparation", "human_approval", "matter_management"
]


@dataclass(frozen=True, slots=True)
class WorkProductActor:
    tenant_id: UUID
    principal_id: UUID
    authorized_matter_id: UUID
    decision_id: UUID
    correlation_id: UUID
    capability: WorkProductCapability
    purpose: WorkProductPurpose
    policy_revision: str
    revocation_revision: str
    credential_digest: str
    ancestor_credential_digests: tuple[str, ...]
    authorized_at: datetime
    expires_at: datetime

    @property
    def evidence(self) -> AuthorizationEvidence:
        return AuthorizationEvidence(
            tenant_id=self.tenant_id,
            matter_id=self.authorized_matter_id,
            decision_id=self.decision_id,
            principal_id=self.principal_id,
            capability=self.capability,
            purpose=self.purpose,
            policy_revision=self.policy_revision,
            revocation_revision=self.revocation_revision,
            credential_digest=self.credential_digest,
            ancestor_credential_digests=self.ancestor_credential_digests,
            authorized_at=self.authorized_at,
            expires_at=self.expires_at,
        )


class WorkProductService:
    """Orchestrate immutable versions and exact Approval lifecycle records."""

    def __init__(
        self,
        *,
        repository: WorkProductRepository,
        clock: Callable[[], datetime],
        id_factory: Callable[[], UUID],
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    def create(
        self,
        *,
        matter_id: UUID,
        command: CreateWorkProductBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize(
            actor,
            matter_id,
            capability="work_product.draft",
            purpose="work_product_preparation",
        )
        self._require_content_hash(command.content, command.content_sha256)
        fingerprint = self._fingerprint("create", matter_id, actor, command)
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        work_product_id = self._id_factory()
        version = WorkProductVersionRecord(
            version_id=self._id_factory(),
            version_number=1,
            content=command.content,
            content_sha256=command.content_sha256,
            source=command.source,
            created_by_principal_id=actor.principal_id,
            created_at=now,
        )
        aggregate = WorkProductAggregate(
            tenant_id=actor.tenant_id,
            matter_id=matter_id,
            work_product_id=work_product_id,
            aggregate_version=1,
            title=command.title,
            work_product_kind=command.work_product_kind,
            status=WorkProductStatus.DRAFT,
            current_version_id=version.version_id,
            versions=(version,),
            created_at=now,
            updated_at=now,
        )
        return self._commit(
            action="work_product.created",
            actor=actor,
            aggregate=aggregate,
            expected_version=None,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def create_version(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        command: CreateVersionBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_draft(actor, matter_id)
        self._require_content_hash(command.content, command.content_sha256)
        fingerprint = self._fingerprint(
            "create_version", matter_id, actor, command, work_product_id
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        self._require_binding(current, command.expected_current)
        if command.content_sha256 == current.current_version.content_sha256:
            raise WorkProductServiceError("unchanged_content", status_code=409)
        superseded = current.current_version.model_copy(
            update={"status": VersionStatus.SUPERSEDED, "superseded_at": now}
        )
        new_version = WorkProductVersionRecord(
            version_id=self._id_factory(),
            version_number=len(current.versions) + 1,
            content=command.content,
            content_sha256=command.content_sha256,
            source=command.source,
            created_by_principal_id=actor.principal_id,
            created_at=now,
        )
        approvals = tuple(
            self._invalidate_approval(
                item, current.current_version.binding, new_version, now
            )
            for item in current.approvals
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "status": WorkProductStatus.IN_REVIEW,
                "current_version_id": new_version.version_id,
                "versions": tuple(
                    superseded if item.version_id == superseded.version_id else item
                    for item in current.versions
                )
                + (new_version,),
                "approvals": approvals,
                "updated_at": now,
            }
        )
        return self._commit(
            action="work_product.version_created",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def ground_sentence(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        command: GroundSentenceBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_draft(actor, matter_id)
        fingerprint = self._fingerprint(
            "ground_sentence", matter_id, actor, command, work_product_id
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        version = self._require_binding(current, command.binding)
        sentence = next(
            (
                item
                for item in split_draft_sentences(version.content)
                if item.key == command.sentence_key
            ),
            None,
        )
        if sentence is None:
            raise WorkProductServiceError("sentence_not_found", status_code=409)
        try:
            claim_exists = self._repository.claim_exists(
                actor.tenant_id, matter_id, command.claim_id
            )
        except Exception:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None
        if not claim_exists:
            raise WorkProductServiceError("claim_not_found", status_code=404)
        if any(
            item.binding == command.binding
            and item.sentence_key == command.sentence_key
            for item in current.groundings
        ):
            raise WorkProductServiceError("sentence_already_grounded", status_code=409)
        grounding = SentenceGroundingRecord(
            grounding_id=self._id_factory(),
            binding=command.binding,
            sentence_key=command.sentence_key,
            claim_id=command.claim_id,
            source_span_start=sentence.start,
            source_span_end=sentence.end,
            grounded_by_principal_id=actor.principal_id,
            grounded_at=now,
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "groundings": current.groundings + (grounding,),
                "updated_at": now,
            }
        )
        return self._commit(
            action="work_product.sentence_grounded",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def validate(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        command: ValidateWorkProductBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_draft(actor, matter_id)
        fingerprint = self._fingerprint(
            "validate", matter_id, actor, command, work_product_id
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        version = self._require_binding(current, command.binding)
        sentence_keys = tuple(
            item.key for item in split_draft_sentences(version.content)
        )
        grounded = {
            item.sentence_key
            for item in current.groundings
            if item.binding == command.binding
        }
        missing = tuple(key for key in sentence_keys if key not in grounded)
        checks = set(command.check_ids)
        required = {"content_hash", "sentence_grounding", "source_lineage"}
        missing_checks = required - checks
        outcome = (
            ValidationOutcome.PASSED
            if not missing and not missing_checks
            else ValidationOutcome.FAILED
        )
        rationale = command.rationale
        if missing_checks:
            rationale = (
                f"{rationale}; missing checks: {','.join(sorted(missing_checks))}"
            )
        validation = ValidationRecord(
            validation_id=self._id_factory(),
            binding=command.binding,
            outcome=outcome,
            check_ids=command.check_ids,
            missing_sentence_keys=missing,
            validator_principal_id=actor.principal_id,
            validated_at=now,
            rationale=rationale,
            policy_revision=actor.policy_revision,
            authorization=actor.evidence,
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "status": (
                    WorkProductStatus.VALIDATED
                    if outcome is ValidationOutcome.PASSED
                    else WorkProductStatus.IN_REVIEW
                ),
                "validations": current.validations + (validation,),
                "updated_at": now,
            }
        )
        return self._commit(
            action="work_product.validated",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def request_approval(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        command: RequestApprovalBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_draft(actor, matter_id)
        fingerprint = self._fingerprint(
            "request_approval", matter_id, actor, command, work_product_id
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        self._require_binding(current, command.binding)
        validation = next(
            (
                item
                for item in current.validations
                if item.validation_id == command.validation_id
            ),
            None,
        )
        if (
            validation is None
            or validation.binding != command.binding
            or validation.outcome is not ValidationOutcome.PASSED
            or validation.policy_revision != actor.policy_revision
            or validation.authorization.principal_id
            != validation.validator_principal_id
            or validation.authorization.capability != "work_product.draft"
            or validation.authorization.purpose != "work_product_preparation"
            or not self._recorded_authorization_is_active(
                current, validation.authorization, validation.validated_at
            )
            or current.status is not WorkProductStatus.VALIDATED
        ):
            raise WorkProductServiceError("validation_not_current", status_code=409)
        if any(
            item.binding == command.binding and item.status is ApprovalStatus.PENDING
            for item in current.approvals
        ):
            raise WorkProductServiceError("approval_already_pending", status_code=409)
        approval = ApprovalRecord(
            approval_id=self._id_factory(),
            binding=command.binding,
            validation_id=validation.validation_id,
            status=ApprovalStatus.PENDING,
            requested_by_principal_id=actor.principal_id,
            requested_at=now,
            request_policy_revision=actor.policy_revision,
            request_authorization=actor.evidence,
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "approvals": current.approvals + (approval,),
                "updated_at": now,
            }
        )
        return self._commit(
            action="approval.requested",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def decide_approval(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        approval_id: UUID,
        command: DecideApprovalBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_approval(actor, matter_id)
        fingerprint = self._fingerprint(
            "decide_approval", matter_id, actor, command, work_product_id, approval_id
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        self._require_binding(current, command.binding)
        approval = self._approval(current, approval_id)
        if approval.status is not ApprovalStatus.PENDING:
            raise WorkProductServiceError("approval_not_pending", status_code=409)
        if approval.binding != command.binding:
            raise WorkProductServiceError("approval_binding_mismatch", status_code=409)
        validation = self._validation(current, approval.validation_id)
        if (
            validation.outcome is not ValidationOutcome.PASSED
            or validation.binding != command.binding
            or validation.policy_revision != actor.policy_revision
            or approval.request_policy_revision != actor.policy_revision
            or approval.request_authorization.principal_id
            != approval.requested_by_principal_id
            or approval.request_authorization.capability != "work_product.draft"
            or approval.request_authorization.purpose != "work_product_preparation"
            or not self._recorded_authorization_is_active(
                current, approval.request_authorization, approval.requested_at
            )
            or not self._recorded_authorization_is_active(
                current, validation.authorization, validation.validated_at
            )
            or current.status is not WorkProductStatus.VALIDATED
        ):
            raise WorkProductServiceError(
                "approval_preconditions_stale", status_code=409
            )
        status = ApprovalStatus(command.decision)
        decided = approval.model_copy(
            update={
                "status": status,
                "reviewer_principal_id": actor.principal_id,
                "decided_at": now,
                "rationale": command.rationale,
                "decision_policy_revision": actor.policy_revision,
                "decision_authorization": actor.evidence,
            }
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "status": (
                    WorkProductStatus.APPROVED
                    if status is ApprovalStatus.APPROVED
                    else WorkProductStatus.VALIDATED
                ),
                "approvals": self._replace_approval(current, decided),
                "updated_at": now,
            }
        )
        return self._commit(
            action=f"approval.{command.decision}",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def revoke_approval(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        approval_id: UUID,
        command: RevokeApprovalBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_approval(actor, matter_id)
        fingerprint = self._fingerprint(
            "revoke_approval", matter_id, actor, command, work_product_id, approval_id
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        self._require_binding(current, command.binding)
        approval = self._approval(current, approval_id)
        if approval.status is not ApprovalStatus.APPROVED:
            raise WorkProductServiceError("approval_not_approved", status_code=409)
        if approval.binding != command.binding:
            raise WorkProductServiceError("approval_binding_mismatch", status_code=409)
        revoked = approval.model_copy(
            update={
                "status": ApprovalStatus.REVOKED,
                "revoker_principal_id": actor.principal_id,
                "revoked_at": now,
                "revocation_rationale": command.rationale,
                "revocation_authorization": actor.evidence,
            }
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "status": WorkProductStatus.VALIDATED,
                "approvals": self._replace_approval(current, revoked),
                "updated_at": now,
            }
        )
        return self._commit(
            action="approval.revoked",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def supersede_approval(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        approval_id: UUID,
        command: SupersedeApprovalBody,
        idempotency_key: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        now = self._authorize_approval(actor, matter_id)
        fingerprint = self._fingerprint(
            "supersede_approval",
            matter_id,
            actor,
            command,
            work_product_id,
            approval_id,
        )
        prior = self._find_replay(actor, matter_id, idempotency_key, fingerprint)
        if prior is not None:
            return prior
        current = self._load_exact(
            actor, matter_id, work_product_id, command.expected_aggregate_version
        )
        prior_approval = self._approval(current, approval_id)
        successor = self._approval(current, command.superseding_approval_id)
        if prior_approval.status not in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.SUPERSEDED,
        }:
            raise WorkProductServiceError("approval_not_supersedable", status_code=409)
        if successor.status is not ApprovalStatus.APPROVED:
            raise WorkProductServiceError(
                "superseding_approval_not_valid", status_code=409
            )
        if (
            successor.binding != command.superseding_binding
            or successor.binding != current.current_version.binding
            or self._approval_invalid_reason(
                current, successor, command.superseding_binding, now
            )
            is not None
        ):
            raise WorkProductServiceError(
                "superseding_approval_not_valid", status_code=409
            )
        if successor.binding == prior_approval.binding:
            raise WorkProductServiceError(
                "supersession_requires_new_version", status_code=409
            )
        superseded = prior_approval.model_copy(
            update={
                "status": ApprovalStatus.SUPERSEDED,
                "superseded_at": now,
                "superseded_by_approval_id": successor.approval_id,
                "superseding_version_id": successor.binding.work_product_version_id,
            }
        )
        aggregate = current.model_copy(
            update={
                "aggregate_version": current.aggregate_version + 1,
                "approvals": self._replace_approval(current, superseded),
                "updated_at": now,
            }
        )
        return self._commit(
            action="approval.superseded",
            actor=actor,
            aggregate=aggregate,
            expected_version=current.aggregate_version,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
        )

    def get(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        actor: WorkProductActor,
    ) -> WorkProductAggregate:
        self._authorize_read(actor, matter_id)
        return self._load(actor, matter_id, work_product_id)

    def list(
        self, *, matter_id: UUID, actor: WorkProductActor
    ) -> tuple[WorkProductAggregate, ...]:
        self._authorize_read(actor, matter_id)
        try:
            return self._repository.list_for_matter(actor.tenant_id, matter_id)
        except Exception:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None

    def compare(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        left_version_id: UUID,
        right_version_id: UUID,
        actor: WorkProductActor,
    ) -> WorkProductComparison:
        self._authorize_read(actor, matter_id)
        aggregate = self._load(actor, matter_id, work_product_id)
        left = self._version(aggregate, left_version_id)
        right = self._version(aggregate, right_version_id)
        lines = tuple(
            unified_diff(
                left.content.splitlines(),
                right.content.splitlines(),
                fromfile=f"version-{left.version_number}",
                tofile=f"version-{right.version_number}",
                lineterm="",
            )
        )
        return WorkProductComparison(
            work_product_id=work_product_id,
            left=left.binding,
            right=right.binding,
            unified_diff=lines,
            changed=left.content_sha256 != right.content_sha256,
        )

    def approval_validity(
        self,
        *,
        matter_id: UUID,
        work_product_id: UUID,
        approval_id: UUID,
        binding: VersionBinding,
        actor: WorkProductActor,
    ) -> ApprovalValidity:
        now = self._authorize_read(actor, matter_id)
        aggregate = self._load(actor, matter_id, work_product_id)
        approval = self._approval(aggregate, approval_id)
        reason = self._approval_invalid_reason(aggregate, approval, binding, now)
        return ApprovalValidity(
            approval_id=approval_id,
            binding=binding,
            valid=reason is None,
            reason_code=reason or "approval_valid",
        )

    def _approval_invalid_reason(
        self,
        aggregate: WorkProductAggregate,
        approval: ApprovalRecord,
        binding: VersionBinding,
        now: datetime,
    ) -> str | None:
        if approval.status is not ApprovalStatus.APPROVED:
            return f"approval_{approval.status.value}"
        if aggregate.status is not WorkProductStatus.APPROVED:
            return "work_product_not_approved"
        if approval.binding != binding or aggregate.current_version.binding != binding:
            return "approval_subject_stale"
        if any(
            value is None
            for value in (
                approval.reviewer_principal_id,
                approval.decided_at,
                approval.rationale,
                approval.decision_policy_revision,
                approval.decision_authorization,
            )
        ):
            return "approval_decision_evidence_incomplete"
        decision_authorization = approval.decision_authorization
        decided_at = approval.decided_at
        assert decision_authorization is not None
        assert decided_at is not None
        try:
            validation = self._validation(aggregate, approval.validation_id)
        except WorkProductServiceError:
            return "approval_validation_missing"
        if (
            approval.request_authorization.principal_id
            != approval.requested_by_principal_id
            or approval.request_authorization.capability != "work_product.draft"
            or approval.request_authorization.purpose != "work_product_preparation"
            or decision_authorization.principal_id != approval.reviewer_principal_id
            or decision_authorization.capability != "work_product.approve"
            or decision_authorization.purpose != "human_approval"
            or validation.authorization.principal_id
            != validation.validator_principal_id
            or validation.authorization.capability != "work_product.draft"
            or validation.authorization.purpose != "work_product_preparation"
        ):
            return "approval_capability_evidence_invalid"
        if (
            validation.outcome is not ValidationOutcome.PASSED
            or validation.binding != binding
        ):
            return "approval_validation_stale"
        try:
            policy = self._repository.current_policy_revision(
                aggregate.tenant_id, aggregate.matter_id
            )
            request_active = self._repository.authorization_is_active(
                aggregate.tenant_id,
                aggregate.matter_id,
                approval.request_authorization,
                approval.requested_at,
            )
            decision_active = self._repository.authorization_is_active(
                aggregate.tenant_id,
                aggregate.matter_id,
                decision_authorization,
                decided_at,
            )
            validation_active = self._repository.authorization_is_active(
                aggregate.tenant_id,
                aggregate.matter_id,
                validation.authorization,
                validation.validated_at,
            )
        except Exception:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None
        if (
            approval.request_policy_revision != policy
            or approval.request_authorization.policy_revision != policy
            or approval.decision_policy_revision != policy
            or decision_authorization.policy_revision != policy
            or validation.policy_revision != policy
            or validation.authorization.policy_revision != policy
        ):
            return "approval_policy_stale"
        if not request_active or not decision_active or not validation_active:
            return "approval_capability_revoked"
        if decided_at > now:
            return "approval_decision_time_invalid"
        return None

    def _authorize_draft(self, actor: WorkProductActor, matter_id: UUID) -> datetime:
        return self._authorize(
            actor,
            matter_id,
            capability="work_product.draft",
            purpose="work_product_preparation",
        )

    def _authorize_approval(self, actor: WorkProductActor, matter_id: UUID) -> datetime:
        return self._authorize(
            actor,
            matter_id,
            capability="work_product.approve",
            purpose="human_approval",
        )

    def _authorize_read(self, actor: WorkProductActor, matter_id: UUID) -> datetime:
        return self._authorize(
            actor,
            matter_id,
            capability="matter.read",
            purpose="matter_management",
        )

    def _authorize(
        self,
        actor: WorkProductActor,
        matter_id: UUID,
        *,
        capability: str,
        purpose: str,
    ) -> datetime:
        if actor.authorized_matter_id != matter_id:
            raise WorkProductServiceError(
                "authorization_scope_mismatch", status_code=403
            )
        if actor.capability != capability or actor.purpose != purpose:
            raise WorkProductServiceError(
                "operation_capability_mismatch", status_code=403
            )
        try:
            evidence = actor.evidence
        except ValueError:
            raise WorkProductServiceError(
                "authorization_evidence_incomplete", status_code=503
            ) from None
        now = self._clock()
        try:
            policy = self._repository.current_policy_revision(
                actor.tenant_id, matter_id
            )
            active = self._repository.authorization_is_active(
                actor.tenant_id, matter_id, evidence, now
            )
            member = self._repository.is_matter_member(
                actor.tenant_id, matter_id, actor.principal_id
            )
        except Exception:
            raise WorkProductServiceError(
                "work_product_authorization_unavailable", status_code=503
            ) from None
        if policy != actor.policy_revision:
            raise WorkProductServiceError("stale_policy_evidence", status_code=409)
        if not active:
            raise WorkProductServiceError("capability_inactive", status_code=403)
        if not member:
            raise WorkProductServiceError("matter_membership_denied", status_code=403)
        return now

    def _find_replay(
        self,
        actor: WorkProductActor,
        matter_id: UUID,
        idempotency_key: UUID,
        fingerprint: str,
    ) -> WorkProductAggregate | None:
        try:
            prior = self._repository.find_idempotent(
                actor.tenant_id, matter_id, idempotency_key
            )
        except Exception:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None
        if prior is None:
            return None
        if prior[0] != fingerprint:
            raise WorkProductServiceError("idempotency_key_reused", status_code=409)
        return prior[1]

    def _load(
        self, actor: WorkProductActor, matter_id: UUID, work_product_id: UUID
    ) -> WorkProductAggregate:
        try:
            aggregate = self._repository.get(
                actor.tenant_id, matter_id, work_product_id
            )
        except Exception:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None
        if aggregate is None:
            raise WorkProductServiceError("work_product_not_found", status_code=404)
        return aggregate

    def _recorded_authorization_is_active(
        self,
        aggregate: WorkProductAggregate,
        evidence: AuthorizationEvidence,
        at: datetime,
    ) -> bool:
        try:
            return self._repository.authorization_is_active(
                aggregate.tenant_id, aggregate.matter_id, evidence, at
            )
        except Exception:
            raise WorkProductServiceError(
                "work_product_authorization_unavailable", status_code=503
            ) from None

    def _load_exact(
        self,
        actor: WorkProductActor,
        matter_id: UUID,
        work_product_id: UUID,
        expected_aggregate_version: int,
    ) -> WorkProductAggregate:
        aggregate = self._load(actor, matter_id, work_product_id)
        if aggregate.aggregate_version != expected_aggregate_version:
            raise WorkProductServiceError(
                "work_product_version_conflict", status_code=409
            )
        return aggregate

    @staticmethod
    def _require_binding(
        aggregate: WorkProductAggregate, binding: VersionBinding
    ) -> WorkProductVersionRecord:
        if aggregate.current_version.binding != binding:
            raise WorkProductServiceError(
                "work_product_binding_mismatch", status_code=409
            )
        return aggregate.current_version

    @staticmethod
    def _require_content_hash(content: str, digest: str) -> None:
        if content_sha256(content) != digest:
            raise WorkProductServiceError("content_hash_mismatch", status_code=409)

    @staticmethod
    def _version(
        aggregate: WorkProductAggregate, version_id: UUID
    ) -> WorkProductVersionRecord:
        try:
            return next(
                item for item in aggregate.versions if item.version_id == version_id
            )
        except StopIteration:
            raise WorkProductServiceError(
                "work_product_version_not_found", status_code=404
            ) from None

    @staticmethod
    def _approval(aggregate: WorkProductAggregate, approval_id: UUID) -> ApprovalRecord:
        try:
            return next(
                item for item in aggregate.approvals if item.approval_id == approval_id
            )
        except StopIteration:
            raise WorkProductServiceError(
                "approval_not_found", status_code=404
            ) from None

    @staticmethod
    def _validation(
        aggregate: WorkProductAggregate, validation_id: UUID
    ) -> ValidationRecord:
        try:
            return next(
                item
                for item in aggregate.validations
                if item.validation_id == validation_id
            )
        except StopIteration:
            raise WorkProductServiceError(
                "validation_not_found", status_code=404
            ) from None

    @staticmethod
    def _replace_approval(
        aggregate: WorkProductAggregate, replacement: ApprovalRecord
    ) -> tuple[ApprovalRecord, ...]:
        return tuple(
            replacement if item.approval_id == replacement.approval_id else item
            for item in aggregate.approvals
        )

    @staticmethod
    def _invalidate_approval(
        approval: ApprovalRecord,
        prior_binding: VersionBinding,
        successor: WorkProductVersionRecord,
        at: datetime,
    ) -> ApprovalRecord:
        if approval.binding != prior_binding or approval.status not in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        }:
            return approval
        return approval.model_copy(
            update={
                "status": ApprovalStatus.SUPERSEDED,
                "superseded_at": at,
                "superseding_version_id": successor.version_id,
            }
        )

    @staticmethod
    def _fingerprint(
        action: str,
        matter_id: UUID,
        actor: WorkProductActor,
        command: BaseModel,
        *resource_ids: UUID,
    ) -> str:
        return canonical_sha256(
            {
                "action": action,
                "tenantId": str(actor.tenant_id),
                "matterId": str(matter_id),
                "principalId": str(actor.principal_id),
                "resourceIds": [str(value) for value in resource_ids],
                "command": command.model_dump(mode="json", by_alias=True),
            }
        )

    def _commit(
        self,
        *,
        action: str,
        actor: WorkProductActor,
        aggregate: WorkProductAggregate,
        expected_version: int | None,
        idempotency_key: UUID,
        request_sha256: str,
    ) -> WorkProductAggregate:
        aggregate = WorkProductAggregate.model_validate(aggregate.model_dump())
        event_id = self._id_factory()
        digest = canonical_sha256(aggregate)
        audit = WorkProductAuditEvent(
            event_id=event_id,
            tenant_id=aggregate.tenant_id,
            matter_id=aggregate.matter_id,
            work_product_id=aggregate.work_product_id,
            aggregate_version=aggregate.aggregate_version,
            correlation_id=actor.correlation_id,
            actor_principal_id=actor.principal_id,
            action=action,
            outcome="completed",
            subject_sha256=digest,
            occurred_at=aggregate.updated_at,
        )
        outbox = WorkProductOutboxEvent(
            outbox_id=event_id,
            event_id=event_id,
            tenant_id=aggregate.tenant_id,
            matter_id=aggregate.matter_id,
            work_product_id=aggregate.work_product_id,
            aggregate_version=aggregate.aggregate_version,
            payload_sha256=digest,
            available_at=aggregate.updated_at,
        )
        try:
            return self._repository.commit(
                expected_aggregate_version=expected_version,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                aggregate=aggregate,
                audit=audit,
                outbox=outbox,
            )
        except WorkProductIdempotencyConflict:
            raise WorkProductServiceError(
                "idempotency_key_reused", status_code=409
            ) from None
        except WorkProductVersionConflict:
            raise WorkProductServiceError(
                "work_product_version_conflict", status_code=409
            ) from None
        except WorkProductNotFound:
            raise WorkProductServiceError(
                "work_product_not_found", status_code=404
            ) from None
        except WorkProductRepositoryUnavailable:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None
        except Exception:
            raise WorkProductServiceError(
                "work_product_store_unavailable", status_code=503
            ) from None
