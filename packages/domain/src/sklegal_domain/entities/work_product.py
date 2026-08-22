"""Work product, template, and bracketed-unknown entities."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    Self,
)

from pydantic import (
    Field,
    model_validator,
)

from ..base import MatterStatefulEntity, ProtectedStatefulEntity
from ..exceptions import DomainTransitionError
from ..states import (
    WorkProductStatus,
    WorkProductTemplateStatus,
    WorkProductTemplateVersionStatus,
    WorkProductUnknownStatus,
    WorkProductVersionStatus,
)
from ..value_objects import (
    ArtifactBinding,
    DomainId,
    PlaceholderKey,
    Sha256,
    ShortText,
    UtcDateTime,
)

WORK_PRODUCT_KINDS = (
    "memo",
    "letter",
    "pleading",
    "contract",
    "packet",
    "report",
    "other",
)


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


class WorkProductTemplate(ProtectedStatefulEntity):
    """A tenant-scoped drafting template bound to an exact current version."""

    name: ShortText
    work_product_kind: Literal[
        "memo", "letter", "pleading", "contract", "packet", "report", "other"
    ]
    current_version_id: DomainId
    status: WorkProductTemplateStatus = WorkProductTemplateStatus.DRAFT

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        ProtectedStatefulEntity.IMMUTABLE_FIELDS | {"work_product_kind"}
    )
    TRANSITIONS: ClassVar = {
        WorkProductTemplateStatus.DRAFT: frozenset(
            {WorkProductTemplateStatus.ACTIVE, WorkProductTemplateStatus.RETIRED}
        ),
        WorkProductTemplateStatus.ACTIVE: frozenset(
            {WorkProductTemplateStatus.RETIRED}
        ),
        WorkProductTemplateStatus.RETIRED: frozenset(),
    }
    _DRAFT_PAYLOAD_FIELDS: ClassVar[frozenset[str]] = frozenset({"name"})

    def evolve(self, *, at: datetime, **changes: Any) -> Self:
        attempted = self._DRAFT_PAYLOAD_FIELDS.intersection(changes)
        if attempted and self.status != WorkProductTemplateStatus.DRAFT:
            names = ", ".join(sorted(attempted))
            raise DomainTransitionError(
                f"template descriptive payload can change only in draft: {names}"
            )
        return super().evolve(at=at, **changes)


class WorkProductTemplateVersion(ProtectedStatefulEntity):
    """An immutable, hash-pinned template version.

    The bracketed unknown inventory is derived from the exact template content
    with ``sklegal_domain.drafting.extract_unknown_occurrences`` instead of a
    stored copy, so the inventory can never drift from the hashed body.
    """

    template_id: DomainId
    version_number: Annotated[int, Field(ge=1)]
    content_sha256: Sha256
    status: WorkProductTemplateVersionStatus = WorkProductTemplateVersionStatus.DRAFT

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        ProtectedStatefulEntity.IMMUTABLE_FIELDS
        | {
            "template_id",
            "version_number",
            "content_sha256",
        }
    )

    TRANSITIONS: ClassVar = {
        WorkProductTemplateVersionStatus.DRAFT: frozenset(
            {WorkProductTemplateVersionStatus.FROZEN}
        ),
        WorkProductTemplateVersionStatus.FROZEN: frozenset(
            {WorkProductTemplateVersionStatus.ARCHIVED}
        ),
        WorkProductTemplateVersionStatus.ARCHIVED: frozenset(),
    }


class WorkProductUnknown(MatterStatefulEntity):
    """A first-class bracketed unknown blocking one exact work product version."""

    version_binding: ArtifactBinding
    placeholder_key: PlaceholderKey
    hint: ShortText | None = None
    resolved_by_principal_id: DomainId | None = None
    resolved_at: UtcDateTime | None = None
    status: WorkProductUnknownStatus = WorkProductUnknownStatus.OPEN

    IMMUTABLE_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.IMMUTABLE_FIELDS
        | {
            "version_binding",
            "placeholder_key",
            "hint",
            "resolved_by_principal_id",
            "resolved_at",
        }
    )
    SET_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"resolved_by_principal_id", "resolved_at"}
    )
    TRANSITION_ONLY_FIELDS: ClassVar[frozenset[str]] = (
        MatterStatefulEntity.TRANSITION_ONLY_FIELDS
        | {"resolved_by_principal_id", "resolved_at"}
    )

    TRANSITIONS: ClassVar = {
        WorkProductUnknownStatus.OPEN: frozenset({WorkProductUnknownStatus.RESOLVED}),
        WorkProductUnknownStatus.RESOLVED: frozenset(),
    }

    @model_validator(mode="after")
    def validate_resolution(self) -> WorkProductUnknown:
        resolution = (self.resolved_by_principal_id, self.resolved_at)
        if self.status == WorkProductUnknownStatus.RESOLVED:
            if any(value is None for value in resolution):
                raise ValueError("resolved unknown requires resolution evidence")
            assert self.resolved_at is not None
            if self.resolved_at > self.updated_at:
                raise ValueError("resolution time cannot be later than updated_at")
        elif any(value is not None for value in resolution):
            raise ValueError("open unknown cannot carry resolution evidence")
        return self
