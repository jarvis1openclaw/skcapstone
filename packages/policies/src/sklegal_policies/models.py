"""Strict policy records and sanitized decision contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sklegal_capauth import (
    CAPABILITY_RULES,
    Audience,
    Capability,
    CapabilityGrant,
    ModelRoute,
    Purpose,
)
from sklegal_domain import DataClassification

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ShortCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
    ),
]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("policy timestamps must use UTC offset zero")
    return value


def _contains_nil_uuid(value: object) -> bool:
    if isinstance(value, UUID):
        return value.int == 0
    if isinstance(value, BaseModel):
        return any(_contains_nil_uuid(item) for item in value.__dict__.values())
    if isinstance(value, Mapping):
        return any(_contains_nil_uuid(item) for item in value.values())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_nil_uuid(item) for item in value)
    return False


class PolicyValue(BaseModel):
    """Immutable strict base for policy boundary values."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @model_validator(mode="after")
    def reject_nil_identifiers(self) -> Self:
        if _contains_nil_uuid(self.__dict__):
            raise ValueError("policy identifiers cannot be nil UUIDs")
        return self

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update:
            raise ValueError("unvalidated policy copy updates are disabled")
        return super().model_copy(deep=deep)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if include is not None or exclude is not None or update:
            raise ValueError("unvalidated policy copy changes are disabled")
        return self.model_copy(deep=deep)

    def __replace__(self, **changes: Any) -> Self:
        if changes:
            raise ValueError("unvalidated policy replacement is disabled")
        return self.model_copy()

    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        del _fields_set, values
        raise ValueError("unvalidated policy construction is disabled")


class AccessState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class ConflictDisposition(StrEnum):
    CLEAR = "clear"
    HOLD = "hold"
    WAIVED = "waived"


class PartyRelationship(StrEnum):
    CLIENT = "client"
    ADVERSE_PARTY = "adverse_party"
    RELATED = "related"
    OTHER = "other"


class WallMembershipDisposition(StrEnum):
    ALLOWED = "allowed"
    EXCLUDED = "excluded"


class ProtectedAccessLevel(StrEnum):
    PRIVILEGED_WORK_PRODUCT = "privileged_work_product"
    HIGHLY_RESTRICTED = "highly_restricted"


class LegalHoldStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"


class DataFlowBoundary(StrEnum):
    RETRIEVAL = "retrieval"
    CACHE = "cache"
    MODEL_CONTEXT = "model_context"
    EXPORT = "export"
    AUDIT_DETAIL = "audit_detail"


class PolicyReason(StrEnum):
    ALLOW = "allow"
    CAPAUTH_SCOPE_MISMATCH = "capauth_scope_mismatch"
    POLICY_SCOPE_MISMATCH = "policy_scope_mismatch"
    POLICY_UNAVAILABLE = "policy_unavailable"
    AUDIT_UNAVAILABLE = "audit_unavailable"
    CAPAUTH_EXPIRED = "capauth_expired"
    CAPAUTH_REPLAYED = "capauth_replayed"
    CAPAUTH_STALE = "capauth_stale"
    CAPAUTH_CURRENT_STATE_UNAVAILABLE = "capauth_current_state_unavailable"
    MEMBERSHIP_UNKNOWN = "membership_unknown"
    TENANT_MEMBERSHIP_REQUIRED = "tenant_membership_required"
    MATTER_MEMBERSHIP_REQUIRED = "matter_membership_required"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    CONFLICT_HOLD = "conflict_hold"
    WAIVER_INVALID = "waiver_invalid"
    WALL_STATE_UNKNOWN = "wall_state_unknown"
    WALL_GRANT_REQUIRED = "wall_grant_required"
    WALL_EXCLUDED = "wall_excluded"
    CLASSIFICATION_UNRESOLVED = "classification_unresolved"
    PROTECTED_ACCESS_REQUIRED = "protected_access_required"
    MODEL_CONTEXT_DENIED = "model_context_denied"
    EXTERNAL_MODEL_DENIED = "external_model_denied"
    AUDIT_DETAIL_DENIED = "audit_detail_denied"
    RETENTION_POLICY_UNAVAILABLE = "retention_policy_unavailable"
    RETENTION_NOT_DUE = "retention_not_due"
    HOLD_STATE_UNKNOWN = "hold_state_unknown"
    LEGAL_HOLD_ACTIVE = "legal_hold_active"
    PENDING_EXPORT = "pending_export"
    OWNERSHIP_UNRESOLVED = "ownership_unresolved"
    PRESERVATION_REQUIRED = "preservation_required"


