"""Bracketed unknown extraction, freeze readiness, and supersession planning.

Pure domain helpers for the work product drafting slice. No I/O and no model
calls: models produce typed proposals, and these helpers reduce typed records
into readiness decisions. A bracketed unknown uses the explicit marker syntax
``[?key]`` or ``[?key: optional hint]`` so ordinary square brackets in legal
text never become placeholders.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import Field

from .entities.work_product import (
    WorkProduct,
    WorkProductUnknown,
    WorkProductVersion,
)
from .exceptions import DomainTransitionError
from .states import (
    WorkProductUnknownStatus,
    WorkProductVersionStatus,
)
from .value_objects import (
    ArtifactBinding,
    DomainId,
    FrozenValue,
    PlaceholderKey,
    Sha256,
    ShortText,
    UtcDateTime,
)

UNKNOWN_PLACEHOLDER_PATTERN = re.compile(
    r"\[\?(?P<key>[a-z][a-z0-9_]{0,63})(?::(?P<hint>[^\]\n]{1,512}))?\]"
)


class UnknownOccurrence(FrozenValue):
    """One located bracketed unknown inside a draft or template body."""

    placeholder_key: PlaceholderKey
    hint: ShortText | None
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=1)]

    @property
    def marker(self) -> str:
        if self.hint is None:
            return f"[?{self.placeholder_key}]"
        return f"[?{self.placeholder_key}: {self.hint}]"


def extract_unknown_occurrences(content: str) -> tuple[UnknownOccurrence, ...]:
    """Locate every bracketed unknown in document order without deduplication."""

    if not isinstance(content, str):
        raise TypeError("content must be text")
    occurrences: list[UnknownOccurrence] = []
    for match in UNKNOWN_PLACEHOLDER_PATTERN.finditer(content):
        hint = match.group("hint")
        occurrences.append(
            UnknownOccurrence(
                placeholder_key=match.group("key"),
                hint=hint.strip() if hint is not None else None,
                start=match.start(),
                end=match.end(),
            )
        )
    return tuple(occurrences)


def register_unknown_keys(
    occurrences: tuple[UnknownOccurrence, ...],
) -> tuple[UnknownOccurrence, ...]:
    """Reduce occurrences to one first-class blocker per placeholder key.

    The first occurrence wins so the blocker inventory stays deterministic;
    every later occurrence of the same key remains visible in the raw
    extraction output and is never silently rewritten.
    """

    seen: set[str] = set()
    unique: list[UnknownOccurrence] = []
    for occurrence in occurrences:
        if occurrence.placeholder_key in seen:
            continue
        seen.add(occurrence.placeholder_key)
        unique.append(occurrence)
    return tuple(unique)


def _binds_exactly(unknown: WorkProductUnknown, version: WorkProductVersion) -> bool:
    binding = unknown.version_binding
    return (
        unknown.tenant_id == version.tenant_id
        and unknown.matter_id == version.matter_id
        and binding.artifact_id == version.id
        and binding.artifact_version == version.version_number
        and binding.content_sha256 == version.content_sha256
    )


def unresolved_blockers(
    version: WorkProductVersion,
    unknowns: tuple[WorkProductUnknown, ...],
) -> tuple[WorkProductUnknown, ...]:
    """Return the open unknowns bound to one exact work product version."""

    return tuple(
        unknown
        for unknown in unknowns
        if _binds_exactly(unknown, version)
        and unknown.status == WorkProductUnknownStatus.OPEN
    )


def assert_freeze_ready(
    version: WorkProductVersion,
    unknowns: tuple[WorkProductUnknown, ...],
) -> None:
    """Fail closed when any bracketed unknown still blocks the exact version."""

    blockers = unresolved_blockers(version, unknowns)
    if blockers:
        keys = ", ".join(sorted(unknown.placeholder_key for unknown in blockers))
        raise DomainTransitionError(
            f"work product version has unresolved bracketed unknowns: {keys}"
        )


def build_successor_version(
    work_product: WorkProduct,
    current_version: WorkProductVersion,
    *,
    version_id: DomainId,
    content_sha256: Sha256,
    source_artifact_id: DomainId,
    at: UtcDateTime,
) -> WorkProductVersion:
    """Construct the next draft version in one supersession chain."""

    if (
        work_product.tenant_id != current_version.tenant_id
        or work_product.matter_id != current_version.matter_id
        or work_product.id != current_version.work_product_id
    ):
        raise DomainTransitionError(
            "successor version must stay inside the work product scope"
        )
    if work_product.current_version_id != current_version.id:
        raise DomainTransitionError(
            "successor versions build only on the current work product version"
        )
    if current_version.status != WorkProductVersionStatus.FROZEN:
        raise DomainTransitionError(
            "successor versions build only on a frozen current version"
        )
    if content_sha256 == current_version.content_sha256:
        raise DomainTransitionError(
            "a successor version must change the exact content digest"
        )
    return WorkProductVersion(
        id=version_id,
        tenant_id=work_product.tenant_id,
        matter_id=work_product.matter_id,
        classification=current_version.classification,
        completeness=current_version.completeness,
        work_product_id=work_product.id,
        version_number=current_version.version_number + 1,
        content_sha256=content_sha256,
        source_artifact_id=source_artifact_id,
        created_at=at,
        updated_at=at,
    )


def supersede_version(
    version: WorkProductVersion, *, at: datetime
) -> WorkProductVersion:
    """Move one frozen version to superseded inside its chain."""

    return version.transition_to(WorkProductVersionStatus.SUPERSEDED, at=at)


def assert_approval_matches_version(
    approval_subject: ArtifactBinding,
    version: WorkProductVersion,
) -> None:
    """Fail closed when an approval no longer names the exact version."""

    if (
        approval_subject.artifact_id != version.id
        or approval_subject.artifact_version != version.version_number
        or approval_subject.content_sha256 != version.content_sha256
    ):
        raise DomainTransitionError(
            "approval binds a different exact version: content changed after approval"
        )
