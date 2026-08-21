"""Typed SKLegal entities and legal-domain state machines."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import Field, field_validator, model_validator

from .base import (
    MatterEntity,
    MatterStatefulEntity,
    ProtectedStatefulEntity,
)
from .exceptions import DomainTransitionError
from .states import (
    ApprovalStatus,
    AuthorityStatus,
    ClaimStatus,
    ClientStatus,
    CommunicationStatus,
    DeadlineStatus,
    ElementStatus,
    EngagementStatus,
    EvidenceStatus,
    ExecutionStatus,
    ExecutionStep,
    FactReviewStatus,
    IssueStatus,
    LegacyRecordKind,
    MatterEventStatus,
    MatterStatus,
    PartyRoleStatus,
    ProceedingStatus,
    RecordCompleteness,
    RemedyStatus,
    TaskStatus,
    TenantStatus,
    TensionStatus,
    TransactionStatus,
    ValidationOutcome,
    VerificationStatus,
    WorkProductStatus,
    WorkProductVersionStatus,
)
from .value_objects import (
    ArtifactBinding,
    DomainId,
    EffectiveInterval,
    LegacyAlias,
    NonEmptyText,
    Sha256,
    ShortText,
    SourceReference,
    TypedValue,
    UtcDateTime,
    ValidationSubject,
    require_utc,
    unique_ids,
)


class Tenant(ProtectedStatefulEntity):
    name: ShortText
    status: TenantStatus = TenantStatus.PROPOSED

    TRANSITIONS: ClassVar = {
        TenantStatus.PROPOSED: frozenset({TenantStatus.ACTIVE, TenantStatus.CLOSED}),
        TenantStatus.ACTIVE: frozenset({TenantStatus.SUSPENDED, TenantStatus.CLOSED}),
        TenantStatus.SUSPENDED: frozenset({TenantStatus.ACTIVE, TenantStatus.CLOSED}),
        TenantStatus.CLOSED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_boundary_identity(self) -> Tenant:
        if self.id != self.tenant_id:
            raise ValueError("Tenant id and tenant_id must match")
        return self


class Client(ProtectedStatefulEntity):
    display_name: ShortText
    client_kind: Literal["person", "family", "trust", "estate", "company", "other"]
    status: ClientStatus = ClientStatus.PROPOSED

    TRANSITIONS: ClassVar = {
        ClientStatus.PROPOSED: frozenset({ClientStatus.ACTIVE, ClientStatus.CLOSED}),
        ClientStatus.ACTIVE: frozenset({ClientStatus.INACTIVE, ClientStatus.CLOSED}),
        ClientStatus.INACTIVE: frozenset({ClientStatus.ACTIVE, ClientStatus.CLOSED}),
        ClientStatus.CLOSED: frozenset(),
    }


class Engagement(ProtectedStatefulEntity):
    client_id: DomainId
    title: ShortText
    scope: NonEmptyText
    effective_interval: EffectiveInterval = Field(default_factory=EffectiveInterval)
    status: EngagementStatus = EngagementStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        ProtectedStatefulEntity.IMMUTABLE_FIELDS | {"client_id"}
    )

    TRANSITIONS: ClassVar = {
        EngagementStatus.PROPOSED: frozenset(
            {EngagementStatus.ACTIVE, EngagementStatus.CLOSED}
        ),
        EngagementStatus.ACTIVE: frozenset(
            {EngagementStatus.SUSPENDED, EngagementStatus.CLOSED}
        ),
        EngagementStatus.SUSPENDED: frozenset(
            {EngagementStatus.ACTIVE, EngagementStatus.CLOSED}
        ),
        EngagementStatus.CLOSED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_active_effective_time(self) -> Engagement:
        if self.status == EngagementStatus.ACTIVE:
            if self.effective_interval.valid_from is None:
                raise ValueError("active engagement requires a known effective start")
        return self


class Matter(ProtectedStatefulEntity):
    matter_id: DomainId
    client_id: DomainId
    engagement_id: DomainId
    title: ShortText
    summary: NonEmptyText
    completeness: RecordCompleteness = RecordCompleteness.INCOMPLETE
    aliases: tuple[LegacyAlias, ...] = ()
    opened_at: UtcDateTime | None = None
    closed_at: UtcDateTime | None = None
    status: MatterStatus = MatterStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        ProtectedStatefulEntity.IMMUTABLE_FIELDS
        | {"matter_id", "client_id", "engagement_id", "aliases"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        ProtectedStatefulEntity.TRANSITION_ONLY_FIELDS | {"opened_at", "closed_at"}
    )

    TRANSITIONS: ClassVar = {
        MatterStatus.PROPOSED: frozenset({MatterStatus.OPEN, MatterStatus.CLOSED}),
        MatterStatus.OPEN: frozenset({MatterStatus.ON_HOLD, MatterStatus.CLOSED}),
        MatterStatus.ON_HOLD: frozenset({MatterStatus.OPEN, MatterStatus.CLOSED}),
        MatterStatus.CLOSED: frozenset({MatterStatus.OPEN, MatterStatus.ARCHIVED}),
        MatterStatus.ARCHIVED: frozenset(),
    }

    @field_validator("aliases")
    @classmethod
    def validate_aliases(
        cls, aliases: tuple[LegacyAlias, ...]
    ) -> tuple[LegacyAlias, ...]:
        if any(alias.record_kind != LegacyRecordKind.CONTAINER for alias in aliases):
            raise ValueError("Matter accepts only historical container aliases")
        keys = [
            (alias.source_system, alias.record_kind, alias.legacy_id)
            for alias in aliases
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("Matter aliases must be unique")
        return aliases

    @model_validator(mode="after")
    def validate_matter_state(self) -> Matter:
        if self.id != self.matter_id:
            raise ValueError("Matter id and matter_id must match")
        if self.status in {MatterStatus.OPEN, MatterStatus.ON_HOLD}:
            if self.opened_at is None:
                raise ValueError("open or held matter requires opened_at")
        if self.status == MatterStatus.PROPOSED and self.opened_at is not None:
            raise ValueError("proposed matter cannot carry opened_at")
        if self.status in {MatterStatus.CLOSED, MatterStatus.ARCHIVED}:
            if self.closed_at is None:
                raise ValueError("closed or archived matter requires closed_at")
        elif self.closed_at is not None:
            raise ValueError("only a closed or archived matter can carry closed_at")
        if self.opened_at is not None and self.closed_at is not None:
            if self.closed_at < self.opened_at:
                raise ValueError("closed_at cannot precede opened_at")
        if self.opened_at is not None and self.opened_at > self.updated_at:
            raise ValueError("opened_at cannot be later than updated_at")
        if self.closed_at is not None and self.closed_at > self.updated_at:
            raise ValueError("closed_at cannot be later than updated_at")
        return self


class Party(MatterStatefulEntity):
    display_name: ShortText
    party_kind: Literal["person", "family", "trust", "estate", "company", "other"]
    source_reference: SourceReference
    verification_reference_id: DomainId | None = None
    status: VerificationStatus = VerificationStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"source_reference"}
    )

    TRANSITIONS: ClassVar = {
        VerificationStatus.PROPOSED: frozenset(
            {
                VerificationStatus.VERIFIED,
                VerificationStatus.DISPUTED,
                VerificationStatus.SUPERSEDED,
            }
        ),
        VerificationStatus.DISPUTED: frozenset(
            {VerificationStatus.VERIFIED, VerificationStatus.SUPERSEDED}
        ),
        VerificationStatus.VERIFIED: frozenset(
            {VerificationStatus.DISPUTED, VerificationStatus.SUPERSEDED}
        ),
        VerificationStatus.SUPERSEDED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"verification_reference_id"}
    )

    @model_validator(mode="after")
    def validate_verification(self) -> Party:
        if self.status == VerificationStatus.VERIFIED:
            if self.verification_reference_id is None:
                raise ValueError("verified party requires verification provenance")
        elif self.status == VerificationStatus.PROPOSED:
            if self.verification_reference_id is not None:
                raise ValueError("proposed party cannot carry verification provenance")
        return self


class PartyRole(MatterStatefulEntity):
    party_id: DomainId
    role: Literal[
        "client",
        "counterparty",
        "trustee",
        "beneficiary",
        "witness",
        "counsel",
        "court",
        "agency",
        "other",
    ]
    effective_interval: EffectiveInterval = Field(default_factory=EffectiveInterval)
    source_reference: SourceReference
    verification_reference_id: DomainId | None = None
    status: PartyRoleStatus = PartyRoleStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"party_id", "role", "source_reference"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"verification_reference_id"}
    )

    TRANSITIONS: ClassVar = {
        PartyRoleStatus.PROPOSED: frozenset(
            {PartyRoleStatus.VERIFIED, PartyRoleStatus.INACTIVE}
        ),
        PartyRoleStatus.VERIFIED: frozenset({PartyRoleStatus.INACTIVE}),
        PartyRoleStatus.INACTIVE: frozenset(),
    }

    @model_validator(mode="after")
    def validate_verification(self) -> PartyRole:
        if self.status == PartyRoleStatus.VERIFIED:
            if self.verification_reference_id is None:
                raise ValueError("verified party role requires verification provenance")
        elif self.status == PartyRoleStatus.PROPOSED:
            if self.verification_reference_id is not None:
                raise ValueError(
                    "proposed party role cannot carry verification provenance"
                )
        return self


class Forum(MatterEntity):
    name: ShortText
    jurisdiction: ShortText
    forum_kind: Literal["court", "agency", "arbitration", "mediation", "other"]
    source_reference: SourceReference | None = None

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "source_reference"
    }


class Proceeding(MatterStatefulEntity):
    title: ShortText
    forum_id: DomainId | None = None
    docket_number: ShortText | None = None
    effective_interval: EffectiveInterval = Field(default_factory=EffectiveInterval)
    status: ProceedingStatus = ProceedingStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"forum_id"}
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset({"forum_id"})
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"forum_id"}
    )

    TRANSITIONS: ClassVar = {
        ProceedingStatus.PROPOSED: frozenset(
            {ProceedingStatus.ACTIVE, ProceedingStatus.CLOSED}
        ),
        ProceedingStatus.ACTIVE: frozenset(
            {
                ProceedingStatus.STAYED,
                ProceedingStatus.DISPOSED,
                ProceedingStatus.CLOSED,
            }
        ),
        ProceedingStatus.STAYED: frozenset(
            {
                ProceedingStatus.ACTIVE,
                ProceedingStatus.DISPOSED,
                ProceedingStatus.CLOSED,
            }
        ),
        ProceedingStatus.DISPOSED: frozenset({ProceedingStatus.CLOSED}),
        ProceedingStatus.CLOSED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_active_forum(self) -> Proceeding:
        if (
            self.status
            in {
                ProceedingStatus.ACTIVE,
                ProceedingStatus.STAYED,
                ProceedingStatus.DISPOSED,
            }
            and self.forum_id is None
        ):
            raise ValueError("active proceeding state requires a forum")
        return self


class MatterEvent(MatterStatefulEntity):
    event_type: ShortText
    description: NonEmptyText
    occurred_at: UtcDateTime | None = None
    observed_at: UtcDateTime
    source_reference: SourceReference
    aliases: tuple[LegacyAlias, ...] = ()
    verification_reference_id: DomainId | None = None
    status: MatterEventStatus = MatterEventStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "event_type",
            "description",
            "occurred_at",
            "observed_at",
            "source_reference",
            "aliases",
        }
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset({"occurred_at"})
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {"occurred_at", "verification_reference_id"}
    )

    TRANSITIONS: ClassVar = {
        MatterEventStatus.PROPOSED: frozenset(
            {MatterEventStatus.RECORDED, MatterEventStatus.SUPERSEDED}
        ),
        MatterEventStatus.RECORDED: frozenset(
            {MatterEventStatus.VERIFIED, MatterEventStatus.SUPERSEDED}
        ),
        MatterEventStatus.VERIFIED: frozenset({MatterEventStatus.SUPERSEDED}),
        MatterEventStatus.SUPERSEDED: frozenset(),
    }

    @field_validator("aliases")
    @classmethod
    def validate_aliases(
        cls, aliases: tuple[LegacyAlias, ...]
    ) -> tuple[LegacyAlias, ...]:
        if any(alias.record_kind != LegacyRecordKind.ACTIVITY for alias in aliases):
            raise ValueError("MatterEvent accepts only historical activity aliases")
        keys = [
            (alias.source_system, alias.record_kind, alias.legacy_id)
            for alias in aliases
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("MatterEvent aliases must be unique")
        return aliases

    @model_validator(mode="after")
    def validate_verified_time(self) -> MatterEvent:
        if self.observed_at > self.updated_at:
            raise ValueError("observed_at cannot be later than updated_at")
        if self.occurred_at is not None and self.occurred_at > self.observed_at:
            raise ValueError("occurred_at cannot be later than observed_at")
        if self.status == MatterEventStatus.VERIFIED:
            if self.occurred_at is None:
                raise ValueError("verified matter event requires occurred_at")
            if self.verification_reference_id is None:
                raise ValueError("verified matter event requires review provenance")
        elif (
            self.status
            in {
                MatterEventStatus.PROPOSED,
                MatterEventStatus.RECORDED,
            }
            and self.verification_reference_id is not None
        ):
            raise ValueError("unverified matter event cannot carry review provenance")
        return self


class Transaction(MatterStatefulEntity):
    title: ShortText
    description: NonEmptyText
    party_role_ids: tuple[DomainId, ...]
    effective_at: UtcDateTime | None = None
    source_references: tuple[SourceReference, ...]
    status: TransactionStatus = TransactionStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"source_references"}
    )

    TRANSITIONS: ClassVar = {
        TransactionStatus.PROPOSED: frozenset(
            {TransactionStatus.UNDER_REVIEW, TransactionStatus.SUPERSEDED}
        ),
        TransactionStatus.UNDER_REVIEW: frozenset(
            {
                TransactionStatus.CONFIRMED,
                TransactionStatus.DISPUTED,
                TransactionStatus.SUPERSEDED,
            }
        ),
        TransactionStatus.CONFIRMED: frozenset(
            {TransactionStatus.DISPUTED, TransactionStatus.SUPERSEDED}
        ),
        TransactionStatus.DISPUTED: frozenset(
            {TransactionStatus.CONFIRMED, TransactionStatus.SUPERSEDED}
        ),
        TransactionStatus.SUPERSEDED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"effective_at"}
    )

    @field_validator("party_role_ids")
    @classmethod
    def validate_party_role_ids(
        cls, values: tuple[DomainId, ...]
    ) -> tuple[DomainId, ...]:
        return unique_ids(values, "party_role_ids")

    @model_validator(mode="after")
    def validate_sources_and_time(self) -> Transaction:
        if not self.source_references:
            raise ValueError("transaction requires source provenance")
        if self.status == TransactionStatus.CONFIRMED and self.effective_at is None:
            raise ValueError("confirmed transaction requires effective_at")
        return self


class FactAssertion(MatterStatefulEntity):
    subject_ref: DomainId
    predicate: ShortText
    asserted_value: TypedValue
    source_reference: SourceReference
    source_locator: NonEmptyText
    effective_interval: EffectiveInterval = Field(default_factory=EffectiveInterval)
    observed_at: UtcDateTime
    tension_group_id: DomainId | None = None
    verification_reference_id: DomainId | None = None
    status: FactReviewStatus = FactReviewStatus.SOURCE_ASSERTED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {"subject_ref", "predicate"}
        | {
            "asserted_value",
            "source_reference",
            "source_locator",
            "effective_interval",
            "observed_at",
        }
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {"tension_group_id", "verification_reference_id"}
    )

    TRANSITIONS: ClassVar = {
        FactReviewStatus.SOURCE_ASSERTED: frozenset(
            {
                FactReviewStatus.AMBIGUOUS,
                FactReviewStatus.VERIFIED,
                FactReviewStatus.SUPERSEDED,
            }
        ),
        FactReviewStatus.AMBIGUOUS: frozenset(
            {FactReviewStatus.VERIFIED, FactReviewStatus.SUPERSEDED}
        ),
        FactReviewStatus.VERIFIED: frozenset({FactReviewStatus.SUPERSEDED}),
        FactReviewStatus.SUPERSEDED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_review_state(self) -> FactAssertion:
        if self.observed_at > self.updated_at:
            raise ValueError("observed_at cannot be later than updated_at")
        if self.status == FactReviewStatus.AMBIGUOUS and self.tension_group_id is None:
            raise ValueError("ambiguous fact requires a tension group")
        if self.status == FactReviewStatus.VERIFIED:
            if self.verification_reference_id is None:
                raise ValueError("verified fact requires verification provenance")
        elif (
            self.status
            in {
                FactReviewStatus.SOURCE_ASSERTED,
                FactReviewStatus.AMBIGUOUS,
            }
            and self.verification_reference_id is not None
        ):
            raise ValueError("unverified fact cannot carry verification provenance")
        return self


class TensionGroup(MatterStatefulEntity):
    title: ShortText
    assertion_ids: Annotated[tuple[DomainId, ...], Field(min_length=2)]
    selected_assertion_id: DomainId | None = None
    resolution_rationale: NonEmptyText | None = None
    resolved_by: DomainId | None = None
    resolved_at: UtcDateTime | None = None
    status: TensionStatus = TensionStatus.UNRESOLVED

    TRANSITIONS: ClassVar = {
        TensionStatus.UNRESOLVED: frozenset(
            {TensionStatus.UNDER_REVIEW, TensionStatus.DISMISSED}
        ),
        TensionStatus.UNDER_REVIEW: frozenset(
            {TensionStatus.UNRESOLVED, TensionStatus.RESOLVED, TensionStatus.DISMISSED}
        ),
        TensionStatus.RESOLVED: frozenset({TensionStatus.UNDER_REVIEW}),
        TensionStatus.DISMISSED: frozenset({TensionStatus.UNDER_REVIEW}),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "selected_assertion_id",
            "resolution_rationale",
            "resolved_by",
            "resolved_at",
        }
    )

    @field_validator("assertion_ids")
    @classmethod
    def validate_assertion_ids(
        cls, values: tuple[DomainId, ...]
    ) -> tuple[DomainId, ...]:
        return unique_ids(values, "assertion_ids")

    @model_validator(mode="after")
    def validate_resolution(self) -> TensionGroup:
        resolution_values = (
            self.selected_assertion_id,
            self.resolution_rationale,
            self.resolved_by,
            self.resolved_at,
        )
        if self.status == TensionStatus.RESOLVED:
            if any(value is None for value in resolution_values):
                raise ValueError("resolved tension requires attributable resolution")
            if self.selected_assertion_id not in self.assertion_ids:
                raise ValueError("selected assertion must belong to the tension group")
            if self.resolved_at != self.updated_at:
                raise ValueError(
                    "resolved_at must equal the resolution transition time"
                )
        elif any(value is not None for value in resolution_values):
            raise ValueError("unresolved tension cannot carry resolution fields")
        return self


class EvidenceItem(MatterStatefulEntity):
    title: ShortText
    media_type: ShortText
    content_sha256: Sha256
    source_reference: SourceReference
    verification_reference_id: DomainId | None = None
    status: EvidenceStatus = EvidenceStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"content_sha256", "source_reference"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"verification_reference_id"}
    )

    TRANSITIONS: ClassVar = {
        EvidenceStatus.PROPOSED: frozenset(
            {EvidenceStatus.COLLECTED, EvidenceStatus.SUPERSEDED}
        ),
        EvidenceStatus.COLLECTED: frozenset(
            {
                EvidenceStatus.VERIFIED,
                EvidenceStatus.CHALLENGED,
                EvidenceStatus.EXCLUDED,
                EvidenceStatus.SUPERSEDED,
            }
        ),
        EvidenceStatus.VERIFIED: frozenset(
            {
                EvidenceStatus.CHALLENGED,
                EvidenceStatus.EXCLUDED,
                EvidenceStatus.SUPERSEDED,
            }
        ),
        EvidenceStatus.CHALLENGED: frozenset(
            {
                EvidenceStatus.VERIFIED,
                EvidenceStatus.EXCLUDED,
                EvidenceStatus.SUPERSEDED,
            }
        ),
        EvidenceStatus.EXCLUDED: frozenset({EvidenceStatus.SUPERSEDED}),
        EvidenceStatus.SUPERSEDED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_verification(self) -> EvidenceItem:
        if self.status == EvidenceStatus.VERIFIED:
            if self.verification_reference_id is None:
                raise ValueError("verified evidence requires review provenance")
        elif (
            self.status
            in {
                EvidenceStatus.PROPOSED,
                EvidenceStatus.COLLECTED,
            }
            and self.verification_reference_id is not None
        ):
            raise ValueError("unverified evidence cannot carry review provenance")
        return self


class CustodyEvent(MatterEntity):
    evidence_item_id: DomainId
    action: Literal[
        "acquired", "transferred", "copied", "sealed", "unsealed", "disposed"
    ]
    custodian_id: DomainId
    occurred_at: UtcDateTime
    source_reference: SourceReference

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "evidence_item_id",
        "action",
        "custodian_id",
        "occurred_at",
        "source_reference",
    }

    @model_validator(mode="after")
    def validate_occurrence_time(self) -> CustodyEvent:
        if self.occurred_at > self.updated_at:
            raise ValueError("custody occurrence cannot be later than updated_at")
        return self


class Authority(MatterStatefulEntity):
    title: ShortText
    citation: ShortText
    jurisdiction: ShortText
    authority_kind: Literal[
        "constitution",
        "statute",
        "regulation",
        "case",
        "rule",
        "administrative_material",
        "secondary_source",
        "other",
    ]
    source_reference: SourceReference
    effective_interval: EffectiveInterval = Field(default_factory=EffectiveInterval)
    applicability_validation_id: DomainId | None = None
    status: AuthorityStatus = AuthorityStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"source_reference"}
    )

    TRANSITIONS: ClassVar = {
        AuthorityStatus.PROPOSED: frozenset(
            {
                AuthorityStatus.VERIFIED,
                AuthorityStatus.CHALLENGED,
                AuthorityStatus.NOT_APPLICABLE,
                AuthorityStatus.SUPERSEDED,
            }
        ),
        AuthorityStatus.VERIFIED: frozenset(
            {
                AuthorityStatus.CHALLENGED,
                AuthorityStatus.NOT_APPLICABLE,
                AuthorityStatus.SUPERSEDED,
            }
        ),
        AuthorityStatus.CHALLENGED: frozenset(
            {
                AuthorityStatus.VERIFIED,
                AuthorityStatus.NOT_APPLICABLE,
                AuthorityStatus.SUPERSEDED,
            }
        ),
        AuthorityStatus.NOT_APPLICABLE: frozenset({AuthorityStatus.SUPERSEDED}),
        AuthorityStatus.SUPERSEDED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"applicability_validation_id"}
    )

    @model_validator(mode="after")
    def validate_applicability(self) -> Authority:
        if (
            self.status
            in {
                AuthorityStatus.VERIFIED,
                AuthorityStatus.NOT_APPLICABLE,
            }
            and self.applicability_validation_id is None
        ):
            raise ValueError("authority decision requires applicability validation")
        if self.status == AuthorityStatus.PROPOSED:
            if self.applicability_validation_id is not None:
                raise ValueError("proposed authority cannot carry applicability review")
        return self


class Issue(MatterStatefulEntity):
    question: NonEmptyText
    status: IssueStatus = IssueStatus.IDENTIFIED

    TRANSITIONS: ClassVar = {
        IssueStatus.IDENTIFIED: frozenset(
            {IssueStatus.UNDER_REVIEW, IssueStatus.DEFERRED}
        ),
        IssueStatus.UNDER_REVIEW: frozenset(
            {IssueStatus.RESOLVED, IssueStatus.DEFERRED}
        ),
        IssueStatus.DEFERRED: frozenset({IssueStatus.UNDER_REVIEW}),
        IssueStatus.RESOLVED: frozenset({IssueStatus.UNDER_REVIEW}),
    }


class Claim(MatterStatefulEntity):
    issue_id: DomainId
    label: ShortText
    statement: NonEmptyText
    element_ids: tuple[DomainId, ...] = ()
    evidence_item_ids: tuple[DomainId, ...] = ()
    authority_ids: tuple[DomainId, ...] = ()
    acceptance_validation_id: DomainId | None = None
    status: ClaimStatus = ClaimStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"issue_id"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"acceptance_validation_id"}
    )

    TRANSITIONS: ClassVar = {
        ClaimStatus.PROPOSED: frozenset(
            {ClaimStatus.UNDER_REVIEW, ClaimStatus.WITHDRAWN}
        ),
        ClaimStatus.UNDER_REVIEW: frozenset(
            {
                ClaimStatus.ACCEPTED,
                ClaimStatus.CHALLENGED,
                ClaimStatus.REJECTED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.ACCEPTED: frozenset(
            {ClaimStatus.CHALLENGED, ClaimStatus.WITHDRAWN}
        ),
        ClaimStatus.CHALLENGED: frozenset(
            {ClaimStatus.UNDER_REVIEW, ClaimStatus.REJECTED, ClaimStatus.WITHDRAWN}
        ),
        ClaimStatus.REJECTED: frozenset({ClaimStatus.UNDER_REVIEW}),
        ClaimStatus.WITHDRAWN: frozenset(),
    }

    @field_validator("element_ids", "evidence_item_ids", "authority_ids")
    @classmethod
    def validate_reference_ids(
        cls, values: tuple[DomainId, ...], info: object
    ) -> tuple[DomainId, ...]:
        field_name = getattr(info, "field_name", "reference_ids")
        return unique_ids(values, field_name)

    @model_validator(mode="after")
    def validate_acceptance(self) -> Claim:
        if self.status == ClaimStatus.ACCEPTED:
            if self.acceptance_validation_id is None:
                raise ValueError("accepted claim requires validation provenance")
            if not self.element_ids:
                raise ValueError("accepted claim requires at least one element")
        return self


class Defense(Claim):
    """A claim-shaped defensive theory with the same review controls."""


class Element(MatterStatefulEntity):
    theory_kind: Literal["claim", "defense"] = "claim"
    claim_id: DomainId
    description: NonEmptyText
    evidence_item_ids: tuple[DomainId, ...] = ()
    status: ElementStatus = ElementStatus.ALLEGED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"theory_kind", "claim_id"}
    )

    TRANSITIONS: ClassVar = {
        ElementStatus.ALLEGED: frozenset(
            {
                ElementStatus.SUPPORTED,
                ElementStatus.DISPUTED,
                ElementStatus.NOT_ESTABLISHED,
            }
        ),
        ElementStatus.SUPPORTED: frozenset(
            {ElementStatus.DISPUTED, ElementStatus.NOT_ESTABLISHED}
        ),
        ElementStatus.DISPUTED: frozenset(
            {ElementStatus.SUPPORTED, ElementStatus.NOT_ESTABLISHED}
        ),
        ElementStatus.NOT_ESTABLISHED: frozenset({ElementStatus.DISPUTED}),
    }

    @model_validator(mode="after")
    def validate_support(self) -> Element:
        if self.status == ElementStatus.SUPPORTED and not self.evidence_item_ids:
            raise ValueError("supported element requires evidence")
        unique_ids(self.evidence_item_ids, "evidence_item_ids")
        return self


class Remedy(MatterStatefulEntity):
    claim_id: DomainId
    description: NonEmptyText
    authority_ids: tuple[DomainId, ...] = ()
    status: RemedyStatus = RemedyStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"claim_id"}
    )

    TRANSITIONS: ClassVar = {
        RemedyStatus.PROPOSED: frozenset(
            {RemedyStatus.AVAILABLE, RemedyStatus.UNAVAILABLE}
        ),
        RemedyStatus.AVAILABLE: frozenset(
            {RemedyStatus.AWARDED, RemedyStatus.DENIED, RemedyStatus.UNAVAILABLE}
        ),
        RemedyStatus.UNAVAILABLE: frozenset({RemedyStatus.AVAILABLE}),
        RemedyStatus.AWARDED: frozenset(),
        RemedyStatus.DENIED: frozenset(),
    }


class DeadlineCalculation(MatterEntity):
    trigger_fact_id: DomainId
    calculation_rule: NonEmptyText
    candidate_due_at: UtcDateTime
    calculated_at: UtcDateTime
    source_references: Annotated[tuple[SourceReference, ...], Field(min_length=1)]
    calculation_version: ShortText

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "trigger_fact_id",
        "calculation_rule",
        "candidate_due_at",
        "calculated_at",
        "source_references",
        "calculation_version",
    }

    @model_validator(mode="after")
    def validate_calculated_time(self) -> DeadlineCalculation:
        if self.calculated_at > self.updated_at:
            raise ValueError("calculated_at cannot be later than updated_at")
        return self


class Deadline(MatterStatefulEntity):
    title: ShortText
    candidate_due_at: UtcDateTime | None = None
    operative_due_at: UtcDateTime | None = None
    trigger_fact_id: DomainId | None = None
    calculation_id: DomainId | None = None
    review_validation_id: DomainId | None = None
    completed_at: UtcDateTime | None = None
    status: DeadlineStatus = DeadlineStatus.CANDIDATE

    TRANSITIONS: ClassVar = {
        DeadlineStatus.CANDIDATE: frozenset(
            {DeadlineStatus.REVIEWED, DeadlineStatus.WITHDRAWN}
        ),
        DeadlineStatus.REVIEWED: frozenset(
            {DeadlineStatus.OPERATIVE, DeadlineStatus.WITHDRAWN}
        ),
        DeadlineStatus.OPERATIVE: frozenset(
            {
                DeadlineStatus.SATISFIED,
                DeadlineStatus.MISSED,
                DeadlineStatus.WITHDRAWN,
            }
        ),
        DeadlineStatus.SATISFIED: frozenset(),
        DeadlineStatus.MISSED: frozenset({DeadlineStatus.SATISFIED}),
        DeadlineStatus.WITHDRAWN: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "candidate_due_at",
            "operative_due_at",
            "trigger_fact_id",
            "calculation_id",
            "review_validation_id",
            "completed_at",
        }
    )

    @model_validator(mode="after")
    def validate_deadline_provenance(self) -> Deadline:
        provenance = (
            self.trigger_fact_id,
            self.calculation_id,
            self.review_validation_id,
        )
        if self.status in {
            DeadlineStatus.REVIEWED,
            DeadlineStatus.OPERATIVE,
            DeadlineStatus.SATISFIED,
            DeadlineStatus.MISSED,
        }:
            if any(value is None for value in provenance):
                raise ValueError(
                    "reviewed deadline requires trigger, calculation, and review provenance"
                )
        if (
            self.status
            in {
                DeadlineStatus.OPERATIVE,
                DeadlineStatus.SATISFIED,
                DeadlineStatus.MISSED,
            }
            and self.operative_due_at is None
        ):
            raise ValueError("operative deadline state requires operative_due_at")
        if self.status == DeadlineStatus.SATISFIED and self.completed_at is None:
            raise ValueError("satisfied deadline requires completed_at")
        if self.status == DeadlineStatus.SATISFIED:
            if self.completed_at != self.updated_at:
                raise ValueError(
                    "completed_at must equal the satisfaction transition time"
                )
        if self.status != DeadlineStatus.SATISFIED and self.completed_at is not None:
            raise ValueError("only a satisfied deadline can carry completed_at")
        if self.status in {DeadlineStatus.CANDIDATE, DeadlineStatus.REVIEWED}:
            if self.operative_due_at is not None:
                raise ValueError("non-operative deadline cannot carry operative_due_at")
        if self.status == DeadlineStatus.CANDIDATE:
            if self.review_validation_id is not None:
                raise ValueError("deadline candidate cannot carry completed review")
        return self


class Task(MatterStatefulEntity):
    title: ShortText
    description: NonEmptyText
    assigned_principal_id: DomainId | None = None
    due_at: UtcDateTime | None = None
    blocked_reason: NonEmptyText | None = None
    completed_at: UtcDateTime | None = None
    status: TaskStatus = TaskStatus.DRAFT

    TRANSITIONS: ClassVar = {
        TaskStatus.DRAFT: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
        TaskStatus.READY: frozenset(
            {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
        ),
        TaskStatus.IN_PROGRESS: frozenset(
            {TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        ),
        TaskStatus.BLOCKED: frozenset(
            {TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}
        ),
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS | {"blocked_reason", "completed_at"}
    )

    @model_validator(mode="after")
    def validate_task_state(self) -> Task:
        if self.status == TaskStatus.READY and self.assigned_principal_id is None:
            raise ValueError("ready task requires an assignee")
        if self.status == TaskStatus.BLOCKED and self.blocked_reason is None:
            raise ValueError("blocked task requires blocked_reason")
        if self.status != TaskStatus.BLOCKED and self.blocked_reason is not None:
            raise ValueError("only a blocked task can carry blocked_reason")
        if self.status == TaskStatus.COMPLETED and self.completed_at is None:
            raise ValueError("completed task requires completed_at")
        if self.status == TaskStatus.COMPLETED:
            if self.completed_at != self.updated_at:
                raise ValueError(
                    "completed_at must equal the completion transition time"
                )
        if self.status != TaskStatus.COMPLETED and self.completed_at is not None:
            raise ValueError("only a completed task can carry completed_at")
        return self


class Communication(MatterStatefulEntity):
    direction: Literal["inbound", "outbound", "internal"]
    channel: Literal["email", "mail", "service", "filing", "calendar", "other"]
    subject: ShortText
    participant_ids: tuple[DomainId, ...]
    work_product_version_id: DomainId | None = None
    destination_verified: bool = False
    destination_sha256: Sha256 | None = None
    validation_result_id: DomainId | None = None
    approval_id: DomainId | None = None
    execution_id: DomainId | None = None
    status: CommunicationStatus = CommunicationStatus.DRAFT

    TRANSITIONS: ClassVar = {
        CommunicationStatus.DRAFT: frozenset(
            {CommunicationStatus.VALIDATED, CommunicationStatus.CANCELLED}
        ),
        CommunicationStatus.VALIDATED: frozenset(
            {
                CommunicationStatus.DRAFT,
                CommunicationStatus.APPROVED,
                CommunicationStatus.CANCELLED,
            }
        ),
        CommunicationStatus.APPROVED: frozenset(
            {
                CommunicationStatus.DRAFT,
                CommunicationStatus.QUEUED,
                CommunicationStatus.CANCELLED,
            }
        ),
        CommunicationStatus.QUEUED: frozenset(
            {
                CommunicationStatus.DISPATCHED,
                CommunicationStatus.FAILED,
                CommunicationStatus.CANCELLED,
            }
        ),
        CommunicationStatus.DISPATCHED: frozenset(
            {CommunicationStatus.RECEIPT_VERIFIED, CommunicationStatus.FAILED}
        ),
        CommunicationStatus.RECEIPT_VERIFIED: frozenset(),
        CommunicationStatus.FAILED: frozenset({CommunicationStatus.QUEUED}),
        CommunicationStatus.CANCELLED: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "destination_verified",
            "destination_sha256",
            "validation_result_id",
            "approval_id",
            "execution_id",
        }
    )
    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"destination_sha256", "execution_id"}
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"destination_sha256", "execution_id"}
    )

    _PAYLOAD_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "direction",
            "channel",
            "subject",
            "participant_ids",
            "work_product_version_id",
        }
    )
    _GATE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "validation_result_id",
            "approval_id",
            "destination_verified",
            "destination_sha256",
            "execution_id",
        }
    )

    @model_validator(mode="after")
    def validate_external_gates(self) -> Communication:
        if not self.participant_ids:
            raise ValueError("communication requires at least one participant")
        unique_ids(self.participant_ids, "participant_ids")
        progressed = {
            CommunicationStatus.VALIDATED,
            CommunicationStatus.APPROVED,
            CommunicationStatus.QUEUED,
            CommunicationStatus.DISPATCHED,
            CommunicationStatus.RECEIPT_VERIFIED,
            CommunicationStatus.FAILED,
        }
        if self.status in progressed and self.validation_result_id is None:
            raise ValueError("validated communication state requires validation")
        if self.status in progressed - {CommunicationStatus.VALIDATED}:
            if self.approval_id is None:
                raise ValueError("approved communication state requires approval")
        if self.status in {
            CommunicationStatus.QUEUED,
            CommunicationStatus.DISPATCHED,
            CommunicationStatus.RECEIPT_VERIFIED,
            CommunicationStatus.FAILED,
        }:
            if not self.destination_verified:
                raise ValueError("queued communication requires verified destination")
            if self.destination_sha256 is None:
                raise ValueError(
                    "queued communication requires exact destination binding"
                )
            if self.execution_id is None:
                raise ValueError("queued communication requires execution linkage")
        if self.status == CommunicationStatus.DRAFT:
            if (
                any(
                    value is not None
                    for value in (
                        self.validation_result_id,
                        self.approval_id,
                        self.destination_sha256,
                        self.execution_id,
                    )
                )
                or self.destination_verified
            ):
                raise ValueError(
                    "draft communication cannot carry future gate evidence"
                )
        if self.status == CommunicationStatus.VALIDATED:
            if (
                self.approval_id is not None
                or self.destination_sha256 is not None
                or self.execution_id is not None
            ):
                raise ValueError("validated communication cannot carry later gates")
            if self.destination_verified:
                raise ValueError("validated communication cannot be queued early")
        if self.status == CommunicationStatus.APPROVED:
            if (
                self.destination_sha256 is not None
                or self.execution_id is not None
                or self.destination_verified
            ):
                raise ValueError("approved communication cannot be queued early")
        return self

    def evolve(self, *, at: datetime, **changes: Any) -> Self:
        if self.status != CommunicationStatus.DRAFT:
            attempted = self._PAYLOAD_FIELDS.intersection(changes)
            if attempted:
                names = ", ".join(sorted(attempted))
                raise DomainTransitionError(
                    "reviewed communication payload requires a reset transition: "
                    f"{names}"
                )
        return super().evolve(at=at, **changes)

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        payload_changes = self._PAYLOAD_FIELDS.intersection(changes)
        if payload_changes and target != CommunicationStatus.DRAFT:
            names = ", ".join(sorted(payload_changes))
            raise DomainTransitionError(
                f"communication payload can change only on reset to draft: {names}"
            )
        if target == CommunicationStatus.DRAFT:
            uncleared = {
                name
                for name in self._GATE_FIELDS
                if changes.get(name, getattr(self, name)) not in {None, False}
            }
            if uncleared:
                names = ", ".join(sorted(uncleared))
                raise DomainTransitionError(
                    f"communication reset must clear gate evidence: {names}"
                )
        else:
            replaced = {
                name
                for name in self._GATE_FIELDS
                if name in changes
                and getattr(self, name) not in {None, False}
                and changes[name] != getattr(self, name)
            }
            if replaced:
                names = ", ".join(sorted(replaced))
                raise DomainTransitionError(
                    f"communication gate evidence cannot be replaced: {names}"
                )
        return super().transition_to(target, at=at, **changes)


class WorkProductVersion(MatterStatefulEntity):
    work_product_id: DomainId
    version_number: Annotated[int, Field(ge=1)]
    content_sha256: Sha256
    source_artifact_id: DomainId
    status: WorkProductVersionStatus = WorkProductVersionStatus.DRAFT

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "work_product_id",
            "version_number",
            "content_sha256",
            "source_artifact_id",
        }
    )

    TRANSITIONS: ClassVar = {
        WorkProductVersionStatus.DRAFT: frozenset({WorkProductVersionStatus.FROZEN}),
        WorkProductVersionStatus.FROZEN: frozenset(
            {WorkProductVersionStatus.SUPERSEDED}
        ),
        WorkProductVersionStatus.SUPERSEDED: frozenset(),
    }


class WorkProduct(MatterStatefulEntity):
    title: ShortText
    work_product_kind: Literal[
        "memo", "letter", "pleading", "contract", "packet", "report", "other"
    ]
    current_version_id: DomainId
    validation_result_id: DomainId | None = None
    approval_id: DomainId | None = None
    status: WorkProductStatus = WorkProductStatus.DRAFT

    TRANSITIONS: ClassVar = {
        WorkProductStatus.DRAFT: frozenset(
            {WorkProductStatus.IN_REVIEW, WorkProductStatus.WITHDRAWN}
        ),
        WorkProductStatus.IN_REVIEW: frozenset(
            {WorkProductStatus.VALIDATED, WorkProductStatus.WITHDRAWN}
        ),
        WorkProductStatus.VALIDATED: frozenset(
            {WorkProductStatus.APPROVED, WorkProductStatus.IN_REVIEW}
        ),
        WorkProductStatus.APPROVED: frozenset(
            {
                WorkProductStatus.IN_REVIEW,
                WorkProductStatus.SUPERSEDED,
                WorkProductStatus.WITHDRAWN,
            }
        ),
        WorkProductStatus.SUPERSEDED: frozenset(),
        WorkProductStatus.WITHDRAWN: frozenset(),
    }
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {"validation_result_id", "approval_id"}
    )
    _PAYLOAD_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"title", "work_product_kind", "current_version_id"}
    )
    _GATE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"validation_result_id", "approval_id"}
    )

    @model_validator(mode="after")
    def validate_review_gates(self) -> WorkProduct:
        if self.status in {
            WorkProductStatus.VALIDATED,
            WorkProductStatus.APPROVED,
            WorkProductStatus.SUPERSEDED,
        }:
            if self.validation_result_id is None:
                raise ValueError("validated work product requires validation")
        if (
            self.status
            in {
                WorkProductStatus.APPROVED,
                WorkProductStatus.SUPERSEDED,
            }
            and self.approval_id is None
        ):
            raise ValueError("approved work product state requires approval")
        if self.status in {WorkProductStatus.DRAFT, WorkProductStatus.IN_REVIEW}:
            if self.validation_result_id is not None or self.approval_id is not None:
                raise ValueError("unvalidated work product cannot carry gate evidence")
        if self.status == WorkProductStatus.VALIDATED and self.approval_id is not None:
            raise ValueError("validated work product cannot carry approval early")
        return self

    def evolve(self, *, at: datetime, **changes: Any) -> Self:
        if self.status not in {
            WorkProductStatus.DRAFT,
            WorkProductStatus.IN_REVIEW,
        }:
            attempted = self._PAYLOAD_FIELDS.intersection(changes)
            if attempted:
                names = ", ".join(sorted(attempted))
                raise DomainTransitionError(
                    "reviewed work product payload requires an in-review reset: "
                    f"{names}"
                )
        return super().evolve(at=at, **changes)

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        payload_changes = self._PAYLOAD_FIELDS.intersection(changes)
        if payload_changes and target != WorkProductStatus.IN_REVIEW:
            names = ", ".join(sorted(payload_changes))
            raise DomainTransitionError(
                f"work product payload can change only on reset to review: {names}"
            )
        if target == WorkProductStatus.IN_REVIEW:
            uncleared = {
                name
                for name in self._GATE_FIELDS
                if changes.get(name, getattr(self, name)) is not None
            }
            if uncleared:
                names = ", ".join(sorted(uncleared))
                raise DomainTransitionError(
                    f"work product reset must clear gate evidence: {names}"
                )
        else:
            replaced = {
                name
                for name in self._GATE_FIELDS
                if name in changes
                and getattr(self, name) is not None
                and changes[name] != getattr(self, name)
            }
            if replaced:
                names = ", ".join(sorted(replaced))
                raise DomainTransitionError(
                    f"work product gate evidence cannot be replaced: {names}"
                )
        return super().transition_to(target, at=at, **changes)


class ValidationResult(MatterEntity):
    subject: ArtifactBinding | ValidationSubject
    outcome: ValidationOutcome
    check_ids: Annotated[tuple[ShortText, ...], Field(min_length=1)]
    validator_principal_id: DomainId
    validated_at: UtcDateTime
    rationale: NonEmptyText

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "subject",
        "outcome",
        "check_ids",
        "validator_principal_id",
        "validated_at",
        "rationale",
    }

    @field_validator("check_ids")
    @classmethod
    def validate_check_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("check_ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_result_time(self) -> ValidationResult:
        if not self.created_at <= self.validated_at <= self.updated_at:
            raise ValueError("validated_at must fit the result audit interval")
        return self


class Approval(MatterStatefulEntity):
    subject: ArtifactBinding
    reviewer_principal_id: DomainId | None = None
    decided_at: UtcDateTime | None = None
    rationale: NonEmptyText | None = None
    revoker_principal_id: DomainId | None = None
    revocation_rationale: NonEmptyText | None = None
    revoked_at: UtcDateTime | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "subject",
            "reviewer_principal_id",
            "decided_at",
            "rationale",
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        }
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "reviewer_principal_id",
            "decided_at",
            "rationale",
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        }
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {
            "reviewer_principal_id",
            "decided_at",
            "rationale",
            "revoker_principal_id",
            "revocation_rationale",
            "revoked_at",
        }
    )

    TRANSITIONS: ClassVar = {
        ApprovalStatus.PENDING: frozenset(
            {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
        ),
        ApprovalStatus.APPROVED: frozenset({ApprovalStatus.REVOKED}),
        ApprovalStatus.REJECTED: frozenset(),
        ApprovalStatus.REVOKED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_decision(self) -> Approval:
        decision_values = (
            self.reviewer_principal_id,
            self.decided_at,
            self.rationale,
        )
        revocation_values = (
            self.revoker_principal_id,
            self.revocation_rationale,
            self.revoked_at,
        )
        if self.status == ApprovalStatus.PENDING:
            if any(
                value is not None for value in (*decision_values, *revocation_values)
            ):
                raise ValueError("pending approval cannot carry decision evidence")
        elif any(value is None for value in decision_values):
            raise ValueError("approval decision must be attributable and reasoned")
        if self.status == ApprovalStatus.REVOKED:
            if any(value is None for value in revocation_values):
                raise ValueError("revocation must be attributable and reasoned")
            if self.revoked_at != self.updated_at:
                raise ValueError("revoked_at must equal the revocation transition time")
        elif any(value is not None for value in revocation_values):
            raise ValueError("only a revoked approval can carry revocation evidence")
        if self.decided_at is not None:
            if not self.created_at <= self.decided_at <= self.updated_at:
                raise ValueError("decided_at must fit the approval audit interval")
        if self.revoked_at is not None and self.decided_at is not None:
            if self.revoked_at < self.decided_at:
                raise ValueError("revoked_at cannot precede decided_at")
        return self

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        if target in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            if changes.get("decided_at") != at:
                raise ValueError("decided_at must equal the decision transition time")
        if target == ApprovalStatus.REVOKED and changes.get("revoked_at") != at:
            raise ValueError("revoked_at must equal the revocation transition time")
        return super().transition_to(target, at=at, **changes)


class ExecutionEvent(MatterEntity):
    execution_id: DomainId
    step: ExecutionStep
    occurred_at: UtcDateTime
    correlation_id: ShortText
    actor_principal_id: DomainId
    receipt_id: DomainId | None = None

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "execution_id",
        "step",
        "occurred_at",
        "correlation_id",
        "actor_principal_id",
        "receipt_id",
    }

    @model_validator(mode="after")
    def validate_receipt_step(self) -> ExecutionEvent:
        if not self.created_at <= self.occurred_at <= self.updated_at:
            raise ValueError("execution event time must fit its audit interval")
        if self.step == ExecutionStep.RECEIPT_VERIFIED and self.receipt_id is None:
            raise ValueError("receipt-verified event requires receipt_id")
        if self.step != ExecutionStep.RECEIPT_VERIFIED and self.receipt_id is not None:
            raise ValueError("only a receipt-verified event can carry receipt_id")
        return self


class ExecutionReceipt(MatterEntity):
    execution_id: DomainId
    connector: ShortText
    external_receipt_id: ShortText
    artifact_content_sha256: Sha256
    destination_sha256: Sha256
    received_at: UtcDateTime
    verified_at: UtcDateTime

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "execution_id",
        "connector",
        "external_receipt_id",
        "artifact_content_sha256",
        "destination_sha256",
        "received_at",
        "verified_at",
    }

    @model_validator(mode="after")
    def validate_receipt_time(self) -> ExecutionReceipt:
        if self.received_at < self.created_at or self.verified_at > self.updated_at:
            raise ValueError("receipt times must fit its audit interval")
        if self.verified_at < self.received_at:
            raise ValueError("receipt verification cannot precede receipt")
        return self


class Execution(MatterStatefulEntity):
    subject: ArtifactBinding
    destination_sha256: Sha256
    idempotency_key: ShortText
    validation_result: ValidationResult | None = None
    approval: Approval | None = None
    events: tuple[ExecutionEvent, ...] = ()
    receipt: ExecutionReceipt | None = None
    status: ExecutionStatus = ExecutionStatus.DRAFT

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "subject",
            "destination_sha256",
            "idempotency_key",
            "validation_result",
            "approval",
            "receipt",
        }
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"validation_result", "approval", "receipt"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {"validation_result", "approval", "events", "receipt"}
    )

    TRANSITIONS: ClassVar = {
        ExecutionStatus.DRAFT: frozenset(
            {ExecutionStatus.VALIDATED, ExecutionStatus.CANCELLED}
        ),
        ExecutionStatus.VALIDATED: frozenset(
            {ExecutionStatus.APPROVED, ExecutionStatus.CANCELLED}
        ),
        ExecutionStatus.APPROVED: frozenset(
            {ExecutionStatus.QUEUED, ExecutionStatus.CANCELLED}
        ),
        ExecutionStatus.QUEUED: frozenset(
            {
                ExecutionStatus.DISPATCHED,
                ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED,
            }
        ),
        ExecutionStatus.DISPATCHED: frozenset(
            {ExecutionStatus.RECEIPT_VERIFIED, ExecutionStatus.FAILED}
        ),
        ExecutionStatus.RECEIPT_VERIFIED: frozenset(),
        ExecutionStatus.FAILED: frozenset({ExecutionStatus.QUEUED}),
        ExecutionStatus.CANCELLED: frozenset(),
    }

    @field_validator("events")
    @classmethod
    def validate_event_ids(
        cls, events: tuple[ExecutionEvent, ...]
    ) -> tuple[ExecutionEvent, ...]:
        event_ids = tuple(event.id for event in events)
        unique_ids(event_ids, "execution event ids")
        return events

    @model_validator(mode="after")
    def validate_execution_gates(self) -> Execution:
        for event in self.events:
            if event.tenant_id != self.tenant_id or event.matter_id != self.matter_id:
                raise ValueError("execution event crosses tenant or matter boundary")
            if event.execution_id != self.id:
                raise ValueError("execution event belongs to another execution")
            if event.updated_at > self.updated_at:
                raise ValueError("execution event audit time exceeds its execution")
        if self.receipt is not None:
            if self.receipt.tenant_id != self.tenant_id:
                raise ValueError("execution receipt crosses tenant boundary")
            if self.receipt.matter_id != self.matter_id:
                raise ValueError("execution receipt crosses matter boundary")
            if self.receipt.execution_id != self.id:
                raise ValueError("execution receipt belongs to another execution")
            if self.receipt.updated_at > self.updated_at:
                raise ValueError("execution receipt audit time exceeds its execution")
            if self.receipt.artifact_content_sha256 != self.subject.content_sha256:
                raise ValueError(
                    "execution receipt artifact hash does not match approval"
                )
            if self.receipt.destination_sha256 != self.destination_sha256:
                raise ValueError(
                    "execution receipt destination does not match execution"
                )

        progressed = {
            ExecutionStatus.VALIDATED,
            ExecutionStatus.APPROVED,
            ExecutionStatus.QUEUED,
            ExecutionStatus.DISPATCHED,
            ExecutionStatus.RECEIPT_VERIFIED,
            ExecutionStatus.FAILED,
        }
        if self.status == ExecutionStatus.DRAFT:
            if self.validation_result is not None or self.approval is not None:
                raise ValueError("draft execution cannot carry future gate evidence")
        if self.status in progressed and self.validation_result is None:
            raise ValueError("validated execution state requires validation")
        if self.status == ExecutionStatus.VALIDATED and self.approval is not None:
            raise ValueError("validated execution cannot carry approval early")
        if self.status in progressed - {ExecutionStatus.VALIDATED}:
            if self.approval is None:
                raise ValueError("approved execution state requires approval")

        if self.validation_result is not None:
            if (
                self.validation_result.tenant_id != self.tenant_id
                or self.validation_result.matter_id != self.matter_id
            ):
                raise ValueError(
                    "execution validation crosses tenant or matter boundary"
                )
            if self.validation_result.subject != self.subject:
                raise ValueError(
                    "execution validation does not bind the exact artifact"
                )
            if self.validation_result.outcome != ValidationOutcome.PASSED:
                raise ValueError("execution requires a passed validation result")
            if self.validation_result.updated_at > self.updated_at:
                raise ValueError(
                    "execution validation audit time exceeds its execution"
                )
        if self.approval is not None:
            if (
                self.approval.tenant_id != self.tenant_id
                or self.approval.matter_id != self.matter_id
            ):
                raise ValueError("execution approval crosses tenant or matter boundary")
            if self.approval.subject != self.subject:
                raise ValueError("execution approval does not bind the exact artifact")
            if self.approval.status != ApprovalStatus.APPROVED:
                raise ValueError("execution requires a currently approved decision")
            if self.approval.decided_at is None:
                raise ValueError("execution approval lacks decision evidence")
            if self.approval.updated_at > self.updated_at:
                raise ValueError("execution approval cannot be future-dated")

        step_status = {
            ExecutionStep.VALIDATED: ExecutionStatus.VALIDATED,
            ExecutionStep.APPROVED: ExecutionStatus.APPROVED,
            ExecutionStep.QUEUED: ExecutionStatus.QUEUED,
            ExecutionStep.DISPATCHED: ExecutionStatus.DISPATCHED,
            ExecutionStep.RECEIPT_VERIFIED: ExecutionStatus.RECEIPT_VERIFIED,
            ExecutionStep.FAILED: ExecutionStatus.FAILED,
            ExecutionStep.CANCELLED: ExecutionStatus.CANCELLED,
        }
        simulated_status = ExecutionStatus.DRAFT
        last_event_at = self.created_at
        for event in self.events:
            if event.occurred_at < last_event_at or event.occurred_at > self.updated_at:
                raise ValueError(
                    "execution events must be ordered within aggregate time"
                )
            event_status = step_status[event.step]
            if event_status not in self.TRANSITIONS[simulated_status]:
                raise ValueError("execution events do not follow the state graph")
            simulated_status = event_status
            last_event_at = event.occurred_at
        if simulated_status != self.status:
            raise ValueError("execution events do not prove the current state")
        if self.status != ExecutionStatus.RECEIPT_VERIFIED and self.receipt is not None:
            raise ValueError("execution receipt cannot precede receipt verification")
        if self.status == ExecutionStatus.RECEIPT_VERIFIED:
            if self.receipt is None:
                raise ValueError("receipt-verified execution requires a receipt")
            receipt_events = [
                event
                for event in self.events
                if event.step == ExecutionStep.RECEIPT_VERIFIED
                and event.receipt_id == self.receipt.id
            ]
            if not receipt_events:
                raise ValueError("receipt-verified execution requires matching event")
            dispatch_events = [
                event for event in self.events if event.step == ExecutionStep.DISPATCHED
            ]
            if not dispatch_events:
                raise ValueError("receipt-verified execution requires dispatch event")
            if self.receipt.received_at < dispatch_events[-1].occurred_at:
                raise ValueError("execution receipt cannot precede dispatch")
            if receipt_events[-1].occurred_at < self.receipt.verified_at:
                raise ValueError("receipt event cannot precede receipt verification")
        return self

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        checked_at = require_utc(at)
        supplied_events = changes.get("events")
        if not isinstance(supplied_events, tuple):
            raise DomainTransitionError(
                "execution transition requires an immutable event tuple"
            )
        if len(supplied_events) != len(self.events) + 1:
            raise DomainTransitionError(
                "execution transition must append exactly one event"
            )
        if supplied_events[:-1] != self.events:
            raise DomainTransitionError(
                "execution event history must preserve the exact prior prefix"
            )
        step_by_status: dict[StrEnum, ExecutionStep] = {
            ExecutionStatus.VALIDATED: ExecutionStep.VALIDATED,
            ExecutionStatus.APPROVED: ExecutionStep.APPROVED,
            ExecutionStatus.QUEUED: ExecutionStep.QUEUED,
            ExecutionStatus.DISPATCHED: ExecutionStep.DISPATCHED,
            ExecutionStatus.RECEIPT_VERIFIED: ExecutionStep.RECEIPT_VERIFIED,
            ExecutionStatus.FAILED: ExecutionStep.FAILED,
            ExecutionStatus.CANCELLED: ExecutionStep.CANCELLED,
        }
        expected_step = step_by_status.get(target)
        if expected_step is None or supplied_events[-1].step != expected_step:
            raise DomainTransitionError(
                "execution transition event does not match the target state"
            )
        if supplied_events[-1].occurred_at != checked_at:
            raise DomainTransitionError(
                "execution transition event must occur at the transition boundary"
            )
        return super().transition_to(target, at=checked_at, **changes)


def effective_at(
    entity: PartyRole | FactAssertion | Authority, instant: datetime
) -> bool:
    """Evaluate supported domain records at an exact UTC instant."""

    return entity.effective_interval.contains(instant)