class PartyCandidate(PolicyValue):
    party_id: UUID
    tenant_id: UUID
    matter_id: UUID
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    party_kind: Literal["person", "family", "trust", "estate", "company", "other"]
    proposed_relationship: PartyRelationship


class KnownPartyAssociation(PolicyValue):
    association_id: UUID
    tenant_id: UUID
    matter_id: UUID
    party_id: UUID
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    party_kind: Literal["person", "family", "trust", "estate", "company", "other"]
    relationship: PartyRelationship
    active: bool


class ConflictMatch(PolicyValue):
    match_id: UUID = Field(default_factory=uuid4)
    candidate_party_id: UUID
    association_id: UUID
    existing_matter_id: UUID
    normalized_identity_digest: Sha256
    collision_kind: Literal["exact_normalized_name"] = "exact_normalized_name"


class ConflictCheckResult(PolicyValue):
    check_id: UUID
    tenant_id: UUID
    matter_id: UUID
    candidate_party_ids: tuple[UUID, ...]
    matches: tuple[ConflictMatch, ...]
    recommended_disposition: ConflictDisposition
    checked_at: datetime
    normalization_version: Literal["sklegal-party-normalization/v1"] = (
        "sklegal-party-normalization/v1"
    )

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        require_utc(self.checked_at)
        if len(self.candidate_party_ids) != len(set(self.candidate_party_ids)):
            raise ValueError("candidate party identifiers must be unique")
        expected = (
            ConflictDisposition.HOLD if self.matches else ConflictDisposition.CLEAR
        )
        if self.recommended_disposition != expected:
            raise ValueError("conflict recommendation disagrees with exact matches")
        return self


class WaiverReference(PolicyValue):
    waiver_id: UUID
    artifact_id: UUID
    artifact_version: int = Field(ge=1)
    content_sha256: Sha256
    tenant_id: UUID
    matter_id: UUID
    valid_from: datetime
    valid_to: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.valid_from)
        require_utc(self.valid_to)
        if self.valid_to <= self.valid_from:
            raise ValueError("waiver interval must be nonempty")
        return self

    def is_effective(self, at: datetime) -> bool:
        checked = require_utc(at)
        return self.valid_from <= checked < self.valid_to


class ConflictDecision(PolicyValue):
    decision_id: UUID
    tenant_id: UUID
    matter_id: UUID
    conflict_check_id: UUID
    disposition: ConflictDisposition
    waiver_reference: WaiverReference | None = None
    decided_by_principal_id: UUID
    decided_at: datetime

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_utc(self.decided_at)
        if (self.disposition == ConflictDisposition.WAIVED) != (
            self.waiver_reference is not None
        ):
            raise ValueError("only a waived conflict decision carries waiver evidence")
        if self.waiver_reference is not None and (
            self.waiver_reference.tenant_id != self.tenant_id
            or self.waiver_reference.matter_id != self.matter_id
        ):
            raise ValueError("waiver scope must equal conflict decision scope")
        return self


class ConflictHold(PolicyValue):
    hold_id: UUID
    tenant_id: UUID
    matter_id: UUID
    conflict_decision_id: UUID
    reason_code: ShortCode
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("conflict hold interval must be nonempty")
        return self

    def is_active(self, at: datetime) -> bool:
        checked = require_utc(at)
        return self.effective_from <= checked and (
            self.effective_to is None or checked < self.effective_to
        )


class EthicalWall(PolicyValue):
    wall_id: UUID
    tenant_id: UUID
    matter_id: UUID
    name: ShortCode
    active: bool
    membership_complete: bool
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("ethical wall interval must be nonempty")
        return self

    def is_active(self, at: datetime) -> bool:
        checked = require_utc(at)
        return (
            self.active
            and self.effective_from <= checked
            and (self.effective_to is None or checked < self.effective_to)
        )


class WallMembership(PolicyValue):
    wall_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    disposition: WallMembershipDisposition
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("wall membership interval must be nonempty")
        return self

    def is_active(self, at: datetime) -> bool:
        checked = require_utc(at)
        return self.effective_from <= checked and (
            self.effective_to is None or checked < self.effective_to
        )


