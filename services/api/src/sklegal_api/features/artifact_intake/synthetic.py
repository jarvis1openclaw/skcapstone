"""Byte-only synthetic adapter for scanner and derived-artifact fixtures.

The adapter accepts bytes already supplied to the API. It has no filesystem
or connector methods and cannot enumerate or open a HammerTime path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock
from typing import Literal, Protocol, cast

from .contracts import DerivationRequest, OriginalArtifactInput, ScanState


class ArtifactAdapterUnavailable(RuntimeError):
    """The bounded scanner or derivation adapter is unavailable."""


@dataclass(frozen=True, slots=True)
class DerivedArtifactMaterial:
    kind: Literal["text_extraction", "ocr", "transcript"]
    filename_suffix: str
    media_type: str
    content: bytes
    content_sha256: str
    tool_name: str
    tool_version: str


@dataclass(frozen=True, slots=True)
class ArtifactInspection:
    scan_state: ScanState
    scanner_name: str
    scanner_version: str
    signature_revision: str
    derived: tuple[DerivedArtifactMaterial, ...]


@dataclass(frozen=True, slots=True)
class ArtifactStorageReceipt:
    storage_locator: str
    content_sha256: str
    byte_count: int


class ArtifactAdapter(Protocol):
    def inspect(
        self,
        original: OriginalArtifactInput,
        derivations: tuple[DerivationRequest, ...],
    ) -> ArtifactInspection: ...

    def preserve_original(
        self, original: OriginalArtifactInput
    ) -> ArtifactStorageReceipt: ...

    def preserve_derived(
        self, derived: DerivedArtifactMaterial
    ) -> ArtifactStorageReceipt: ...


class SyntheticArtifactAdapter:
    """Deterministic public-data adapter with no path or provider access."""

    filesystem_access = False
    provider_access = False
    signature_revision = hashlib.sha256(b"sklegal-synthetic-scan-v1").hexdigest()

    def __init__(self, *, scan_state: str = "clean") -> None:
        if scan_state not in {"clean", "pending", "unsafe", "failed"}:
            raise ValueError("scan state is outside the closed contract")
        self._scan_state = cast(ScanState, scan_state)
        self._lock = RLock()
        self._objects: dict[str, bytes] = {}

    def _preserve(self, content: bytes, content_sha256: str) -> ArtifactStorageReceipt:
        if hashlib.sha256(content).hexdigest() != content_sha256:
            raise ArtifactAdapterUnavailable("synthetic content hash mismatch")
        locator = f"synthetic:sha256:{content_sha256}"
        with self._lock:
            existing = self._objects.get(locator)
            if existing is not None and existing != content:
                raise ArtifactAdapterUnavailable("immutable synthetic object conflict")
            self._objects[locator] = bytes(content)
        return ArtifactStorageReceipt(
            storage_locator=locator,
            content_sha256=content_sha256,
            byte_count=len(content),
        )

    def preserve_original(
        self, original: OriginalArtifactInput
    ) -> ArtifactStorageReceipt:
        return self._preserve(original.decoded_bytes(), original.content_sha256)

    def preserve_derived(
        self, derived: DerivedArtifactMaterial
    ) -> ArtifactStorageReceipt:
        return self._preserve(derived.content, derived.content_sha256)

    def bytes_at(self, storage_locator: str) -> bytes | None:
        """Return a defensive copy from the public-synthetic object store."""

        with self._lock:
            content = self._objects.get(storage_locator)
            return None if content is None else bytes(content)

    def inspect(
        self,
        original: OriginalArtifactInput,
        derivations: tuple[DerivationRequest, ...],
    ) -> ArtifactInspection:
        if self._scan_state == "failed":
            return ArtifactInspection(
                scan_state="failed",
                scanner_name="synthetic-scanner",
                scanner_version="1.0.0",
                signature_revision=self.signature_revision,
                derived=(),
            )
        if self._scan_state != "clean":
            return ArtifactInspection(
                scan_state=self._scan_state,
                scanner_name="synthetic-scanner",
                scanner_version="1.0.0",
                signature_revision=self.signature_revision,
                derived=(),
            )
        source_sha256 = original.content_sha256
        outputs: list[DerivedArtifactMaterial] = []
        for request in derivations:
            content = (
                "PUBLIC SYNTHETIC DERIVATION\n"
                f"kind={request.kind}\n"
                f"source_sha256={source_sha256}\n"
            ).encode()
            outputs.append(
                DerivedArtifactMaterial(
                    kind=request.kind,
                    filename_suffix=f".{request.kind}.txt",
                    media_type=request.output_media_type,
                    content=content,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    tool_name=request.tool_name,
                    tool_version=request.tool_version,
                )
            )
        return ArtifactInspection(
            scan_state="clean",
            scanner_name="synthetic-scanner",
            scanner_version="1.0.0",
            signature_revision=self.signature_revision,
            derived=tuple(outputs),
        )
