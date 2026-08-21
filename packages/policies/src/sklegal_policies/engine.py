"""Deterministic legal information-barrier and retention decisions."""

from __future__ import annotations

from datetime import datetime, timedelta

from sklegal_capauth import ModelRoute
from sklegal_domain import DataClassification

from .models import (
    AccessState,
    ClassificationSource,
    ConflictDisposition,
    DataFlowBoundary,
    MaterialPolicyFacts,
    PolicyAccessRequest,
    PolicyReason,
    PrivilegeLabel,
    ProtectedAccessLevel,
    RetentionDecision,
    RetentionRequest,
    WallMembershipDisposition,
    WorkProductLabel,
)

_CLASSIFICATION_RANK = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.PRIVILEGED_WORK_PRODUCT: 3,
    DataClassification.HIGHLY_RESTRICTED: 4,
}


def effective_classification(
    sources: tuple[ClassificationSource, ...],
    *,
    privilege_labels: tuple[PrivilegeLabel, ...] = (),
    work_product_labels: tuple[WorkProductLabel, ...] = (),
) -> DataClassification:
    """Apply most-restrictive inheritance and active protective labels."""

    labels_active = any(item.active for item in privilege_labels) or any(
        item.active for item in work_product_labels
    )
    values = [source.classification for source in sources]
    if labels_active:
        values.append(DataClassification.PRIVILEGED_WORK_PRODUCT)
    if not values:
        return DataClassification.CONFIDENTIAL
    return max(values, key=_CLASSIFICATION_RANK.__getitem__)


