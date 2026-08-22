"""Fact assertion, tension group, evidence, custody, and authority entities."""

from __future__ import annotations

from datetime import datetime
from typing import (
    Annotated,
    ClassVar,
    Literal,
)

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from ..base import (
    MatterEntity,
    MatterStatefulEntity,
)
from ..states import (
    AuthorityStatus,
    EvidenceStatus,
    FactReviewStatus,
    TensionStatus,
)
from ..value_objects import (
    DomainId,
    EffectiveInterval,
    NonEmptyText,
    Sha256,
    ShortText,
    SourceReference,
    TypedValue,
    UtcDateTime,
    unique_ids,
)
from .structure import PartyRole


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


def effective_at(
    entity: PartyRole | FactAssertion | Authority, instant: datetime
) -> bool:
    """Evaluate supported domain records at an exact UTC instant."""

    return entity.effective_interval.contains(instant)
