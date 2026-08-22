"""Work product and work product version entities."""

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

from ..base import MatterStatefulEntity
from ..exceptions import DomainTransitionError
from ..states import (
    WorkProductStatus,
    WorkProductVersionStatus,
)
from ..value_objects import (
    DomainId,
    Sha256,
    ShortText,
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