class ProtectedAccessGrant(PolicyValue):
    grant_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    access_level: ProtectedAccessLevel
    purpose: Purpose
    granted_by_principal_id: UUID
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("protected access interval must be nonempty")
        return self

    def is_active(self, at: datetime) -> bool:
        checked = require_utc(at)
        return self.effective_from <= checked and (
            self.effective_to is None or checked < self.effective_to
        )


class ClassificationSource(PolicyValue):
    source_kind: ShortCode
    source_id: UUID
    classification: DataClassification


class PrivilegeLabel(PolicyValue):
    label_id: UUID
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    label: Literal[
        "attorney_client",
        "common_interest",
        "joint_defense",
        "other_privilege_review",
    ]
    active: bool
    labeled_by_principal_id: UUID
    labeled_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.labeled_at)
        return self


class WorkProductLabel(PolicyValue):
    label_id: UUID
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    label: Literal[
        "attorney_work_product", "opinion_work_product", "other_work_product"
    ]
    active: bool
    labeled_by_principal_id: UUID
    labeled_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.labeled_at)
        return self


class RetentionPolicy(PolicyValue):
    retention_policy_id: UUID
    tenant_id: UUID
    matter_id: UUID | None = None
    retain_for_days: int = Field(ge=1, le=36500)
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        require_utc(self.effective_from)
        if self.effective_to is not None:
            require_utc(self.effective_to)
            if self.effective_to <= self.effective_from:
                raise ValueError("retention policy interval must be nonempty")
        return self

    def is_active(self, at: datetime) -> bool:
        checked = require_utc(at)
        return self.effective_from <= checked and (
            self.effective_to is None or checked < self.effective_to
        )


class LegalHold(PolicyValue):
    legal_hold_id: UUID
    tenant_id: UUID
    matter_id: UUID
    status: LegalHoldStatus
    scope: Literal["matter", "material"]
    material_id: UUID | None = None
    issued_by_principal_id: UUID
    effective_from: datetime
    supersedes_hold_id: UUID | None = None
    released_by_principal_id: UUID | None = None
    released_at: datetime | None = None

    @model_validator(mode="after")
    def validate_hold(self) -> Self:
        require_utc(self.effective_from)
        if (self.scope == "material") != (self.material_id is not None):
            raise ValueError("material hold scope requires one material identifier")
        released = self.status == LegalHoldStatus.RELEASED
        complete_release = (
            self.supersedes_hold_id is not None
            and self.released_by_principal_id is not None
            and self.released_at is not None
        )
        if released != complete_release:
            raise ValueError("released hold requires attributable release evidence")
        if not released and self.supersedes_hold_id is not None:
            raise ValueError("active hold cannot supersede another hold")
        if self.released_at is not None:
            require_utc(self.released_at)
            if self.released_at < self.effective_from:
                raise ValueError("hold release cannot precede its effective time")
        return self

    def is_active_for(self, material_id: UUID, at: datetime) -> bool:
        checked = require_utc(at)
        return (
            self.status == LegalHoldStatus.ACTIVE
            and self.effective_from <= checked
            and (self.scope == "matter" or self.material_id == material_id)
        )