class PolicyEngine:
    """Pure reducer over a complete request-local policy snapshot."""

    @staticmethod
    def _conflict_reason(
        facts: MaterialPolicyFacts,
        *,
        evaluated_at: datetime,
    ) -> PolicyReason | None:
        if not facts.conflict_state_complete or facts.conflict_decision is None:
            return PolicyReason.CONFLICT_UNRESOLVED
        conflict = facts.conflict_decision
        if conflict.decided_at > evaluated_at:
            return PolicyReason.CONFLICT_UNRESOLVED
        if conflict.disposition == ConflictDisposition.HOLD:
            return PolicyReason.CONFLICT_HOLD
        if conflict.disposition == ConflictDisposition.WAIVED:
            waiver = conflict.waiver_reference
            if waiver is None or not waiver.is_effective(evaluated_at):
                return PolicyReason.WAIVER_INVALID
        return None

    def reason_for_access(
        self, request: PolicyAccessRequest, facts: MaterialPolicyFacts
    ) -> tuple[PolicyReason, DataClassification | None]:
        if (
            facts.tenant_id != request.tenant_id
            or facts.matter_id != request.matter_id
            or facts.material_id != request.material_id
            or facts.material_version != request.material_version
        ):
            return PolicyReason.POLICY_SCOPE_MISMATCH, None
        if AccessState.UNKNOWN in {
            facts.tenant_membership,
            facts.matter_membership,
        }:
            return PolicyReason.MEMBERSHIP_UNKNOWN, None
        if facts.tenant_membership != AccessState.ACTIVE:
            return PolicyReason.TENANT_MEMBERSHIP_REQUIRED, None
        if facts.matter_membership != AccessState.ACTIVE:
            return PolicyReason.MATTER_MEMBERSHIP_REQUIRED, None

        conflict_reason = self._conflict_reason(
            facts,
            evaluated_at=request.evaluated_at,
        )
        if conflict_reason is not None:
            return conflict_reason, None
        if any(item.is_active(request.evaluated_at) for item in facts.conflict_holds):
            return PolicyReason.CONFLICT_HOLD, None

        if not facts.wall_state_complete:
            return PolicyReason.WALL_STATE_UNKNOWN, None
        for wall in facts.walls:
            if not wall.is_active(request.evaluated_at):
                continue
            if not wall.membership_complete:
                return PolicyReason.WALL_STATE_UNKNOWN, None
            memberships = tuple(
                membership
                for membership in facts.wall_memberships
                if membership.wall_id == wall.wall_id
                and membership.principal_id == request.principal_id
                and membership.is_active(request.evaluated_at)
            )
            if any(
                item.disposition == WallMembershipDisposition.EXCLUDED
                for item in memberships
            ):
                return PolicyReason.WALL_EXCLUDED, None
            if not any(
                item.disposition == WallMembershipDisposition.ALLOWED
                for item in memberships
            ):
                return PolicyReason.WALL_GRANT_REQUIRED, None

        if not facts.classification_state_complete or not facts.classification_sources:
            return PolicyReason.CLASSIFICATION_UNRESOLVED, None
        classification = effective_classification(
            facts.classification_sources,
            privilege_labels=facts.privilege_labels,
            work_product_labels=facts.work_product_labels,
        )
        if classification in {
            DataClassification.PRIVILEGED_WORK_PRODUCT,
            DataClassification.HIGHLY_RESTRICTED,
        }:
            required = (
                ProtectedAccessLevel.HIGHLY_RESTRICTED
                if classification == DataClassification.HIGHLY_RESTRICTED
                else ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT
            )
            grants = tuple(
                grant
                for grant in facts.protected_access_grants
                if grant.principal_id == request.principal_id
                and grant.purpose == request.purpose
                and grant.is_active(request.evaluated_at)
            )
            sufficient = any(
                grant.access_level == required
                or (
                    required == ProtectedAccessLevel.PRIVILEGED_WORK_PRODUCT
                    and grant.access_level == ProtectedAccessLevel.HIGHLY_RESTRICTED
                )
                for grant in grants
            )
            if not sufficient:
                return PolicyReason.PROTECTED_ACCESS_REQUIRED, classification

        if request.boundary == DataFlowBoundary.MODEL_CONTEXT and classification in {
            DataClassification.PRIVILEGED_WORK_PRODUCT,
            DataClassification.HIGHLY_RESTRICTED,
        }:
            return PolicyReason.MODEL_CONTEXT_DENIED, classification
        if (
            request.boundary == DataFlowBoundary.MODEL_CONTEXT
            and request.model_route == ModelRoute.OPENAI
            and classification
            in {
                DataClassification.CONFIDENTIAL,
                DataClassification.PRIVILEGED_WORK_PRODUCT,
                DataClassification.HIGHLY_RESTRICTED,
            }
        ):
            return PolicyReason.EXTERNAL_MODEL_DENIED, classification
        if request.boundary == DataFlowBoundary.AUDIT_DETAIL and classification in {
            DataClassification.CONFIDENTIAL,
            DataClassification.PRIVILEGED_WORK_PRODUCT,
            DataClassification.HIGHLY_RESTRICTED,
        }:
            return PolicyReason.AUDIT_DETAIL_DENIED, classification
        return PolicyReason.ALLOW, classification

    def decide_retention(
        self, request: RetentionRequest, facts: MaterialPolicyFacts
    ) -> RetentionDecision:
        reason = PolicyReason.ALLOW
        active_hold = False
        if (
            facts.tenant_id != request.tenant_id
            or facts.matter_id != request.matter_id
            or facts.material_id != request.material_id
            or facts.material_version != request.material_version
        ):
            reason = PolicyReason.POLICY_SCOPE_MISMATCH
        elif AccessState.UNKNOWN in {
            facts.tenant_membership,
            facts.matter_membership,
        }:
            reason = PolicyReason.MEMBERSHIP_UNKNOWN
        elif facts.tenant_membership != AccessState.ACTIVE:
            reason = PolicyReason.TENANT_MEMBERSHIP_REQUIRED
        elif facts.matter_membership != AccessState.ACTIVE:
            reason = PolicyReason.MATTER_MEMBERSHIP_REQUIRED
        else:
            conflict_reason = self._conflict_reason(
                facts,
                evaluated_at=request.evaluated_at,
            )
            if conflict_reason is not None:
                reason = conflict_reason
        if reason == PolicyReason.ALLOW and not facts.legal_hold_state_complete:
            reason = PolicyReason.HOLD_STATE_UNKNOWN
        elif reason == PolicyReason.ALLOW:
            released_hold_ids = {
                hold.supersedes_hold_id
                for hold in facts.legal_holds
                if hold.status.value == "released"
                and hold.released_at is not None
                and hold.released_at <= request.evaluated_at
            }
            active_hold = any(
                hold.legal_hold_id not in released_hold_ids
                and hold.is_active_for(request.material_id, request.evaluated_at)
                for hold in facts.legal_holds
            )
        if reason == PolicyReason.ALLOW and active_hold:
            reason = PolicyReason.LEGAL_HOLD_ACTIVE
        elif reason == PolicyReason.ALLOW and any(
            hold.is_active(request.evaluated_at) for hold in facts.conflict_holds
        ):
            reason = PolicyReason.CONFLICT_HOLD
        elif reason == PolicyReason.ALLOW and facts.preservation_required:
            reason = PolicyReason.PRESERVATION_REQUIRED
        elif reason == PolicyReason.ALLOW and not facts.ownership_resolved:
            reason = PolicyReason.OWNERSHIP_UNRESOLVED
        elif reason == PolicyReason.ALLOW and facts.pending_export:
            reason = PolicyReason.PENDING_EXPORT
        elif reason == PolicyReason.ALLOW and (
            facts.retention_policy is None
            or not facts.retention_policy.is_active(request.evaluated_at)
        ):
            reason = PolicyReason.RETENTION_POLICY_UNAVAILABLE
        elif (
            reason == PolicyReason.ALLOW
            and facts.retention_policy is not None
            and request.evaluated_at
            < request.material_created_at
            + timedelta(days=facts.retention_policy.retain_for_days)
        ):
            reason = PolicyReason.RETENTION_NOT_DUE
        return RetentionDecision(
            principal_id=request.principal_id,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            material_id=request.material_id,
            material_version=request.material_version,
            allow=reason == PolicyReason.ALLOW,
            reason=reason,
            policy_revision=facts.policy_revision,
            evaluated_at=request.evaluated_at,
        )
