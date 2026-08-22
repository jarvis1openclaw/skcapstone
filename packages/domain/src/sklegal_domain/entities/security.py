"""Tenant, client, and engagement boundary entities."""

from __future__ import annotations

from typing import (
    ClassVar,
    Literal,
)

from pydantic import (
    Field,
    model_validator,
)

from ..base import ProtectedStatefulEntity
from ..states import (
    ClientStatus,
    EngagementStatus,
    TenantStatus,
)
from ..value_objects import (
    DomainId,
    EffectiveInterval,
    NonEmptyText,
    ShortText,
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