class MaterialPolicyFacts(PolicyValue):
    policy_revision: Sha256
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    material_version: int = Field(ge=1)
    tenant_membership: AccessState
    matter_membership: AccessState
    conflict_state_complete: bool
    wall_state_complete: bool
    classification_state_complete: bool
    conflict_decision: ConflictDecision | None = None
    conflict_holds: tuple[ConflictHold, ...] = ()
    walls: tuple[EthicalWall, ...] = ()
    wall_memberships: tuple[WallMembership, ...] = ()
    protected_access_grants: tuple[ProtectedAccessGrant, ...] = ()
    classification_sources: tuple[ClassificationSource, ...] = ()
    privilege_labels: tuple[PrivilegeLabel, ...] = ()
    work_product_labels: tuple[WorkProductLabel, ...] = ()
    retention_policy: RetentionPolicy | None = None
    legal_holds: tuple[LegalHold, ...] = ()
    legal_hold_state_complete: bool
    ownership_resolved: bool
    pending_export: bool
    preservation_required: bool
    profile_owner_principal_id: UUID | None = None

    @model_validator(mode="after")
    def validate_nested_scope(self) -> Self:
        scoped = (
            *((item.tenant_id, item.matter_id) for item in self.conflict_holds),
            *((item.tenant_id, item.matter_id) for item in self.walls),
            *((item.tenant_id, item.matter_id) for item in self.wall_memberships),
            *(
                (item.tenant_id, item.matter_id)
                for item in self.protected_access_grants
            ),
            *((item.tenant_id, item.matter_id) for item in self.privilege_labels),
            *((item.tenant_id, item.matter_id) for item in self.work_product_labels),
            *((item.tenant_id, item.matter_id) for item in self.legal_holds),
        )
        if any(scope != (self.tenant_id, self.matter_id) for scope in scoped):
            raise ValueError("nested policy record crosses tenant or matter scope")
        if self.conflict_decision is not None and (
            self.conflict_decision.tenant_id != self.tenant_id
            or self.conflict_decision.matter_id != self.matter_id
        ):
            raise ValueError("conflict decision crosses tenant or matter scope")
        if any(
            label.material_id != self.material_id for label in self.privilege_labels
        ) or any(
            label.material_id != self.material_id for label in self.work_product_labels
        ):
            raise ValueError("protection label crosses material scope")
        if self.retention_policy is not None and (
            self.retention_policy.tenant_id != self.tenant_id
            or self.retention_policy.matter_id not in {None, self.matter_id}
        ):
            raise ValueError("retention policy crosses tenant or matter scope")
        hold_ids = tuple(item.legal_hold_id for item in self.legal_holds)
        if len(hold_ids) != len(set(hold_ids)):
            raise ValueError("legal hold identifiers must be unique")
        holds_by_id = {item.legal_hold_id: item for item in self.legal_holds}
        released_targets: set[UUID] = set()
        for release in self.legal_holds:
            if release.status != LegalHoldStatus.RELEASED:
                continue
            target_id = release.supersedes_hold_id
            if target_id is None:
                raise ValueError("released legal hold must name one exact active hold")
            target = holds_by_id.get(target_id)
            if (
                target is None
                or target.status != LegalHoldStatus.ACTIVE
                or target.tenant_id != release.tenant_id
                or target.matter_id != release.matter_id
                or target.scope != release.scope
                or target.material_id != release.material_id
                or target.issued_by_principal_id != release.issued_by_principal_id
                or target.effective_from != release.effective_from
                or release.released_at is None
                or release.released_at < target.effective_from
            ):
                raise ValueError("legal hold release must match one exact active hold")
            if target_id in released_targets:
                raise ValueError("an active legal hold can be released only once")
            released_targets.add(target_id)
        return self


class PolicyAccessRequest(PolicyValue):
    boundary: DataFlowBoundary
    principal_id: UUID
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    material_version: int = Field(ge=1)
    material_sha256: Sha256
    purpose: Purpose
    model_route: ModelRoute | None = None
    workflow_run_id: ShortCode | None = None
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        require_utc(self.evaluated_at)
        if (self.boundary == DataFlowBoundary.MODEL_CONTEXT) != (
            self.model_route is not None
        ):
            raise ValueError("only model context carries an exact model route")
        return self


_MATERIAL_READ_CAPABILITIES = frozenset(
    {
        Capability.EVIDENCE_READ,
        Capability.CORPUS_ARTIFACT_READ,
    }
)


