"""Simulation-first, HammerTime-owned ingestion bridge."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .errors import CompletionEvidenceMissingError, PromotionRejectedError, UnsupportedSubmissionError

ALLOWED_SUFFIXES = frozenset({".md", ".markdown", ".json", ".txt", ".yaml", ".yml"})


class HammerTimeIntakeGateway(Protocol):
    def submit(self, *, idempotency_key: str, manifest: Mapping[str, Any]) -> str: ...
    def poll(self, submission_id: str) -> Mapping[str, Any]: ...
    def promote(self, submission_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SourceEntry:
    relative_path: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class SubmissionPlan:
    batch_id: str
    idempotency_key: str
    destination: str
    sources: tuple[SourceEntry, ...]
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class SubmissionResult:
    batch_id: str
    status: Literal["dry_run", "receipt_verified"]
    artifact_reference: str | None
    completion_evidence: Mapping[str, Any] | None
    release_reference: str | None


def _validate_destination(destination: str) -> str:
    normalized = destination.strip().strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise ValueError("destination must be a normalized relative path")
    if any(part.lower() == "inbox" for part in normalized.split("/")):
        raise ValueError("direct Inbox destinations are forbidden")
    return normalized


class HammerTimeSubmissionBridge:
    """Prepare and dispatch an intake batch through the HammerTime gateway."""

    def __init__(self, *, gateway: HammerTimeIntakeGateway, fixture_root: str | Path):
        self._gateway = gateway
        self._root = Path(fixture_root).resolve()
        if not self._root.is_dir():
            raise ValueError("fixture_root must be an existing directory")

    def plan(self, *, source_paths: Iterable[str | Path], destination: str) -> SubmissionPlan:
        destination = _validate_destination(destination)
        entries: list[SourceEntry] = []
        for supplied in source_paths:
            path = (self._root / supplied).resolve()
            if self._root not in path.parents or not path.is_file():
                raise ValueError("source must be a regular file under fixture_root")
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                raise UnsupportedSubmissionError(path.name)
            data = path.read_bytes()
            entries.append(SourceEntry(path.relative_to(self._root).as_posix(), hashlib.sha256(data).hexdigest(), len(data)))
        if not entries:
            raise ValueError("at least one source is required")
        entries.sort(key=lambda item: item.relative_path)
        payload = {"destination": destination, "sources": [entry.__dict__ for entry in entries]}
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        batch_id = f"sklegal-{key[:20]}"
        manifest = {"schema_version": 1, "batch_id": batch_id, "idempotency_key": key, **payload}
        return SubmissionPlan(batch_id, key, destination, tuple(entries), manifest)

    def submit(self, plan: SubmissionPlan, *, dry_run: bool = True) -> SubmissionResult:
        if dry_run:
            return SubmissionResult(plan.batch_id, "dry_run", None, None, None)
        submission_id = self._gateway.submit(idempotency_key=plan.idempotency_key, manifest=plan.manifest)
        completion = self._gateway.poll(submission_id)
        if completion.get("status") != "completed" or not completion.get("completion_evidence"):
            raise CompletionEvidenceMissingError(plan.batch_id)
        if completion.get("qc") != "accepted":
            raise PromotionRejectedError(plan.batch_id)
        promoted = self._gateway.promote(submission_id)
        reference, release = promoted.get("artifact_reference"), promoted.get("release_reference")
        if not reference or not release:
            raise PromotionRejectedError("promotion did not return artifact and release references")
        return SubmissionResult(plan.batch_id, "receipt_verified", reference, completion["completion_evidence"], release)
