"""Immutable value objects shared by SKLegal legal-domain aggregates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .exceptions import DomainError
from .states import LegacyRecordKind


def require_domain_id(value: UUID) -> UUID:
    if value.int == 0:
        raise ValueError("domain identifiers cannot be nil UUIDs")
    return value


def require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    if offset.total_seconds() != 0:
        raise ValueError("timestamp must use UTC offset zero")
    return value.astimezone(UTC)


DomainId = Annotated[UUID, AfterValidator(require_domain_id)]
UtcDateTime = Annotated[datetime, AfterValidator(require_utc)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PlaceholderKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
]
LegacySlug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]

LEGAL_VALIDATION_SUBJECT_KINDS = (
    "party",
    "party_role",
    "matter_event",
    "fact_assertion",
    "evidence_item",
    "authority",
    "claim",
    "defense",
    "deadline",
)


class FrozenValue(BaseModel):
    """Strict and immutable value-object base."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update:
            raise DomainError("unvalidated value-object copy updates are disabled")
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
            raise DomainError("unvalidated value-object copy changes are disabled")
        return self.model_copy(deep=deep)

    def __replace__(self, **changes: Any) -> Self:
        if changes:
            raise DomainError("unvalidated value-object replacement is disabled")
        return self.model_copy()

    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        del _fields_set, values
        raise DomainError("unvalidated value-object construction is disabled")


class EffectiveInterval(FrozenValue):
    """A UTC, start-inclusive and end-exclusive effective-time interval."""

    valid_from: UtcDateTime | None = None
    valid_to: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_order(self) -> EffectiveInterval:
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be later than valid_from")
        return self

    @property
    def is_unknown(self) -> bool:
        return self.valid_from is None and self.valid_to is None

    def contains(self, instant: datetime) -> bool:
        checked = require_utc(instant)
        if self.is_unknown:
            return False
        if self.valid_from is not None and checked < self.valid_from:
            return False
        return self.valid_to is None or checked < self.valid_to

    def overlaps(self, other: EffectiveInterval) -> bool:
        if self.is_unknown or other.is_unknown:
            return False
        if self.valid_to is not None and other.valid_from is not None:
            if self.valid_to <= other.valid_from:
                return False
        if other.valid_to is not None and self.valid_from is not None:
            if other.valid_to <= self.valid_from:
                return False
        return True


class ArtifactBinding(FrozenValue):
    """An exact immutable artifact version and content digest."""

    artifact_id: DomainId
    artifact_version: Annotated[int, Field(ge=1)]
    content_sha256: Sha256


class ValidationSubject(FrozenValue):
    """Exact typed validation subject for a non-artifact legal record."""

    subject_kind: Literal[
        "party",
        "party_role",
        "matter_event",
        "fact_assertion",
        "evidence_item",
        "authority",
        "claim",
        "defense",
        "deadline",
    ]
    artifact_id: DomainId
    artifact_version: Annotated[int, Field(ge=1)]
    content_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_digest_applicability(self) -> ValidationSubject:
        if self.content_sha256 is not None:
            raise ValueError("legal validation subject cannot carry an artifact digest")
        return self


class LegacyAlias(FrozenValue):
    """Lossless HammerTime provenance without defining a canonical ITIL type."""

    source_system: Literal["hammertime"] = "hammertime"
    record_kind: LegacyRecordKind
    legacy_id: str
    legacy_slug: LegacySlug
    legacy_path: str
    source_version: ShortText
    content_sha256: Sha256
    observed_at: UtcDateTime

    @field_validator("legacy_id")
    @classmethod
    def validate_legacy_id(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("legacy_id must be non-empty without outer whitespace")
        return value

    @field_validator("legacy_path")
    @classmethod
    def validate_legacy_path(cls, value: str) -> str:
        if not value or value != value.strip() or "\\" in value or "\x00" in value:
            raise ValueError("legacy_path must be a normalized relative POSIX path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("legacy_path must be a normalized relative POSIX path")
        if str(path) != value:
            raise ValueError("legacy_path must be a normalized relative POSIX path")
        return value

    @model_validator(mode="after")
    def validate_kind_and_identifier(self) -> LegacyAlias:
        patterns = {
            LegacyRecordKind.CONTAINER: r"^PRB-[0-9]{4}-[0-9]{3,}$",
            LegacyRecordKind.ACTIVITY: r"^INC-[0-9]{3,}$",
        }
        if re.fullmatch(patterns[self.record_kind], self.legacy_id) is None:
            raise ValueError("legacy_id does not match its historical record kind")
        return self


class SourceReference(FrozenValue):
    """An immutable pointer to a source version, without source payload."""

    source_reference_id: DomainId
    source_system: ShortText
    source_version: ShortText
    content_sha256: Sha256
    locator: NonEmptyText
    observed_at: UtcDateTime


class TypedValue(FrozenValue):
    """A bounded, JSON-compatible fact value with an explicit type label."""

    value_type: Literal["string", "integer", "number", "boolean", "null"]
    value: str | int | float | bool | None

    @model_validator(mode="after")
    def validate_runtime_type(self) -> TypedValue:
        expected: dict[str, tuple[type, ...]] = {
            "string": (str,),
            "integer": (int,),
            "number": (int, float),
            "boolean": (bool,),
            "null": (type(None),),
        }
        if self.value_type == "integer" and isinstance(self.value, bool):
            raise ValueError("fact value does not match value_type")
        if self.value_type == "number" and isinstance(self.value, bool):
            raise ValueError("fact value does not match value_type")
        if not isinstance(self.value, expected[self.value_type]):
            raise ValueError("fact value does not match value_type")
        if isinstance(self.value, float) and not isfinite(self.value):
            raise ValueError("numeric fact value must be finite")
        return self


def unique_ids(values: tuple[DomainId, ...], field_name: str) -> tuple[DomainId, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicate identifiers")
    return values


def scrub_transition_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy so caller-owned mappings are never retained."""

    return dict(changes)
