"""Claim ledger entities: source-span-grounded claims and support records."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    ClassVar,
    Self,
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
from ..exceptions import DomainTransitionError
from ..states import (
    ClaimSupportKind,
    LedgerClaimStatus,
)
from ..value_objects import (
    DomainId,
    NonEmptyText,
    Sha256,
    SourceReference,
)


class ClaimSupport(MatterEntity):
    """An append-only support or counter-support link to an exact source span."""

    claim_id: DomainId
    kind: ClaimSupportKind
    source_reference: SourceReference
    span_start: Annotated[int, Field(ge=0)]
    span_end: Annotated[int, Field(ge=1)]
    excerpt_sha256: Sha256
    note: NonEmptyText | None = None
    recorded_by_principal_id: DomainId
    policy_revision: Sha256

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = MatterEntity.IMMUTABLE_FIELDS | {
        "claim_id",
        "kind",
        "source_reference",
        "span_start",
        "span_end",
        "excerpt_sha256",
        "note",
        "recorded_by_principal_id",
        "policy_revision",
    }

    @model_validator(mode="after")
    def validate_span(self) -> ClaimSupport:
        if self.span_end <= self.span_start:
            raise ValueError("support span end must be later than span start")
        return self


class LedgerClaim(MatterStatefulEntity):
    """One ledger claim that must trace to exact source spans and a policy revision."""

    statement: NonEmptyText
    policy_revision: Sha256
    support: tuple[ClaimSupport, ...]
    status: LedgerClaimStatus = LedgerClaimStatus.PROPOSED

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS | {"policy_revision"}
    )

    TRANSITIONS: ClassVar = {
        LedgerClaimStatus.PROPOSED: frozenset(
            {LedgerClaimStatus.UNDER_REVIEW, LedgerClaimStatus.WITHDRAWN}
        ),
        LedgerClaimStatus.UNDER_REVIEW: frozenset(
            {
                LedgerClaimStatus.SUPPORTED,
                LedgerClaimStatus.CHALLENGED,
                LedgerClaimStatus.WITHDRAWN,
            }
        ),
        LedgerClaimStatus.SUPPORTED: frozenset(
            {LedgerClaimStatus.CHALLENGED, LedgerClaimStatus.WITHDRAWN}
        ),
        LedgerClaimStatus.CHALLENGED: frozenset(
            {LedgerClaimStatus.UNDER_REVIEW, LedgerClaimStatus.WITHDRAWN}
        ),
        LedgerClaimStatus.WITHDRAWN: frozenset(),
    }

    @field_validator("support")
    @classmethod
    def validate_support_ids(
        cls, values: tuple[ClaimSupport, ...]
    ) -> tuple[ClaimSupport, ...]:
        identifiers = tuple(record.id for record in values)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("support must not contain duplicate record identifiers")
        return values

    @model_validator(mode="after")
    def validate_support_trace(self) -> LedgerClaim:
        if not self.support:
            raise ValueError("ledger claim requires at least one support record")
        if not any(record.kind == ClaimSupportKind.SUPPORT for record in self.support):
            raise ValueError(
                "ledger claim requires at least one supporting source span"
            )
        for record in self.support:
            if (
                record.claim_id != self.id
                or record.tenant_id != self.tenant_id
                or record.matter_id != self.matter_id
            ):
                raise ValueError(
                    "claim support record crosses tenant, matter, or claim scope"
                )
        return self

    def _guard_support_history(self, changes: dict[str, Any]) -> None:
        if "support" not in changes:
            return
        revised = tuple(changes["support"])
        if revised[: len(self.support)] != self.support or len(revised) == len(
            self.support
        ):
            raise DomainTransitionError("claim support history is append-only")

    def evolve(self, *, at: datetime, **changes: Any) -> Self:
        self._guard_support_history(changes)
        return super().evolve(at=at, **changes)

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        self._guard_support_history(changes)
        return super().transition_to(target, at=at, **changes)

    def record_support(self, record: ClaimSupport, *, at: datetime) -> Self:
        """Append one new support or counter-support record."""

        return self.evolve(at=at, support=(*self.support, record))
