"""Matter, party, forum, proceeding, matter event, and transaction entities."""

from __future__ import annotations

from typing import (
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
    ProtectedStatefulEntity,
)
from ..states import (
    LegacyRecordKind,
    MatterEventStatus,
    MatterStatus,
    PartyRoleStatus,
    ProceedingStatus,
    RecordCompleteness,
    TransactionStatus,
    VerificationStatus,
)
from ..value_objects import (
    DomainId,
    EffectiveInterval,
    LegacyAlias,
    NonEmptyText,
    ShortText,
    SourceReference,
    UtcDateTime,
    unique_ids,
)


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
