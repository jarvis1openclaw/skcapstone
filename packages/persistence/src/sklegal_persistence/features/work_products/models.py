"""Immutable records for the Work Product horizontal feature lane."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=4096)]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]


class FrozenRecord(BaseModel):
    """Strict frozen JSON record with the API's camel-case wire shape."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


def canonical_sha256(value: BaseModel | dict[str, Any]) -> str:
    """Return a deterministic digest for an evidence record."""

    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_sha256(content: str) -> str:
    """Hash exact UTF-8 Work Product content."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class WorkProductStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    VALIDATED = "validated"
    APPROVED = "approved"
    WITHDRAWN = "withdrawn"


class VersionStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class ValidationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class VersionBinding(FrozenRecord):
    work_product_version_id: UUID
    version_number: int = Field(ge=1)
    content_sha256: Sha256


class SourceBinding(FrozenRecord):
    source_artifact_id: UUID
    source_artifact_version: int = Field(ge=1)
    source_content_sha256: Sha256
    source_locator: ShortText


class AuthorizationEvidence(FrozenRecord):
    tenant_id: UUID
    matter_id: UUID
    decision_id: UUID
    principal_id: UUID
    capability: Literal["work_product.draft", "work_product.approve", "matter.read"]
    purpose: Literal["work_product_preparation", "human_approval", "matter_management"]
    policy_revision: ShortText
    revocation_revision: Sha256
    credential_digest: Sha256
    ancestor_credential_digests: tuple[Sha256, ...] = ()
    authorized_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> AuthorizationEvidence:
        if self.expires_at <= self.authorized_at:
            raise ValueError("authorization expiry must follow authorization time")
        expected_purpose = {
            "work_product.draft": "work_product_preparation",
            "work_product.approve": "human_approval",
            "matter.read": "matter_management",
        }[self.capability]
        if self.purpose != expected_purpose:
            raise ValueError("capability and purpose do not match")
        digests = (self.credential_digest, *self.ancestor_credential_digests)
        if len(digests) != len(set(digests)):
            raise ValueError("authorization credential chain contains duplicates")
        return self


class WorkProductVersionRecord(FrozenRecord):
    version_id: UUID
    version_number: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=1_000_000)
    content_sha256: Sha256
    source: SourceBinding
    created_by_principal_id: UUID
    created_at: datetime
    status: VersionStatus = VersionStatus.ACTIVE
    superseded_at: datetime | None = None

    @model_validator(mode="after")
    def validate_version(self) -> WorkProductVersionRecord:
        if content_sha256(self.content) != self.content_sha256:
            raise ValueError("Work Product content hash does not match exact content")
        if (self.status is VersionStatus.SUPERSEDED) != (
            self.superseded_at is not None
        ):
            raise ValueError("superseded version requires its transition time")
        return self

    @property
    def binding(self) -> VersionBinding:
        return VersionBinding(
            work_product_version_id=self.version_id,
            version_number=self.version_number,
            content_sha256=self.content_sha256,
        )


class SentenceGroundingRecord(FrozenRecord):
    grounding_id: UUID
    binding: VersionBinding
    sentence_key: Sha256
    claim_id: UUID
    source_span_start: int = Field(ge=0)
    source_span_end: int = Field(gt=0)
    grounded_by_principal_id: UUID
    grounded_at: datetime

    @model_validator(mode="after")
    def validate_span(self) -> SentenceGroundingRecord:
        if self.source_span_end <= self.source_span_start:
            raise ValueError("source span end must follow its start")
        return self


class ValidationRecord(FrozenRecord):
    validation_id: UUID
    binding: VersionBinding
    outcome: ValidationOutcome
    check_ids: tuple[ShortText, ...] = Field(min_length=1)
    missing_sentence_keys: tuple[Sha256, ...] = ()
    validator_principal_id: UUID
    validated_at: datetime
    rationale: NonEmpty
    policy_revision: ShortText
    authorization: AuthorizationEvidence

    @model_validator(mode="after")
    def validate_result(self) -> ValidationRecord:
        if len(self.check_ids) != len(set(self.check_ids)):
            raise ValueError("validation check identifiers must be unique")
        if self.outcome is ValidationOutcome.PASSED and self.missing_sentence_keys:
            raise ValueError(
                "passing validation cannot have missing sentence groundings"
            )
        if self.validator_principal_id != self.authorization.principal_id:
            raise ValueError("validator must match authorization principal")
        if (
            self.authorization.capability != "work_product.draft"
            or self.authorization.purpose != "work_product_preparation"
            or self.policy_revision != self.authorization.policy_revision
            or not (
                self.authorization.authorized_at
                <= self.validated_at
                < self.authorization.expires_at
            )
        ):
            raise ValueError("validation authorization evidence is not exact")
        return self


class ApprovalRecord(FrozenRecord):
    approval_id: UUID
    binding: VersionBinding
    validation_id: UUID
    status: ApprovalStatus
    requested_by_principal_id: UUID
    requested_at: datetime
    request_policy_revision: ShortText
    request_authorization: AuthorizationEvidence
    reviewer_principal_id: UUID | None = None
    decided_at: datetime | None = None
    rationale: NonEmpty | None = None
    decision_policy_revision: ShortText | None = None
    decision_authorization: AuthorizationEvidence | None = None
    revoker_principal_id: UUID | None = None
    revoked_at: datetime | None = None
    revocation_rationale: NonEmpty | None = None
    revocation_authorization: AuthorizationEvidence | None = None
    superseded_at: datetime | None = None
    superseded_by_approval_id: UUID | None = None
    superseding_version_id: UUID | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> ApprovalRecord:
        decided = (
            self.reviewer_principal_id,
            self.decided_at,
            self.rationale,
            self.decision_policy_revision,
            self.decision_authorization,
        )
        revoked = (
            self.revoker_principal_id,
            self.revoked_at,
            self.revocation_rationale,
            self.revocation_authorization,
        )
        superseded = (
            self.superseded_at,
            self.superseded_by_approval_id,
            self.superseding_version_id,
        )
        if self.status is ApprovalStatus.PENDING:
            if any(value is not None for value in (*decided, *revoked, *superseded)):
                raise ValueError("pending Approval cannot carry lifecycle evidence")
        elif self.status is ApprovalStatus.SUPERSEDED:
            if any(value is not None for value in decided) and any(
                value is None for value in decided
            ):
                raise ValueError(
                    "superseded Approval decision evidence must be complete"
                )
        elif any(value is None for value in decided):
            raise ValueError("decided Approval requires exact reviewer evidence")
        if self.status is ApprovalStatus.REVOKED:
            if any(value is None for value in revoked):
                raise ValueError("revoked Approval requires revocation evidence")
        elif any(value is not None for value in revoked):
            raise ValueError("only revoked Approval can carry revocation evidence")
        if self.status is ApprovalStatus.SUPERSEDED:
            if self.superseded_at is None or (
                self.superseded_by_approval_id is None
                and self.superseding_version_id is None
            ):
                raise ValueError("superseded Approval requires its replacement")
        elif any(value is not None for value in superseded):
            raise ValueError("only superseded Approval can carry supersession evidence")
        if (
            self.requested_by_principal_id != self.request_authorization.principal_id
            or self.request_authorization.capability != "work_product.draft"
            or self.request_authorization.purpose != "work_product_preparation"
            or self.request_policy_revision
            != self.request_authorization.policy_revision
            or not (
                self.request_authorization.authorized_at
                <= self.requested_at
                < self.request_authorization.expires_at
            )
        ):
            raise ValueError("Approval request authorization evidence is not exact")
        if self.decision_authorization is not None and (
            self.reviewer_principal_id != self.decision_authorization.principal_id
            or self.decision_authorization.capability != "work_product.approve"
            or self.decision_authorization.purpose != "human_approval"
            or self.decision_policy_revision
            != self.decision_authorization.policy_revision
            or self.decided_at is None
            or not (
                self.decision_authorization.authorized_at
                <= self.decided_at
                < self.decision_authorization.expires_at
            )
        ):
            raise ValueError("Approval decision authorization evidence is not exact")
        if self.revocation_authorization is not None and (
            self.revoker_principal_id != self.revocation_authorization.principal_id
            or self.revocation_authorization.capability != "work_product.approve"
            or self.revocation_authorization.purpose != "human_approval"
            or self.revoked_at is None
            or not (
                self.revocation_authorization.authorized_at
                <= self.revoked_at
                < self.revocation_authorization.expires_at
            )
        ):
            raise ValueError("Approval revocation authorization evidence is not exact")
        if self.decided_at is not None and self.decided_at < self.requested_at:
            raise ValueError("Approval decision cannot precede its request")
        if self.revoked_at is not None and (
            self.decided_at is None or self.revoked_at < self.decided_at
        ):
            raise ValueError("Approval revocation cannot precede its decision")
        if self.superseded_at is not None and (
            self.superseded_at < (self.decided_at or self.requested_at)
        ):
            raise ValueError(
                "Approval supersession cannot precede prior lifecycle evidence"
            )
        return self


class WorkProductAggregate(FrozenRecord):
    schema_version: Literal["sklegal.work-product-aggregate/v1"] = (
        "sklegal.work-product-aggregate/v1"
    )
    tenant_id: UUID
    matter_id: UUID
    work_product_id: UUID
    aggregate_version: int = Field(ge=1)
    title: ShortText
    work_product_kind: Literal[
        "memo", "letter", "pleading", "contract", "packet", "report", "other"
    ]
    status: WorkProductStatus
    current_version_id: UUID
    versions: tuple[WorkProductVersionRecord, ...] = Field(min_length=1)
    groundings: tuple[SentenceGroundingRecord, ...] = ()
    validations: tuple[ValidationRecord, ...] = ()
    approvals: tuple[ApprovalRecord, ...] = ()
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_aggregate(self) -> WorkProductAggregate:
        if len({item.version_id for item in self.versions}) != len(self.versions):
            raise ValueError("Work Product version identifiers must be unique")
        if len({item.version_number for item in self.versions}) != len(self.versions):
            raise ValueError("Work Product version numbers must be unique")
        current = [
            item for item in self.versions if item.version_id == self.current_version_id
        ]
        if len(current) != 1 or current[0].status is not VersionStatus.ACTIVE:
            raise ValueError(
                "current Work Product version must be exactly one active version"
            )
        if sum(item.status is VersionStatus.ACTIVE for item in self.versions) != 1:
            raise ValueError("Work Product must have exactly one active version")
        if [item.version_number for item in self.versions] != list(
            range(1, len(self.versions) + 1)
        ):
            raise ValueError("Work Product version numbers must be contiguous")
        if len({item.grounding_id for item in self.groundings}) != len(self.groundings):
            raise ValueError("grounding identifiers must be unique")
        if len({item.validation_id for item in self.validations}) != len(
            self.validations
        ):
            raise ValueError("validation identifiers must be unique")
        if len({item.approval_id for item in self.approvals}) != len(self.approvals):
            raise ValueError("Approval identifiers must be unique")
        bindings = {item.binding for item in self.versions}
        validation_by_id = {item.validation_id: item for item in self.validations}
        approval_ids = {item.approval_id for item in self.approvals}
        version_ids = {item.version_id for item in self.versions}
        if any(item.binding not in bindings for item in self.groundings):
            raise ValueError("grounding must bind an exact Work Product version")
        if any(item.binding not in bindings for item in self.validations):
            raise ValueError("validation must bind an exact Work Product version")
        for approval in self.approvals:
            validation = validation_by_id.get(approval.validation_id)
            if (
                approval.binding not in bindings
                or validation is None
                or validation.binding != approval.binding
                or validation.outcome is not ValidationOutcome.PASSED
            ):
                raise ValueError("Approval must bind an exact passing validation")
            if (
                approval.superseded_by_approval_id is not None
                and approval.superseded_by_approval_id not in approval_ids
            ):
                raise ValueError("superseding Approval must exist in the aggregate")
            if (
                approval.superseding_version_id is not None
                and approval.superseding_version_id not in version_ids
            ):
                raise ValueError("superseding Work Product version must exist")
        if self.status is WorkProductStatus.APPROVED and not any(
            item.status is ApprovalStatus.APPROVED
            and item.binding == self.current_version.binding
            for item in self.approvals
        ):
            raise ValueError("approved Work Product requires exact current Approval")
        if self.updated_at < self.created_at:
            raise ValueError("aggregate update cannot precede creation")
        return self

    @property
    def current_version(self) -> WorkProductVersionRecord:
        return next(
            item for item in self.versions if item.version_id == self.current_version_id
        )


class WorkProductAuditEvent(FrozenRecord):
    event_id: UUID
    tenant_id: UUID
    matter_id: UUID
    work_product_id: UUID
    aggregate_version: int = Field(ge=1)
    correlation_id: UUID
    actor_principal_id: UUID
    action: ShortText
    outcome: Literal["completed", "denied"]
    subject_sha256: Sha256
    occurred_at: datetime


class WorkProductOutboxEvent(FrozenRecord):
    outbox_id: UUID
    event_id: UUID
    tenant_id: UUID
    matter_id: UUID
    work_product_id: UUID
    aggregate_version: int = Field(ge=1)
    topic: Literal["sklegal.work_product.changed"] = "sklegal.work_product.changed"
    payload_sha256: Sha256
    available_at: datetime


class WorkProductComparison(FrozenRecord):
    work_product_id: UUID
    left: VersionBinding
    right: VersionBinding
    unified_diff: tuple[str, ...]
    changed: bool


class ApprovalValidity(FrozenRecord):
    approval_id: UUID
    binding: VersionBinding
    valid: bool
    reason_code: ShortText
