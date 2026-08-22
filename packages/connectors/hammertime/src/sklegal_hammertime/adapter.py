"""Read-only HammerTime release and artifact adapter.

This module is the only SKLegal code allowed to construct HammerTime
filesystem paths. It wraps release aliases, release manifests, artifact
reads, source hashes, decompositions, packet references, validation
results, legacy matter path resolution, and gated matter-tree artifact
inventory behind typed read-only APIs.

Hard boundaries:

- The adapter never opens a file with anything but read-only flags and
  exposes no write, move, delete, or dispatch surface.
- ``Inbox/`` is never searched, read, or returned, in any casing.
- Matter-scoped content (legacy problem/incident records, validation
  reports, packet facts, owner directions) requires an allow decision
  from the configured matter authorizer and fails closed without one.
- Every response pins the exact relative path, content SHA-256, and UTC
  observation time of the bytes that were read.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sklegal_domain import LegacyRecordKind

from .errors import (
    AdapterUnavailableError,
    AmbiguousLegacyIdError,
    ArtifactTooLargeError,
    ForbiddenPathError,
    MalformedFrontmatterError,
    MalformedManifestError,
    MatterAccessDenied,
    MissingPathError,
    RegistryMismatchError,
    SourceHashMismatchError,
    StaleReleaseError,
)
from .frontmatter import split_frontmatter
from .models import (
    AliasBinding,
    ArtifactRead,
    DecomposedStateSeal,
    Decomposition,
    DecompositionChunk,
    DecompositionClaim,
    DecompositionEntity,
    DocumentCounts,
    LegacyMatterRecord,
    LegacyPathResolution,
    MatterAccessRequest,
    MatterArtifact,
    MatterArtifactInventory,
    MatterAuthorizer,
    MatterValidationReport,
    PacketReference,
    ReleaseManifest,
    ReleaseSummary,
    ResolvedRelease,
    RuntimeAlias,
    RuntimeAliasesSnapshot,
    SnapshotPin,
    SourceHashVerification,
    mapping_of,
)

ROOT_ENV_VAR = "SKLEGAL_HAMMERTIME_ROOT"
DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

_RELEASES_DIR = PurePosixPath("json/releases")
_ALIASES_PATH = PurePosixPath("json/state/runtime-aliases.json")
_DECOMPOSED_DIR = PurePosixPath("json/decomposed")
_DECOMPOSED_STATE = PurePosixPath("json/state/decomposed-state.json")
_REGISTRY_PATH = PurePosixPath("incidents/_incident-registry.md")
_PROBLEMS_DIR = PurePosixPath("incidents/problems")

_RELEASE_PREFIX = "corpus-release-"
_RELEASE_SUFFIX = ".json"
_RELEASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,191}$")
_ALIAS_TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_DECOMPOSITION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_LEGACY_ID_PATTERN = re.compile(r"^(?:PRB-[0-9]{4}-[0-9]{3,}|INC-[0-9]{3,})$")
_REGISTRY_LINK_PATTERN = re.compile(r"\((problems/[^()\s]+)\)")
_REGISTRY_ID_PATTERN = re.compile(r"\b(?:PRB-[0-9]{4}-[0-9]{3,}|INC-[0-9]{3,})\b")
_PACKET_FACTS_PATTERN = re.compile(r"^packet-v([0-9]+)-facts\.json$")

# Artifact reads are bounded to corpus and reference subtrees. Legacy
# matter content under incidents/ is only reachable through the gated
# matter APIs, and Inbox/ is never reachable at all.
_ARTIFACT_ROOTS = frozenset(
    {"reference", "json", "summaries", "knowledge", "templates", "profiles", "docs"}
)
_FORBIDDEN_ROOTS = frozenset({"inbox", "incidents"})

_MANIFEST_REQUIRED_KEYS = (
    "release_id",
    "release_target",
    "schema_version",
    "vector_collection",
    "graph_name",
    "document_counts",
    "documents",
    "verification",
)


def _record_kind(legacy_id: str) -> LegacyRecordKind:
    if legacy_id.startswith("PRB-"):
        return LegacyRecordKind.CONTAINER
    return LegacyRecordKind.ACTIVITY


@dataclass(frozen=True)
class _LegacyRead:
    relative: PurePosixPath
    record_kind: LegacyRecordKind
    parent_legacy_id: str | None
    content: bytes
    digest: str
    registry_pin: SnapshotPin


class HammerTimeReleaseAdapter:
    """Typed read-only access to one HammerTime corpus root."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        matter_authorizer: MatterAuthorizer | None = None,
        clock: Callable[[], datetime] | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        if root is None:
            root = os.environ.get(ROOT_ENV_VAR)
        if root is None:
            raise AdapterUnavailableError(
                f"HammerTime root is not configured; set {ROOT_ENV_VAR} "
                "or pass root explicitly"
            )
        candidate = Path(root)
        if not candidate.is_dir():
            raise AdapterUnavailableError(
                f"HammerTime root is not a readable directory: {root}"
            )
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise AdapterUnavailableError(
                "the host cannot enforce symlink-safe HammerTime reads"
            )
        self._root = candidate.resolve()
        self._matter_authorizer = matter_authorizer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_artifact_bytes = max_artifact_bytes

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # path safety and pinned reads
    # ------------------------------------------------------------------

    def _resolve(self, relative_path: str) -> PurePosixPath:
        """Validate and normalize a caller-supplied relative path."""
        if (
            not relative_path
            or relative_path != relative_path.strip()
            or "\\" in relative_path
            or "\x00" in relative_path
        ):
            raise ForbiddenPathError(f"denied non-normalized path: {relative_path!r}")
        pure = PurePosixPath(relative_path)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or str(pure) != relative_path
        ):
            raise ForbiddenPathError(f"denied non-normalized path: {relative_path!r}")
        pure = self._validate_relative(pure)
        first = pure.parts[0].lower()
        if first in _FORBIDDEN_ROOTS:
            raise ForbiddenPathError(
                f"denied path under {pure.parts[0]}/: {relative_path!r}"
            )
        return pure

    def _validate_relative(self, relative: PurePosixPath) -> PurePosixPath:
        """Fail closed on internal and caller-supplied path components."""
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or any(part.lower() == "inbox" for part in relative.parts)
        ):
            raise ForbiddenPathError(f"denied HammerTime path: {relative!s}")
        return relative

    def _assert_descriptor_path(
        self,
        descriptor: int,
        expected: PurePosixPath | None,
    ) -> None:
        """Verify the opened descriptor is still at its expected rooted path."""
        proc_path = f"/proc/self/fd/{descriptor}"
        try:
            target_text = os.readlink(proc_path)
        except OSError:
            raise AdapterUnavailableError(
                "descriptor path verification is unavailable"
            ) from None
        if target_text.endswith(" (deleted)"):
            raise ForbiddenPathError("HammerTime descriptor target changed during read")
        target = Path(target_text).resolve(strict=False)
        try:
            relative = target.relative_to(self._root)
        except ValueError:
            raise ForbiddenPathError(
                "HammerTime descriptor escaped the configured root"
            ) from None
        observed = PurePosixPath(*relative.parts) if relative.parts else None
        if observed is not None and any(
            part.lower() == "inbox" for part in observed.parts
        ):
            raise ForbiddenPathError("HammerTime descriptor resolved inside Inbox")
        if observed != expected:
            raise ForbiddenPathError("HammerTime descriptor path changed during read")

    def _open_error(self, exc: OSError, relative: PurePosixPath) -> None:
        if exc.errno == errno.ELOOP:
            raise ForbiddenPathError(
                f"symlinked HammerTime path is forbidden: {relative}"
            ) from None
        if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            raise MissingPathError(f"missing HammerTime artifact: {relative}") from None
        if isinstance(exc, PermissionError):
            raise AdapterUnavailableError(
                f"HammerTime artifact is not readable: {relative}"
            ) from None
        raise AdapterUnavailableError(
            f"HammerTime artifact cannot be opened safely: {relative}"
        ) from None

    @contextmanager
    def _directory_descriptor(
        self,
        relative: PurePosixPath | None,
    ) -> Iterator[int]:
        """Open a rooted directory chain without following any symlink."""
        parts = () if relative is None else self._validate_relative(relative).parts
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            directory_flags |= os.O_CLOEXEC
        descriptors: list[int] = []
        try:
            try:
                current = os.open(self._root, directory_flags)
            except OSError as exc:
                self._open_error(exc, PurePosixPath("."))
                raise AssertionError("unreachable")
            descriptors.append(current)
            self._assert_descriptor_path(current, None)
            prefix: list[str] = []
            for part in parts:
                prefix.append(part)
                expected = PurePosixPath(*prefix)
                try:
                    current = os.open(part, directory_flags, dir_fd=current)
                except OSError as exc:
                    if isinstance(exc, NotADirectoryError):
                        try:
                            metadata = os.stat(
                                part,
                                dir_fd=current,
                                follow_symlinks=False,
                            )
                        except OSError:
                            metadata = None
                        if metadata is not None and stat.S_ISLNK(metadata.st_mode):
                            raise ForbiddenPathError(
                                f"symlinked HammerTime path is forbidden: {expected}"
                            ) from None
                    self._open_error(exc, expected)
                    raise AssertionError("unreachable")
                descriptors.append(current)
                self._assert_descriptor_path(current, expected)
            yield current
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _read_bytes(self, relative: PurePosixPath) -> tuple[bytes, str]:
        """Read one regular file with read-only flags and return its hash."""
        relative = self._validate_relative(relative)
        parent_parts = relative.parts[:-1]
        parent = PurePosixPath(*parent_parts) if parent_parts else None
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        with self._directory_descriptor(parent) as directory:
            try:
                descriptor = os.open(relative.name, file_flags, dir_fd=directory)
            except OSError as exc:
                self._open_error(exc, relative)
                raise AssertionError("unreachable")
            with os.fdopen(descriptor, "rb") as handle:
                self._assert_descriptor_path(handle.fileno(), relative)
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise MissingPathError(
                        f"HammerTime path is not a regular file: {relative}"
                    )
                if metadata.st_size > self._max_artifact_bytes:
                    raise ArtifactTooLargeError(
                        f"HammerTime artifact exceeds the read budget: {relative}"
                    )
                content = handle.read(self._max_artifact_bytes + 1)
        if len(content) > self._max_artifact_bytes:
            raise ArtifactTooLargeError(
                f"HammerTime artifact exceeds the read budget: {relative}"
            )
        return content, hashlib.sha256(content).hexdigest()

    def _list_directory_names(self, relative: PurePosixPath) -> list[str]:
        """List one fixed directory through its verified descriptor."""
        relative = self._validate_relative(relative)
        with self._directory_descriptor(relative) as descriptor:
            try:
                return sorted(os.listdir(descriptor))
            except OSError:
                raise AdapterUnavailableError(
                    f"HammerTime directory is not readable: {relative}"
                ) from None

    def _optional_read_bytes(self, relative: PurePosixPath) -> tuple[bytes, str] | None:
        try:
            return self._read_bytes(relative)
        except MissingPathError:
            return None

    def _read_json(self, relative: PurePosixPath) -> tuple[dict[str, Any], str]:
        content, digest = self._read_bytes(relative)
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MalformedManifestError(
                f"invalid JSON in HammerTime file: {relative}"
            ) from None
        if not isinstance(payload, dict):
            raise MalformedManifestError(
                f"HammerTime JSON file must hold an object: {relative}"
            )
        return payload, digest

    def _pin(
        self,
        relative: PurePosixPath,
        digest: str,
        *,
        release_id: str | None = None,
        alias_target: str | None = None,
    ) -> SnapshotPin:
        return SnapshotPin(
            relative_path=str(relative),
            content_sha256=digest,
            observed_at=self._clock(),
            release_id=release_id,
            alias_target=alias_target,
        )

    def _authorize_matter(
        self,
        legacy_id: str,
        record_kind: LegacyRecordKind,
        parent_legacy_id: str | None,
        relative_path: str | None,
    ) -> None:
        """Fail-closed matter gate for every matter-scoped read."""
        if self._matter_authorizer is None:
            raise MatterAccessDenied(
                f"no matter authorizer configured; denied {legacy_id}"
            )
        request = MatterAccessRequest(
            legacy_id=legacy_id,
            record_kind=record_kind,
            parent_legacy_id=parent_legacy_id,
            relative_path=relative_path,
        )
        try:
            allowed = self._matter_authorizer(request)
        except Exception:
            raise MatterAccessDenied(
                f"matter authorizer failed closed for {legacy_id}"
            ) from None
        if allowed is not True:
            raise MatterAccessDenied(f"matter authorizer denied {legacy_id}")

    # ------------------------------------------------------------------
    # release manifests and runtime aliases
    # ------------------------------------------------------------------

    def list_releases(self) -> list[ReleaseSummary]:
        """List every pinned release manifest under ``json/releases/``."""
        summaries: list[ReleaseSummary] = []
        for name in self._list_directory_names(_RELEASES_DIR):
            if not name.startswith(_RELEASE_PREFIX) or not name.endswith(
                _RELEASE_SUFFIX
            ):
                continue
            release_id = name[len(_RELEASE_PREFIX) : -len(_RELEASE_SUFFIX)]
            if _RELEASE_ID_PATTERN.fullmatch(release_id) is None:
                continue
            relative = _RELEASES_DIR / name
            payload, digest = self._read_json(relative)
            if payload.get("release_id") != release_id:
                raise MalformedManifestError(
                    f"release manifest identity mismatch: {relative}"
                )
            summaries.append(
                ReleaseSummary(
                    release_id=release_id,
                    release_target=_optional_str(payload, "release_target", relative),
                    generated_at=_optional_str(payload, "generated_at", relative),
                    pin=self._pin(relative, digest, release_id=release_id),
                )
            )
        return summaries

    def get_release_manifest(self, release_id: str) -> ReleaseManifest:
        """Read and pin one exact release manifest by release id."""
        if _RELEASE_ID_PATTERN.fullmatch(release_id) is None:
            raise ValueError(f"invalid release id: {release_id!r}")
        relative = _RELEASES_DIR / f"{_RELEASE_PREFIX}{release_id}{_RELEASE_SUFFIX}"
        payload, digest = self._read_json(relative)
        return self._manifest_from_payload(release_id, relative, payload, digest)

    def _manifest_from_payload(
        self,
        release_id: str,
        relative: PurePosixPath,
        payload: dict[str, Any],
        digest: str,
        *,
        alias_target: str | None = None,
    ) -> ReleaseManifest:
        for key in _MANIFEST_REQUIRED_KEYS:
            if key not in payload:
                raise MalformedManifestError(
                    f"release manifest lacks {key!r}: {relative}"
                )
        if payload["release_id"] != release_id:
            raise MalformedManifestError(
                f"release manifest identity mismatch: {relative}"
            )
        counts = payload["document_counts"]
        documents = payload["documents"]
        if not isinstance(counts, dict) or not isinstance(documents, dict):
            raise MalformedManifestError(
                f"release manifest document sections must be objects: {relative}"
            )
        try:
            return ReleaseManifest(
                pin=self._pin(
                    relative, digest, release_id=release_id, alias_target=alias_target
                ),
                release_id=payload["release_id"],
                release_target=payload["release_target"],
                schema_version=payload["schema_version"],
                vector_collection=payload["vector_collection"],
                graph_name=payload["graph_name"],
                mode=_optional_str(payload, "mode", relative),
                generated_at=_optional_str(payload, "generated_at", relative),
                source_commit=(
                    payload["source_commit"]
                    if isinstance(payload.get("source_commit"), str)
                    else None
                ),
                document_counts=DocumentCounts(
                    new=_count_field(counts, "new", relative),
                    changed=_count_field(counts, "changed", relative),
                    deleted=_count_field(counts, "deleted", relative),
                    unchanged=_count_field(counts, "unchanged", relative),
                ),
                documents={
                    str(key): [str(item) for item in value]
                    for key, value in documents.items()
                    if isinstance(value, list)
                },
                verification=mapping_of(payload["verification"])
                if isinstance(payload["verification"], dict)
                else {},
                decomposed_snapshot=(
                    mapping_of(payload["decomposed_snapshot"])
                    if isinstance(payload.get("decomposed_snapshot"), dict)
                    else None
                ),
                raw=payload,
            )
        except (TypeError, ValueError) as exc:
            raise MalformedManifestError(
                f"release manifest violates the read contract: {relative}: {exc}"
            ) from None

    def get_runtime_aliases(self) -> RuntimeAliasesSnapshot:
        """Read and pin the mutable runtime alias state file."""
        payload, digest = self._read_json(_ALIASES_PATH)
        raw_aliases = payload.get("aliases")
        schema_version = payload.get("schema_version")
        if not isinstance(raw_aliases, dict) or not isinstance(schema_version, int):
            raise MalformedManifestError(
                f"runtime alias state violates the read contract: {_ALIASES_PATH}"
            )
        aliases: dict[str, RuntimeAlias] = {}
        for target, binding in raw_aliases.items():
            if _ALIAS_TARGET_PATTERN.fullmatch(str(target)) is None or not isinstance(
                binding, dict
            ):
                raise MalformedManifestError(
                    f"runtime alias entry violates the read contract: {target!r}"
                )
            current = _binding_from_payload(binding.get("current"), target)
            previous_raw = binding.get("previous")
            previous = (
                _binding_from_payload(previous_raw, target)
                if isinstance(previous_raw, dict)
                else None
            )
            aliases[str(target)] = RuntimeAlias(
                target=str(target), current=current, previous=previous
            )
        try:
            return RuntimeAliasesSnapshot(
                pin=self._pin(_ALIASES_PATH, digest),
                schema_version=schema_version,
                updated_at=_optional_str(payload, "updated_at", _ALIASES_PATH),
                aliases=aliases,
            )
        except (TypeError, ValueError) as exc:
            raise MalformedManifestError(
                f"runtime alias state violates the read contract: {exc}"
            ) from None

    def resolve_current_release(self, target: str) -> ResolvedRelease:
        """Resolve the release an alias currently points at, with drift kept.

        Raises StaleReleaseError when the alias is unknown, when the pinned
        manifest is missing, or when the manifest identity disagrees with
        the alias binding. Non-fatal binding drift is reported verbatim in
        ``ResolvedRelease.drift`` and never silently harmonized.
        """
        if _ALIAS_TARGET_PATTERN.fullmatch(target) is None:
            raise ValueError(f"invalid alias target: {target!r}")
        snapshot = self.get_runtime_aliases()
        alias = snapshot.aliases.get(target)
        if alias is None:
            raise StaleReleaseError(f"no runtime alias binding for target {target!r}")
        binding = alias.current
        relative = _RELEASES_DIR / (
            f"{_RELEASE_PREFIX}{binding.release_id}{_RELEASE_SUFFIX}"
        )
        try:
            payload, digest = self._read_json(relative)
        except MissingPathError:
            raise StaleReleaseError(
                f"alias {target!r} points at missing release {binding.release_id!r}"
            ) from None
        if payload.get("release_id") != binding.release_id:
            raise StaleReleaseError(
                f"alias {target!r} release identity mismatch for {binding.release_id!r}"
            )
        manifest = self._manifest_from_payload(
            binding.release_id, relative, payload, digest, alias_target=target
        )
        drift: list[str] = []
        expected_name = f"{_RELEASE_PREFIX}{binding.release_id}{_RELEASE_SUFFIX}"
        if PurePosixPath(binding.manifest_path).name != expected_name:
            drift.append(
                f"alias manifest_path basename {binding.manifest_path!r} does not "
                f"name the pinned manifest {expected_name!r}"
            )
        if (
            binding.vector_collection is not None
            and binding.vector_collection != manifest.vector_collection
        ):
            drift.append(
                f"alias vector collection {binding.vector_collection!r} differs "
                f"from manifest {manifest.vector_collection!r}"
            )
        if binding.graph_name is not None and binding.graph_name != manifest.graph_name:
            drift.append(
                f"alias graph name {binding.graph_name!r} differs from manifest "
                f"{manifest.graph_name!r}"
            )
        return ResolvedRelease(
            aliases_pin=snapshot.pin,
            alias=alias,
            manifest=manifest,
            drift=drift,
        )

    # ------------------------------------------------------------------
    # decompositions
    # ------------------------------------------------------------------

    def get_decomposed_state(self) -> DecomposedStateSeal:
        """Read and pin the sealed decomposed-artifact state snapshot."""
        payload, digest = self._read_json(_DECOMPOSED_STATE)
        release_id = payload.get("release_id")
        snapshot = payload.get("snapshot")
        if not isinstance(release_id, str) or not isinstance(snapshot, dict):
            raise MalformedManifestError(
                f"decomposed state violates the read contract: {_DECOMPOSED_STATE}"
            )
        return DecomposedStateSeal(
            pin=self._pin(_DECOMPOSED_STATE, digest),
            release_id=release_id,
            release_target=_optional_str(payload, "release_target", _DECOMPOSED_STATE),
            schema_version=(
                str(payload["schema_version"])
                if payload.get("schema_version") is not None
                else None
            ),
            sealed_at=_optional_str(payload, "sealed_at", _DECOMPOSED_STATE),
            snapshot=mapping_of(snapshot),
            raw=payload,
        )

    def get_decomposition(
        self,
        document_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> Decomposition:
        """Read and pin one decomposed document artifact.

        When ``expected_sha256`` is supplied, a changed artifact raises
        SourceHashMismatchError instead of returning unpinned content.
        """
        if _DECOMPOSITION_ID_PATTERN.fullmatch(document_id) is None:
            raise ValueError(f"invalid decomposition document id: {document_id!r}")
        relative = _DECOMPOSED_DIR / f"{document_id}.json"
        payload, digest = self._read_json(relative)
        if expected_sha256 is not None and digest != expected_sha256:
            raise SourceHashMismatchError(
                f"decomposition hash changed for {document_id!r}: "
                f"expected {expected_sha256}, observed {digest}"
            )
        try:
            return Decomposition(
                pin=self._pin(relative, digest),
                document_id=document_id,
                source_file=payload["source_file"],
                decomposed_at=_optional_str(payload, "decomposed_at", relative),
                frontmatter=mapping_of(payload.get("frontmatter") or {}),
                stats={
                    str(key): int(value)
                    for key, value in (payload.get("stats") or {}).items()
                },
                chunks=[
                    DecompositionChunk.model_validate(item)
                    for item in payload.get("chunks") or []
                ],
                claims=[
                    DecompositionClaim.model_validate(item)
                    for item in payload.get("claims") or []
                ],
                citations=[mapping_of(item) for item in payload.get("citations") or []],
                entities=[
                    DecompositionEntity.model_validate(item)
                    for item in payload.get("entities") or []
                ],
                relationships=[
                    mapping_of(item) for item in payload.get("relationships") or []
                ],
                raw=payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedManifestError(
                f"decomposition violates the read contract: {relative}: {exc}"
            ) from None

    # ------------------------------------------------------------------
    # bounded artifact reads and source hashes
    # ------------------------------------------------------------------

    def read_artifact(
        self,
        relative_path: str,
        *,
        expected_sha256: str | None = None,
    ) -> ArtifactRead:
        """Read exact bytes from one allowlisted corpus artifact.

        Only corpus and reference subtrees are readable here. Legacy matter
        content under ``incidents/`` requires the gated matter APIs, and
        ``Inbox/`` is denied in every casing.
        """
        pure = self._resolve(relative_path)
        if pure.parts[0].lower() not in _ARTIFACT_ROOTS:
            raise ForbiddenPathError(
                f"artifact reads are bounded to corpus subtrees: {relative_path!r}"
            )
        content, digest = self._read_bytes(pure)
        if expected_sha256 is not None and digest != expected_sha256:
            raise SourceHashMismatchError(
                f"artifact hash changed for {relative_path!r}: "
                f"expected {expected_sha256}, observed {digest}"
            )
        return ArtifactRead(pin=self._pin(pure, digest), content=content)

    def verify_source_hash(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
    ) -> SourceHashVerification:
        """Prove that an artifact still matches its pinned source hash."""
        read = self.read_artifact(relative_path)
        if read.pin.content_sha256 != expected_sha256:
            raise SourceHashMismatchError(
                f"source hash changed for {relative_path!r}: "
                f"expected {expected_sha256}, observed {read.pin.content_sha256}"
            )
        return SourceHashVerification(pin=read.pin, expected_sha256=expected_sha256)

    # ------------------------------------------------------------------
    # legacy matter structure (matter-authorizer gated)
    # ------------------------------------------------------------------

    def legacy_matter_path(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None = None,
    ) -> LegacyPathResolution:
        """Return an authorized, typed, snapshot-pinned legacy path."""
        read = self._read_authorized_legacy(
            legacy_id,
            parent_legacy_id=parent_legacy_id,
        )
        return self._path_resolution(legacy_id, read)

    def resolve_legacy_matter(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None = None,
    ) -> LegacyMatterRecord:
        """Read and pin one legacy matter record after an allow decision."""
        read = self._read_authorized_legacy(
            legacy_id,
            parent_legacy_id=parent_legacy_id,
        )
        relative = read.relative
        kind = read.record_kind
        try:
            text = read.content.decode("utf-8")
        except UnicodeDecodeError:
            raise MalformedFrontmatterError(
                f"legacy matter record is not UTF-8 text: {relative}"
            ) from None
        frontmatter, body = split_frontmatter(text, relative_path=str(relative))
        observed_id = frontmatter.get(
            "problem_id" if kind is LegacyRecordKind.CONTAINER else "incident_id"
        )
        if observed_id != legacy_id:
            raise RegistryMismatchError(
                f"legacy id {legacy_id!r} resolved to {relative} whose "
                f"frontmatter declares {observed_id!r}"
            )
        slug = frontmatter.get("slug")
        if not isinstance(slug, str) or not slug:
            if kind is LegacyRecordKind.CONTAINER:
                slug = relative.parts[2]
            else:
                slug = relative.parts[4]
        parent_id = frontmatter.get("problem_id")
        if kind is LegacyRecordKind.ACTIVITY and parent_id != read.parent_legacy_id:
            raise RegistryMismatchError(
                f"legacy activity {legacy_id!r} resolved under "
                f"{read.parent_legacy_id!r} but declares parent {parent_id!r}"
            )
        try:
            return LegacyMatterRecord(
                pin=self._pin(relative, read.digest),
                registry_pin=read.registry_pin,
                record_kind=kind,
                legacy_id=legacy_id,
                slug=str(slug),
                relative_path=str(relative),
                parent_legacy_id=(
                    str(parent_id)
                    if kind is LegacyRecordKind.ACTIVITY and isinstance(parent_id, str)
                    else None
                ),
                frontmatter=frontmatter,
                body=body,
            )
        except (TypeError, ValueError) as exc:
            raise MalformedFrontmatterError(
                f"legacy matter record violates the read contract: {relative}: {exc}"
            ) from None

    def get_matter_validation_report(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None = None,
    ) -> MatterValidationReport:
        """Read and pin the HammerTime validation result for one record."""
        incident_dir = self._incident_dir(legacy_id, parent_legacy_id)
        relative = incident_dir / "validation-report.json"
        payload, digest = self._read_json(relative)
        known = {"validated_date", "incident_id", "valid", "errors", "notices"}
        try:
            return MatterValidationReport(
                pin=self._pin(relative, digest),
                legacy_id=legacy_id,
                validated_date=_optional_str(payload, "validated_date", relative),
                valid=payload.get("valid")
                if isinstance(payload.get("valid"), bool)
                else None,
                errors=list(payload.get("errors") or []),
                notices=list(payload.get("notices") or []),
                sections={
                    key: value for key, value in payload.items() if key not in known
                },
                raw=payload,
            )
        except (TypeError, ValueError) as exc:
            raise MalformedManifestError(
                f"validation report violates the read contract: {relative}: {exc}"
            ) from None

    def list_packet_references(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None = None,
    ) -> list[PacketReference]:
        """List pinned versioned packet facts payloads for one record."""
        incident_dir = self._incident_dir(legacy_id, parent_legacy_id)
        references: list[PacketReference] = []
        for name in self._list_directory_names(incident_dir):
            match = _PACKET_FACTS_PATTERN.fullmatch(name)
            if match is None:
                continue
            facts_relative = incident_dir / name
            payload, digest = self._read_json(facts_relative)
            review_relative = (
                incident_dir / f"phase-0-wave-1-v{match.group(1)}-review.md"
            )
            review_read = self._optional_read_bytes(review_relative)
            review_pin = (
                self._pin(review_relative, review_read[1])
                if review_read is not None
                else None
            )
            references.append(
                PacketReference(
                    packet_version=int(match.group(1)),
                    facts_pin=self._pin(facts_relative, digest),
                    facts=payload,
                    review_pin=review_pin,
                )
            )
        return references

    def get_owner_directions(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None = None,
    ) -> ArtifactRead:
        """Read and pin the owner-directions record for one matter activity."""
        incident_dir = self._incident_dir(legacy_id, parent_legacy_id)
        relative = incident_dir / "correspondence" / "OWNER-DIRECTIONS.md"
        content, digest = self._read_bytes(relative)
        return ArtifactRead(pin=self._pin(relative, digest), content=content)

    def read_matter_artifact(
        self,
        legacy_id: str,
        relative_path: str,
        *,
        parent_legacy_id: str | None = None,
        expected_sha256: str | None = None,
    ) -> ArtifactRead:
        """Read exact bytes from one file inside an authorized matter tree.

        Unlike ``read_artifact`` this API reaches matter-scoped content, so
        it requires the same allow decision as the matter record itself and
        refuses any path that escapes the resolved matter directory. When
        ``expected_sha256`` is supplied, a changed artifact raises
        SourceHashMismatchError instead of returning unpinned content.
        """
        read = self._read_authorized_legacy(
            legacy_id,
            parent_legacy_id=parent_legacy_id,
        )
        matter_root = read.relative.parent
        if (
            not relative_path
            or relative_path != relative_path.strip()
            or "\\" in relative_path
            or "\x00" in relative_path
        ):
            raise ForbiddenPathError(
                f"denied non-normalized matter artifact path: {relative_path!r}"
            )
        pure = self._validate_relative(PurePosixPath(relative_path))
        if (
            pure.is_absolute()
            or len(pure.parts) <= len(matter_root.parts)
            or pure.parts[: len(matter_root.parts)] != matter_root.parts
        ):
            raise ForbiddenPathError(
                f"matter artifact reads are bounded to {matter_root}: {relative_path!r}"
            )
        content, digest = self._read_bytes(pure)
        if expected_sha256 is not None and digest != expected_sha256:
            raise SourceHashMismatchError(
                f"matter artifact hash changed for {relative_path!r}: "
                f"expected {expected_sha256}, observed {digest}"
            )
        return ArtifactRead(pin=self._pin(pure, digest), content=content)

    def list_matter_artifacts(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None = None,
    ) -> MatterArtifactInventory:
        """Recursively pin every regular file under one authorized matter tree.

        Every regular file is hashed exactly once with read-only flags and
        returned with its byte count and source modification time. Entries
        that cannot be hashed losslessly are never followed and are listed
        verbatim in ``skipped``. ``Inbox`` components are denied in any
        casing, consistent with the rest of the adapter.
        """
        read = self._read_authorized_legacy(
            legacy_id,
            parent_legacy_id=parent_legacy_id,
        )
        matter_root = read.relative.parent
        artifacts: list[MatterArtifact] = []
        skipped: list[str] = []
        stack = [matter_root]
        while stack:
            current = stack.pop()
            with self._directory_descriptor(current) as descriptor:
                entries: list[tuple[str, os.stat_result]] = []
                for name in sorted(os.listdir(descriptor)):
                    try:
                        entries.append(
                            (
                                name,
                                os.stat(name, dir_fd=descriptor, follow_symlinks=False),
                            )
                        )
                    except OSError:
                        skipped.append(f"{current / name}:unreadable")
            for name, metadata in entries:
                relative = current / name
                if stat.S_ISLNK(metadata.st_mode):
                    skipped.append(f"{relative}:symlink")
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if name.lower() == "inbox":
                        skipped.append(f"{relative}:forbidden-inbox")
                        continue
                    stack.append(relative)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    skipped.append(f"{relative}:special-file")
                    continue
                if metadata.st_size > self._max_artifact_bytes:
                    skipped.append(f"{relative}:exceeds-read-budget")
                    continue
                content, digest = self._read_bytes(relative)
                artifacts.append(
                    MatterArtifact(
                        pin=self._pin(relative, digest),
                        byte_count=len(content),
                        modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC),
                    )
                )
        return MatterArtifactInventory(
            matter_root=str(matter_root),
            artifacts=sorted(artifacts, key=lambda item: item.pin.relative_path),
            skipped=sorted(skipped),
        )

    # ------------------------------------------------------------------
    # legacy path resolution internals (index reads only, no content)
    # ------------------------------------------------------------------

    def _require_legacy_id(self, legacy_id: str) -> None:
        if _LEGACY_ID_PATTERN.fullmatch(legacy_id) is None:
            raise ValueError(f"invalid legacy record id: {legacy_id!r}")

    def _registry_links(self) -> tuple[dict[str, list[str]], SnapshotPin]:
        """Map legacy ids to paths and pin the exact registry snapshot."""
        content, digest = self._read_bytes(_REGISTRY_PATH)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise MalformedManifestError(
                f"incident registry is not UTF-8 text: {_REGISTRY_PATH}"
            ) from None
        links: dict[str, list[str]] = {}
        for line in text.splitlines():
            ids = _REGISTRY_ID_PATTERN.findall(line)
            paths = _REGISTRY_LINK_PATTERN.findall(line)
            if not ids or not paths:
                continue
            for legacy_id in ids:
                links.setdefault(legacy_id, []).extend(paths)
        return links, self._pin(_REGISTRY_PATH, digest)

    def _resolve_problem_path(
        self, legacy_id: str
    ) -> tuple[PurePosixPath, SnapshotPin]:
        links, registry_pin = self._registry_links()
        candidates = sorted(
            {path for path in links.get(legacy_id, []) if path.endswith("/PROBLEM.md")}
        )
        if not candidates:
            raise MissingPathError(
                f"no registry path for legacy matter container {legacy_id!r}"
            )
        if len(candidates) > 1:
            raise AmbiguousLegacyIdError(
                f"legacy id {legacy_id!r} maps to multiple registry paths"
            )
        relative = self._validate_relative(_PROBLEMS_DIR.parent / candidates[0])
        return relative, registry_pin

    def _resolve_incident_path(
        self, legacy_id: str, problem_path: PurePosixPath
    ) -> PurePosixPath:
        problem_dir = problem_path.parent
        incidents_dir = problem_dir / "incidents"
        candidates: list[PurePosixPath] = []
        for name in self._list_directory_names(incidents_dir):
            if not name.startswith(f"{legacy_id}-"):
                continue
            candidate_dir = incidents_dir / name
            try:
                names = self._list_directory_names(candidate_dir)
            except MissingPathError:
                continue
            if "INCIDENT.md" in names:
                candidates.append(candidate_dir / "INCIDENT.md")
        if not candidates:
            raise MissingPathError(
                f"no incident record for {legacy_id!r} under {problem_dir}"
            )
        if len(candidates) > 1:
            raise AmbiguousLegacyIdError(
                f"legacy id {legacy_id!r} maps to multiple incident records"
            )
        return candidates[0]

    def _read_authorized_legacy(
        self,
        legacy_id: str,
        *,
        parent_legacy_id: str | None,
    ) -> _LegacyRead:
        """Authorize before discovery, recheck exact path, then read once."""
        self._require_legacy_id(legacy_id)
        kind = _record_kind(legacy_id)
        if kind is LegacyRecordKind.ACTIVITY:
            if parent_legacy_id is None:
                raise AmbiguousLegacyIdError(
                    f"legacy activity id {legacy_id!r} requires parent_legacy_id"
                )
            self._require_legacy_id(parent_legacy_id)
            if _record_kind(parent_legacy_id) is not LegacyRecordKind.CONTAINER:
                raise ValueError(
                    "parent_legacy_id must be a matter container id: "
                    f"{parent_legacy_id!r}"
                )
        elif parent_legacy_id is not None:
            raise ValueError("parent_legacy_id is only valid for legacy activities")

        self._authorize_matter(legacy_id, kind, parent_legacy_id, None)
        if kind is LegacyRecordKind.CONTAINER:
            relative, registry_pin = self._resolve_problem_path(legacy_id)
        else:
            assert parent_legacy_id is not None
            problem_path, registry_pin = self._resolve_problem_path(parent_legacy_id)
            relative = self._resolve_incident_path(legacy_id, problem_path)
        self._authorize_matter(
            legacy_id,
            kind,
            parent_legacy_id,
            str(relative),
        )
        content, digest = self._read_bytes(relative)
        return _LegacyRead(
            relative=relative,
            record_kind=kind,
            parent_legacy_id=parent_legacy_id,
            content=content,
            digest=digest,
            registry_pin=registry_pin,
        )

    def _path_resolution(
        self,
        legacy_id: str,
        read: _LegacyRead,
    ) -> LegacyPathResolution:
        return LegacyPathResolution(
            pin=self._pin(read.relative, read.digest),
            registry_pin=read.registry_pin,
            record_kind=read.record_kind,
            legacy_id=legacy_id,
            relative_path=str(read.relative),
            parent_legacy_id=read.parent_legacy_id,
        )

    def _incident_dir(
        self,
        legacy_id: str,
        parent_legacy_id: str | None,
    ) -> PurePosixPath:
        self._require_legacy_id(legacy_id)
        if _record_kind(legacy_id) is not LegacyRecordKind.ACTIVITY:
            raise ValueError(
                f"expected a legacy activity id (INC-*), got {legacy_id!r}"
            )
        read = self._read_authorized_legacy(
            legacy_id,
            parent_legacy_id=parent_legacy_id,
        )
        return read.relative.parent


def _optional_str(
    payload: dict[str, Any], key: str, relative: PurePosixPath
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedManifestError(f"field {key!r} must be a string in {relative}")
    return value


def _count_field(payload: dict[str, Any], key: str, relative: PurePosixPath) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedManifestError(
            f"document_counts field {key!r} must be an integer in {relative}"
        )
    return value


def _binding_from_payload(payload: Any, target: str) -> AliasBinding:
    if not isinstance(payload, dict):
        raise MalformedManifestError(
            f"runtime alias {target!r} lacks a current binding"
        )
    release_id = payload.get("release_id")
    manifest_path = payload.get("manifest_path")
    if not isinstance(release_id, str) or not isinstance(manifest_path, str):
        raise MalformedManifestError(
            f"runtime alias {target!r} binding violates the read contract"
        )
    try:
        return AliasBinding(
            release_id=release_id,
            manifest_path=manifest_path,
            promoted_at=(
                payload["promoted_at"]
                if isinstance(payload.get("promoted_at"), str)
                else None
            ),
            vector_collection=(
                payload["vector_collection"]
                if isinstance(payload.get("vector_collection"), str)
                else None
            ),
            graph_name=(
                payload["graph_name"]
                if isinstance(payload.get("graph_name"), str)
                else None
            ),
        )
    except (TypeError, ValueError) as exc:
        raise MalformedManifestError(
            f"runtime alias {target!r} binding violates the read contract: {exc}"
        ) from None
