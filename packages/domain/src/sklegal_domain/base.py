"""Immutable aggregate bases and deterministic state-machine reduction."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .exceptions import DomainTransitionError, IdentityMutationError
from .states import DataClassification, RecordCompleteness
from .value_objects import (
    DomainId,
    LegacyAlias,
    SourceReference,
    UtcDateTime,
    require_utc,
    scrub_transition_changes,
)


class DomainEntity(BaseModel):
    """A frozen entity whose explicit evolution preserves identity."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"id", "created_at"})
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset()
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = frozenset({"status"})

    id: DomainId
    version: int = Field(default=1, ge=1)
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_audit_time(self) -> DomainEntity:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            direct_values = value if isinstance(value, tuple) else (value,)
            for item in direct_values:
                if isinstance(item, (SourceReference, LegacyAlias)):
                    if item.observed_at > self.updated_at:
                        raise ValueError(
                            "embedded source provenance cannot be later than "
                            "the owning entity audit time"
                        )
        return self

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update:
            raise IdentityMutationError("unvalidated entity copy updates are disabled")
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
            raise IdentityMutationError("unvalidated entity copy changes are disabled")
        return self.model_copy(deep=deep)

    def __replace__(self, **changes: Any) -> Self:
        if changes:
            raise IdentityMutationError("unvalidated entity replacement is disabled")
        return self.model_copy()

    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        del _fields_set, values
        raise IdentityMutationError("unvalidated entity construction is disabled")

    def identity_values(self) -> tuple[object, ...]:
        return tuple(getattr(self, name) for name in sorted(self.IMMUTABLE_FIELDS))

    def evolve(self, *, at: datetime, **changes: Any) -> Self:
        attempted = self.TRANSITION_ONLY_FIELDS.intersection(changes)
        if attempted:
            names = ", ".join(sorted(attempted))
            raise DomainTransitionError(
                f"state evidence can change only through transition_to: {names}"
            )
        return self._reduce(at=at, changes=changes, transition=False)

    def _reduce(
        self, *, at: datetime, changes: dict[str, Any], transition: bool
    ) -> Self:
        checked_at = require_utc(at)
        if checked_at < self.updated_at:
            raise DomainTransitionError(
                "aggregate evolution cannot move backward in time"
            )
        forbidden = self.IMMUTABLE_FIELDS | {"updated_at", "version"}
        attempted = forbidden.intersection(changes)
        invalid_immutable = {
            name
            for name in attempted
            if not (
                transition
                and name in self.SET_ONCE_FIELDS
                and getattr(self, name) is None
                and changes[name] is not None
            )
        }
        if invalid_immutable:
            names = ", ".join(sorted(invalid_immutable))
            raise IdentityMutationError(f"identity fields cannot change: {names}")
        data = self.model_dump(mode="python")
        data.update(scrub_transition_changes(changes))
        data["version"] = self.version + 1
        data["updated_at"] = checked_at
        candidate = type(self).model_validate(data)
        for name in self.IMMUTABLE_FIELDS:
            if getattr(candidate, name) == getattr(self, name):
                continue
            if transition and name in self.SET_ONCE_FIELDS:
                if getattr(self, name) is None and getattr(candidate, name) is not None:
                    continue
            raise IdentityMutationError(
                f"identity field changed during evolution: {name}"
            )
        return candidate


class ProtectedEntity(DomainEntity):
    """An entity inside a tenant security boundary."""

    tenant_id: DomainId
    classification: DataClassification = DataClassification.CONFIDENTIAL

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = DomainEntity.IMMUTABLE_FIELDS | {
        "tenant_id"
    }

    def identity_values(self) -> tuple[object, ...]:
        return (self.id, self.tenant_id)


class MatterEntity(ProtectedEntity):
    """A protected record that is always scoped to one matter."""

    matter_id: DomainId
    completeness: RecordCompleteness = RecordCompleteness.INCOMPLETE

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = ProtectedEntity.IMMUTABLE_FIELDS | {
        "matter_id"
    }

    def identity_values(self) -> tuple[object, ...]:
        return (self.id, self.tenant_id, self.matter_id)


class StatefulEntity(DomainEntity):
    """A frozen entity with an explicit transition graph."""

    status: StrEnum
    TRANSITIONS: ClassVar[dict[StrEnum, frozenset[StrEnum]]] = {}

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        if "status" in changes:
            raise DomainTransitionError("status must be supplied only as target")
        if type(target) is not type(self.status):
            raise DomainTransitionError(
                "target status belongs to another state machine"
            )
        allowed = self.TRANSITIONS.get(self.status, frozenset())
        if target not in allowed:
            raise DomainTransitionError(
                f"invalid {type(self).__name__} transition: "
                f"{self.status.value} -> {target.value}"
            )
        return self._reduce(
            at=at, changes={"status": target, **changes}, transition=True
        )


class ProtectedStatefulEntity(ProtectedEntity):
    """A tenant-scoped state machine."""

    status: StrEnum
    TRANSITIONS: ClassVar[dict[StrEnum, frozenset[StrEnum]]] = {}

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        if "status" in changes:
            raise DomainTransitionError("status must be supplied only as target")
        if type(target) is not type(self.status):
            raise DomainTransitionError(
                "target status belongs to another state machine"
            )
        if target not in self.TRANSITIONS.get(self.status, frozenset()):
            raise DomainTransitionError(
                f"invalid {type(self).__name__} transition: "
                f"{self.status.value} -> {target.value}"
            )
        return self._reduce(
            at=at, changes={"status": target, **changes}, transition=True
        )


class MatterStatefulEntity(MatterEntity):
    """A tenant-and-matter-scoped state machine."""

    status: StrEnum
    TRANSITIONS: ClassVar[dict[StrEnum, frozenset[StrEnum]]] = {}

    def transition_to(self, target: StrEnum, *, at: datetime, **changes: Any) -> Self:
        if "status" in changes:
            raise DomainTransitionError("status must be supplied only as target")
        if type(target) is not type(self.status):
            raise DomainTransitionError(
                "target status belongs to another state machine"
            )
        if target not in self.TRANSITIONS.get(self.status, frozenset()):
            raise DomainTransitionError(
                f"invalid {type(self).__name__} transition: "
                f"{self.status.value} -> {target.value}"
            )
        return self._reduce(
            at=at, changes={"status": target, **changes}, transition=True
        )
