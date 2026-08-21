"""Typed value objects returned by the read-only HammerTime adapter.

Every response pins a source snapshot: the exact normalized relative path,
the SHA-256 of the exact bytes read, and the UTC observation time. Raw
payloads are preserved losslessly alongside typed fields so provenance,
uncertainty, and contradictions survive the boundary unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, StringConstraints
from sklegal_domain import LegacyAlias, LegacyRecordKind
from sklegal_domain.value_objects import (
    FrozenValue,
    LegacySlug,
    NonEmptyText,
    Sha256,
    ShortText,
    UtcDateTime,
)


def require_relative_posix(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or "\x00" in value:
        raise ValueError("path must be a normalized relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must be a normalized relative POSIX path")
    if str(path) != value:
        raise ValueError("path must be a normalized relative POSIX path")
    return value


RelativePosixPath = Annotated[str, AfterValidator(require_relative_posix)]
ReleaseId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=192,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    ),
]
AliasTarget = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
DecompositionId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
LegacyRecordId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^(?:PRB-[0-9]{4}-[0-9]{3,}|INC-[0-9]{3,})$",
    ),
]


class SnapshotPin(FrozenValue):
    """Immutable provenance pin attached to every adapter response."""

    relative_path: RelativePosixPath
    content_sha256: Sha256
    observed_at: UtcDateTime
    release_id: ReleaseId | None = None
    alias_target: AliasTarget | None = None


class ArtifactRead(FrozenValue):
    """Exact bytes read from one pinned HammerTime artifact."""

    pin: SnapshotPin
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")


class SourceHashVerification(FrozenValue):
    """Proof that an observed artifact hash equals the pinned expectation."""

    pin: SnapshotPin
    expected_sha256: Sha256
    verified: Literal[True] = True


class DocumentCounts(FrozenValue):
    """Release manifest change counters."""

    new: int = Field(ge=0)
    changed: int = Field(ge=0)
    deleted: int = Field(ge=0)
    unchanged: int = Field(ge=0)


class ReleaseManifest(FrozenValue):
    """One immutable HammerTime corpus release manifest, pinned and lossless."""

    pin: SnapshotPin
    release_id: ReleaseId
    release_target: AliasTarget
    schema_version: int = Field(ge=1)
    vector_collection: ShortText
    graph_name: ShortText
    mode: ShortText | None = None
    generated_at: ShortText | None = None
    source_commit: str | None = None
    document_counts: DocumentCounts
    documents: dict[str, list[str]]
    verification: dict[str, Any]
    decomposed_snapshot: dict[str, Any] | None = None
    raw: dict[str, Any]


class ReleaseSummary(FrozenValue):
    """Listing entry for one release manifest without its full payload."""

    release_id: ReleaseId
    release_target: AliasTarget | None
    generated_at: ShortText | None
    pin: SnapshotPin


class AliasBinding(FrozenValue):
    """One current or previous runtime binding for an environment alias."""

    release_id: ReleaseId
    manifest_path: NonEmptyText
    promoted_at: ShortText | None = None
    vector_collection: ShortText | None = None
    graph_name: ShortText | None = None


class RuntimeAlias(FrozenValue):
    """Current and previous bindings that enable clean roll-forward/back."""

    target: AliasTarget
    current: AliasBinding
    previous: AliasBinding | None = None


class RuntimeAliasesSnapshot(FrozenValue):
    """Pinned snapshot of the HammerTime runtime alias state file."""

    pin: SnapshotPin
    schema_version: int = Field(ge=1)
    updated_at: ShortText | None = None
    aliases: dict[AliasTarget, RuntimeAlias]


class ResolvedRelease(FrozenValue):
    """The release an alias currently points at, verified against its manifest.

    Non-fatal disagreements between the alias binding and the manifest are
    preserved verbatim in ``drift`` instead of being silently harmonized.
    """

    aliases_pin: SnapshotPin
    alias: RuntimeAlias
    manifest: ReleaseManifest
    drift: list[ShortText]


class DecomposedStateSeal(FrozenValue):
    """Pinned snapshot of the sealed decomposed-artifact state."""

    pin: SnapshotPin
    release_id: str
    release_target: str | None = None
    schema_version: str | None = None
    sealed_at: ShortText | None = None
    snapshot: dict[str, Any]
    raw: dict[str, Any]


class DecompositionChunk(FrozenValue):
    chunk_id: NonEmptyText
    chunk_index: int = Field(ge=0)
    text: str
    parent_doc: str | None = None
    section_title: str | None = None
    total_chunks: int | None = Field(default=None, ge=1)


class DecompositionClaim(FrozenValue):
    claim_id: NonEmptyText
    text: str
    source_file: str | None = None
    line: int | None = Field(default=None, ge=1)
    category: str | None = None
    confidence: str | None = None


class DecompositionEntity(FrozenValue):
    entity_id: NonEmptyText
    name: str
    type: str | None = None
    source_file: str | None = None
    context: str | None = None


class Decomposition(FrozenValue):
    """One decomposed document: chunks, claims, citations, entities, and more.

    Citation and relationship entries stay as raw mappings because their
    schema is owned by HammerTime and still evolving; counts are always
    exposed through ``stats``.
    """

    pin: SnapshotPin
    document_id: DecompositionId
    source_file: NonEmptyText
    decomposed_at: ShortText | None = None
    frontmatter: dict[str, Any]
    stats: dict[str, int]
    chunks: list[DecompositionChunk]
    claims: list[DecompositionClaim]
    citations: list[dict[str, Any]]
    entities: list[DecompositionEntity]
    relationships: list[dict[str, Any]]
    raw: dict[str, Any]


class MatterAccessRequest(FrozenValue):
    """Sanitized matter-access question asked of the configured authorizer."""

    legacy_id: LegacyRecordId
    record_kind: LegacyRecordKind
    parent_legacy_id: LegacyRecordId | None = None
    relative_path: RelativePosixPath | None = None


MatterAuthorizer = Callable[[MatterAccessRequest], bool]
"""Returns True only when the caller may read the exact matter record."""


class LegacyPathResolution(FrozenValue):
    """Authorized, snapshot-pinned resolution of one legacy record path."""

    pin: SnapshotPin
    registry_pin: SnapshotPin
    record_kind: LegacyRecordKind
    legacy_id: LegacyRecordId
    relative_path: RelativePosixPath
    parent_legacy_id: LegacyRecordId | None = None


class LegacyMatterRecord(FrozenValue):
    """One legacy matter container or activity record, pinned and lossless."""

    pin: SnapshotPin
    registry_pin: SnapshotPin
    record_kind: LegacyRecordKind
    legacy_id: LegacyRecordId
    slug: LegacySlug
    relative_path: RelativePosixPath
    parent_legacy_id: NonEmptyText | None = None
    frontmatter: dict[str, Any]
    body: str

    def to_legacy_alias(self, *, source_version: str) -> LegacyAlias:
        """Project this record into the canonical domain provenance alias."""
        return LegacyAlias(
            record_kind=self.record_kind,
            legacy_id=self.legacy_id,
            legacy_slug=self.slug,
            legacy_path=self.relative_path,
            source_version=source_version,
            content_sha256=self.pin.content_sha256,
            observed_at=self.pin.observed_at,
        )


class MatterValidationReport(FrozenValue):
    """Pinned HammerTime validation result for one legacy activity record."""

    pin: SnapshotPin
    legacy_id: LegacyRecordId
    validated_date: ShortText | None = None
    valid: bool | None = None
    errors: list[Any]
    notices: list[Any]
    sections: dict[str, Any]
    raw: dict[str, Any]


class PacketReference(FrozenValue):
    """Pinned reference to one versioned packet facts payload."""

    packet_version: int = Field(ge=1)
    facts_pin: SnapshotPin
    facts: dict[str, Any]
    review_pin: SnapshotPin | None = None

    @property
    def review_path(self) -> RelativePosixPath | None:
        """Compatibility view derived only from a pinned review artifact."""
        if self.review_pin is None:
            return None
        return self.review_pin.relative_path


def mapping_of(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy an external mapping into a plain dict without altering content."""
    return {str(key): item for key, item in value.items()}