class PolicyBoundaryRequirement(PolicyValue):
    """Fixed trusted grant contract for one protected material data flow."""

    boundary: DataFlowBoundary
    audience: Audience
    target: ShortCode
    capability: Capability
    purpose: Purpose
    model_route: ModelRoute | None = None
    workflow_run_id: ShortCode | None = None

    @model_validator(mode="after")
    def validate_boundary_contract(self) -> Self:
        rule = CAPABILITY_RULES[self.capability]
        if self.purpose not in rule.purposes:
            raise ValueError("policy purpose does not match capability")
        expected_prefix = CapabilityGrant.TARGET_PREFIX[self.audience]
        if not self.target.startswith(expected_prefix):
            raise ValueError("policy target does not match audience")
        if self.boundary in {DataFlowBoundary.RETRIEVAL, DataFlowBoundary.CACHE}:
            if (
                self.audience not in {Audience.API, Audience.TOOL}
                or self.capability not in _MATERIAL_READ_CAPABILITIES
            ):
                raise ValueError("retrieval and cache require a material read grant")
        elif self.boundary == DataFlowBoundary.MODEL_CONTEXT:
            if (
                self.audience != Audience.MODEL
                or self.capability not in _MATERIAL_READ_CAPABILITIES
            ):
                raise ValueError("model context requires a model material read grant")
        elif self.boundary == DataFlowBoundary.EXPORT:
            if (
                self.audience != Audience.API
                or self.capability not in _MATERIAL_READ_CAPABILITIES
            ):
                raise ValueError("export requires an API material read grant")
        elif self.audience != Audience.API or self.capability != Capability.AUDIT_READ:
            raise ValueError("audit detail requires the API audit-read grant")
        if (self.audience == Audience.MODEL) != (self.model_route is not None):
            raise ValueError("only a model requirement carries a model route")
        if self.audience in {Audience.MODEL, Audience.TOOL}:
            if self.workflow_run_id is None:
                raise ValueError("model and tool requirements need a workflow run")
        elif self.workflow_run_id is not None:
            raise ValueError("API policy requirements cannot carry a workflow run")
        return self

    def bind(self, request: PolicyAccessRequest) -> CapabilityGrant:
        if (
            request.boundary != self.boundary
            or request.purpose != self.purpose
            or request.model_route != self.model_route
            or request.workflow_run_id != self.workflow_run_id
        ):
            raise ValueError("policy request does not match its fixed boundary")
        rule = CAPABILITY_RULES[self.capability]
        return CapabilityGrant(
            audience=self.audience,
            target=self.target,
            capability=self.capability,
            tenant_id=request.tenant_id,
            matter_id=(
                None if self.capability == Capability.AUDIT_READ else request.matter_id
            ),
            resource_type=rule.resource_type,
            resource_id=str(request.material_id),
            resource_version=request.material_version,
            resource_sha256=request.material_sha256,
            operation=rule.operation,
            purpose=self.purpose,
            model_route=self.model_route,
            workflow_run_id=self.workflow_run_id,
        )


class RetentionRequest(PolicyValue):
    principal_id: UUID
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    material_version: int = Field(ge=1)
    material_sha256: Sha256
    material_created_at: datetime
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        require_utc(self.material_created_at)
        require_utc(self.evaluated_at)
        if self.evaluated_at < self.material_created_at:
            raise ValueError("retention evaluation cannot precede material creation")
        return self


class RetentionBoundaryRequirement(PolicyValue):
    """Fixed least-privilege management grant for eligibility evaluation."""

    target: ShortCode
    audience: Literal[Audience.API] = Audience.API
    capability: Literal[Capability.MATTER_MANAGE] = Capability.MATTER_MANAGE
    purpose: Literal[Purpose.MATTER_MANAGEMENT] = Purpose.MATTER_MANAGEMENT

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if not self.target.startswith("api:"):
            raise ValueError("retention target must be an API target")
        return self

    def bind(self, request: RetentionRequest) -> CapabilityGrant:
        rule = CAPABILITY_RULES[self.capability]
        return CapabilityGrant(
            audience=self.audience,
            target=self.target,
            capability=self.capability,
            tenant_id=request.tenant_id,
            matter_id=request.matter_id,
            resource_type=rule.resource_type,
            resource_id=str(request.matter_id),
            operation=rule.operation,
            purpose=self.purpose,
        )


class PolicyDecision(PolicyValue):
    decision_id: UUID = Field(default_factory=uuid4)
    capauth_decision_id: UUID
    correlation_id: UUID
    principal_id: UUID
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    material_version: int = Field(ge=1)
    boundary: DataFlowBoundary
    allow: bool
    reason: PolicyReason
    effective_classification: DataClassification | None = None
    policy_revision: Sha256 | None = None
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_utc(self.evaluated_at)
        if self.allow != (self.reason == PolicyReason.ALLOW):
            raise ValueError("policy decision allow flag and reason disagree")
        return self


class RetentionDecision(PolicyValue):
    decision_id: UUID = Field(default_factory=uuid4)
    capauth_decision_id: UUID | None = None
    correlation_id: UUID | None = None
    principal_id: UUID
    tenant_id: UUID
    matter_id: UUID
    material_id: UUID
    material_version: int = Field(ge=1)
    allow: bool
    reason: PolicyReason
    eligible_only: Literal[True] = True
    policy_revision: Sha256 | None = None
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_utc(self.evaluated_at)
        if self.allow != (self.reason == PolicyReason.ALLOW):
            raise ValueError("retention decision allow flag and reason disagree")
        return self


class PolicyAuthorizedContext(PolicyValue):
    decision: PolicyDecision
    classification: DataClassification
    cache_partition_key: Sha256
