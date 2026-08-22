"""Issue, claim, defense, element, and remedy entities."""

from __future__ import annotations

from typing import (
    ClassVar,
    Literal,
)

from pydantic import (
    field_validator,
    model_validator,
)

from ..base import MatterStatefulEntity
from ..states import (
    ClaimStatus,
    ElementStatus,
    IssueStatus,
    RemedyStatus,
)
from ..value_objects import (
    DomainId,
    NonEmptyText,
    ShortText,
    unique_ids,
)


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
