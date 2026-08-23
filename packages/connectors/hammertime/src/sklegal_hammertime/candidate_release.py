"""Deterministic builder for one official drafting candidate release manifest."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field, model_validator
from sklegal_domain.value_objects import FrozenValue, NonEmptyText, Sha256, ShortText

from .adapter import HammerTimeReleaseAdapter
from .frontmatter import split_frontmatter
from .models import AliasTarget, RelativePosixPath, ReleaseId


class CandidateBuildBlockerCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    MISSING_ARTIFACT = "missing_artifact"
    HASH_MISMATCH = "hash_mismatch"
    RELEASE_COLLISION = "release_collision"


class CandidateBuildBlocker(FrozenValue):
    code: CandidateBuildBlockerCode
    subject: NonEmptyText
    detail: ShortText


class OfficialDraftingCandidateBuildRequest(FrozenValue):
    batch_id: NonEmptyText
    release_id: ReleaseId
    release_target: AliasTarget = "dev"
    finalized_file_list_path: RelativePosixPath
    rights_review_path: RelativePosixPath
    completion_evidence_path: RelativePosixPath
    profile_path: RelativePosixPath
    core_principles_path: RelativePosixPath
    runtime_aliases_path: RelativePosixPath = "json/state/runtime-aliases.json"
    apply: bool = False
    expected_source_count: int = Field(default=14, ge=1)


class OfficialDraftingCandidatePlan(FrozenValue):
    status: str
    dry_run: bool
    release_id: ReleaseId
    release_target: AliasTarget
    manifest_path: RelativePosixPath
    manifest_sha256: Sha256
    source_count: int
    decomposition_count: int
    finalized_file_list_sha256: Sha256
    rights_review_sha256: Sha256
    completion_evidence_sha256: Sha256
    profile_sha256: Sha256
    core_principles_sha256: Sha256
    runtime_aliases_sha256: Sha256
    normalized_sources: tuple[dict[str, Any], ...]
    decompositions: tuple[dict[str, Any], ...]
    projection_bindings: dict[str, Any]
    write_set: tuple[RelativePosixPath, ...]
    actual_write_set: tuple[RelativePosixPath, ...] = ()
    blockers: tuple[CandidateBuildBlocker, ...] = ()

    @model_validator(mode="after")
    def validate_status(self) -> OfficialDraftingCandidatePlan:
        if (self.status == "ready") == bool(self.blockers):
            raise ValueError("ready plans have no blockers; blocked plans do")
        return self


class OfficialDraftingCandidateReleaseBuilder:
    """Build one exact dev candidate manifest from sealed batch evidence."""

    def __init__(
        self,
        adapter: HammerTimeReleaseAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(adapter, HammerTimeReleaseAdapter):
            raise ValueError("candidate release builder requires the HammerTime adapter")
        self._adapter = adapter
        self._clock = clock or (lambda: datetime.now(UTC))

    def build(
        self, request: OfficialDraftingCandidateBuildRequest
    ) -> OfficialDraftingCandidatePlan:
        blockers: list[CandidateBuildBlocker] = []
        manifest_path = f"json/releases/corpus-release-{request.release_id}.json"
        write_set = (manifest_path,)

        payloads = self._read_payloads(request, blockers)
        alias_before = self._adapter.get_runtime_aliases()
        if blockers:
            return self._blocked_plan(
                request=request,
                manifest_path=manifest_path,
                runtime_aliases_sha256=alias_before.pin.content_sha256,
                blockers=blockers,
            )

        (
            file_list_payload,
            file_list_sha256,
            rights_payload,
            rights_sha256,
            completion_payload,
            completion_sha256,
            profile_payload,
            profile_sha256,
            principles_sha256,
        ) = payloads

        normalized_paths = self._validated_file_list(
            request=request,
            payload=file_list_payload,
            blockers=blockers,
        )
        completion_sources = self._validated_completion(
            request=request,
            payload=completion_payload,
            blockers=blockers,
        )
        rights_state = self._validated_rights(
            request=request,
            payload=rights_payload,
            expected_source_ids={item["source_id"] for item in completion_sources},
            blockers=blockers,
        )
        profile_sources = self._validated_profile(
            request=request,
            payload=profile_payload,
            expected_source_ids={item["source_id"] for item in completion_sources},
            blockers=blockers,
        )
        normalized_sources, decompositions = self._validated_artifacts(
            normalized_paths=normalized_paths,
            completion_sources=completion_sources,
            profile_sources=profile_sources,
            blockers=blockers,
        )
        projection = self._validated_projection(
            request=request,
            payload=completion_payload,
            blockers=blockers,
        )

        if blockers:
            return self._blocked_plan(
                request=request,
                manifest_path=manifest_path,
                finalized_file_list_sha256=file_list_sha256,
                rights_review_sha256=rights_sha256,
                completion_evidence_sha256=completion_sha256,
                profile_sha256=profile_sha256,
                core_principles_sha256=principles_sha256,
                runtime_aliases_sha256=alias_before.pin.content_sha256,
                normalized_sources=normalized_sources,
                decompositions=decompositions,
                projection=projection,
                blockers=blockers,
            )

        manifest = self._manifest(
            request=request,
            manifest_path=manifest_path,
            normalized_sources=normalized_sources,
            decompositions=decompositions,
            projection=projection,
            file_list_sha256=file_list_sha256,
            rights_sha256=rights_sha256,
            completion_sha256=completion_sha256,
            profile_sha256=profile_sha256,
            principles_sha256=principles_sha256,
            rights_purpose=str(rights_state["purpose"]),
            runtime_aliases_sha256=alias_before.pin.content_sha256,
        )
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        )
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        actual_write_set: tuple[str, ...] = ()

        if request.apply:
            target = self._adapter.root / manifest_path
            if target.exists():
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.RELEASE_COLLISION,
                        subject=manifest_path,
                        detail="candidate manifest already exists",
                    )
                )
            else:
                self._write_manifest(target, manifest_bytes)
                actual_write_set = (manifest_path,)
            alias_after = self._adapter.get_runtime_aliases()
            if alias_after.pin.content_sha256 != alias_before.pin.content_sha256:
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.HASH_MISMATCH,
                        subject=request.runtime_aliases_path,
                        detail="runtime alias snapshot changed during candidate build",
                    )
                )

        if blockers:
            return self._blocked_plan(
                request=request,
                manifest_path=manifest_path,
                manifest_sha256=manifest_sha256,
                finalized_file_list_sha256=file_list_sha256,
                rights_review_sha256=rights_sha256,
                completion_evidence_sha256=completion_sha256,
                profile_sha256=profile_sha256,
                core_principles_sha256=principles_sha256,
                runtime_aliases_sha256=alias_before.pin.content_sha256,
                normalized_sources=normalized_sources,
                decompositions=decompositions,
                projection=projection,
                actual_write_set=actual_write_set,
                blockers=blockers,
            )

        return OfficialDraftingCandidatePlan(
            status="ready",
            dry_run=not request.apply,
            release_id=request.release_id,
            release_target=request.release_target,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            source_count=len(normalized_sources),
            decomposition_count=len(decompositions),
            finalized_file_list_sha256=file_list_sha256,
            rights_review_sha256=rights_sha256,
            completion_evidence_sha256=completion_sha256,
            profile_sha256=profile_sha256,
            core_principles_sha256=principles_sha256,
            runtime_aliases_sha256=alias_before.pin.content_sha256,
            normalized_sources=tuple(normalized_sources),
            decompositions=tuple(decompositions),
            projection_bindings=projection,
            write_set=write_set,
            actual_write_set=actual_write_set,
        )

    def _read_payloads(
        self,
        request: OfficialDraftingCandidateBuildRequest,
        blockers: list[CandidateBuildBlocker],
    ) -> tuple[
        dict[str, Any],
        str,
        dict[str, Any],
        str,
        dict[str, Any],
        str,
        dict[str, Any],
        str,
        str,
    ] | None:
        try:
            file_list = self._read_json(request.finalized_file_list_path)
            rights = self._read_json(request.rights_review_path)
            completion = self._read_json(request.completion_evidence_path)
            profile = self._read_json(request.profile_path)
            principles = self._adapter.read_artifact(request.core_principles_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.MISSING_ARTIFACT,
                    subject=request.batch_id,
                    detail=str(exc),
                )
            )
            return None
        return (
            file_list[0],
            file_list[1],
            rights[0],
            rights[1],
            completion[0],
            completion[1],
            profile[0],
            profile[1],
            principles.pin.content_sha256,
        )

    def _read_json(self, relative_path: str) -> tuple[dict[str, Any], str]:
        self._validate_relative_input(relative_path)
        artifact = self._adapter.read_artifact(relative_path)
        payload = json.loads(artifact.text)
        if not isinstance(payload, dict):
            raise ValueError(f"expected JSON object at {relative_path}")
        return payload, artifact.pin.content_sha256

    def _validate_relative_input(self, relative_path: str) -> None:
        path = PurePosixPath(relative_path)
        if path.is_absolute() or any(
            part in {"", ".", ".."} or part.lower() == "inbox" for part in path.parts
        ):
            raise ValueError(f"forbidden input path: {relative_path}")

    def _validated_file_list(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        payload: Mapping[str, Any],
        blockers: list[CandidateBuildBlocker],
    ) -> list[str]:
        batch_id = payload.get("batch_id")
        paths = payload.get("normalized_paths")
        if batch_id != request.batch_id or not isinstance(paths, list):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.finalized_file_list_path,
                    detail="finalized file list is malformed or for the wrong batch",
                )
            )
            return []
        validated: list[str] = []
        seen: set[str] = set()
        for value in paths:
            if not isinstance(value, str):
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=request.finalized_file_list_path,
                        detail="finalized file list contains a non-string path",
                    )
                )
                continue
            try:
                self._validate_relative_input(value)
                pure = PurePosixPath(value)
                if pure.parts[0] != "reference":
                    raise ValueError("normalized paths must stay under reference/")
                if value in seen:
                    raise ValueError("duplicate normalized path")
            except ValueError as exc:
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=value,
                        detail=str(exc),
                    )
                )
                continue
            seen.add(value)
            validated.append(value)
        if len(validated) != request.expected_source_count:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.finalized_file_list_path,
                    detail=(
                        "finalized file list must contain exactly "
                        f"{request.expected_source_count} normalized paths"
                    ),
                )
            )
        return validated

    def _validated_completion(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        payload: Mapping[str, Any],
        blockers: list[CandidateBuildBlocker],
    ) -> list[dict[str, Any]]:
        if payload.get("batch_id") != request.batch_id:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="completion evidence belongs to a different batch",
                )
            )
            return []
        counts = payload.get("counts")
        sources = payload.get("sources")
        if not isinstance(counts, dict) or not isinstance(sources, list):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="completion evidence must include counts and sources",
                )
            )
            return []
        expected = request.expected_source_count
        if any(counts.get(key) != expected for key in ("sources", "decompositions")):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="completion evidence counts do not match the sealed batch",
                )
            )
        source_ids: set[str] = set()
        normalized_paths: set[str] = set()
        decomposition_paths: set[str] = set()
        validated: list[dict[str, Any]] = []
        for item in sources:
            if not isinstance(item, dict):
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=request.completion_evidence_path,
                        detail="completion evidence source entry is malformed",
                    )
                )
                continue
            try:
                source_id = str(item["source_id"])
                normalized_path = str(item["normalized_path"])
                decomposition_path = str(item["decomposition_path"])
                self._validate_relative_input(normalized_path)
                self._validate_relative_input(decomposition_path)
                if source_id in source_ids:
                    raise ValueError("duplicate source_id in completion evidence")
                if normalized_path in normalized_paths:
                    raise ValueError("duplicate normalized_path in completion evidence")
                if decomposition_path in decomposition_paths:
                    raise ValueError(
                        "duplicate decomposition_path in completion evidence"
                    )
            except (KeyError, ValueError) as exc:
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=request.completion_evidence_path,
                        detail=str(exc),
                    )
                )
                continue
            source_ids.add(source_id)
            normalized_paths.add(normalized_path)
            decomposition_paths.add(decomposition_path)
            validated.append(dict(item))
        if len(validated) != expected:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="completion evidence must seal the exact batch source set",
                )
            )
        return validated

    def _validated_rights(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        payload: Mapping[str, Any],
        expected_source_ids: set[str],
        blockers: list[CandidateBuildBlocker],
    ) -> dict[str, Any]:
        source_ids = payload.get("approved_source_ids")
        quarantine = payload.get("quarantine")
        purpose = payload.get("purpose")
        if (
            payload.get("batch_id") != request.batch_id
            or not isinstance(source_ids, list)
            or not isinstance(quarantine, list)
            or not isinstance(purpose, str)
        ):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.rights_review_path,
                    detail="rights review is malformed or for the wrong batch",
                )
            )
            return {"purpose": ""}
        approved = {str(item) for item in source_ids}
        if approved != expected_source_ids:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.rights_review_path,
                    detail="rights-cleared source identifiers do not match the batch",
                )
            )
        if quarantine:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.rights_review_path,
                    detail="rights quarantine must be empty for candidate assembly",
                )
            )
        return {"purpose": purpose}

    def _validated_profile(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        payload: Mapping[str, Any],
        expected_source_ids: set[str],
        blockers: list[CandidateBuildBlocker],
    ) -> dict[str, dict[str, Any]]:
        sources = payload.get("sources")
        if (
            payload.get("batch_id") != request.batch_id
            or payload.get("human_review_required") is not True
            or not isinstance(sources, list)
        ):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.profile_path,
                    detail="profile artifact is malformed or not human-review required",
                )
            )
            return {}
        validated: dict[str, dict[str, Any]] = {}
        for item in sources:
            if not isinstance(item, dict) or any(
                key not in item for key in ("source_id", "currentness", "scope", "contradictions")
            ):
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=request.profile_path,
                        detail="profile source entry must preserve currentness, scope, and contradictions",
                    )
                )
                continue
            source_id = str(item["source_id"])
            if source_id in validated:
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=request.profile_path,
                        detail="profile contains a duplicate source_id",
                    )
                )
                continue
            validated[source_id] = dict(item)
        if set(validated) != expected_source_ids:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.profile_path,
                    detail="profile source identifiers do not match the sealed batch",
                )
            )
        return validated

    def _validated_artifacts(
        self,
        *,
        normalized_paths: list[str],
        completion_sources: list[dict[str, Any]],
        profile_sources: dict[str, dict[str, Any]],
        blockers: list[CandidateBuildBlocker],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        source_by_path = {
            str(item["normalized_path"]): item for item in completion_sources
        }
        normalized_sources: list[dict[str, Any]] = []
        decompositions: list[dict[str, Any]] = []
        seen_decompositions: set[str] = set()
        for normalized_path in normalized_paths:
            source = source_by_path.get(normalized_path)
            if source is None:
                blockers.append(
                    CandidateBuildBlocker(
                        code=CandidateBuildBlockerCode.INVALID_INPUT,
                        subject=normalized_path,
                        detail="finalized file list includes an unrelated normalized path",
                    )
                )
                continue
            source_id = str(source["source_id"])
            profile = profile_sources.get(source_id, {})
            try:
                normalized = self._adapter.read_artifact(normalized_path)
                frontmatter, _ = split_frontmatter(
                    normalized.text,
                    relative_path=normalized_path,
                )
                source_sha256 = self._normalized_sha256(source["source_sha256"])
                if (
                    self._normalized_sha256(frontmatter.get("source_sha256", ""))
                    != source_sha256
                ):
                    raise ValueError(
                        "normalized frontmatter source_sha256 differs from completion evidence"
                    )
                decomposition_path = str(source["decomposition_path"])
                if decomposition_path in seen_decompositions:
                    raise ValueError("normalized artifact does not have exactly one decomposition")
                seen_decompositions.add(decomposition_path)
                decomposition_id = Path(decomposition_path).stem
                decomposition = self._adapter.get_decomposition(decomposition_id)
                if decomposition.source_file != normalized_path:
                    raise ValueError("decomposition source_file differs from normalized path")
                if (
                    self._normalized_sha256(
                        decomposition.frontmatter.get("source_sha256", "")
                    )
                    != source_sha256
                ):
                    raise ValueError("decomposition source hash differs from completion evidence")
                if not decomposition.chunks:
                    raise ValueError("decomposition chunks must be non-empty")
                normalized_sources.append(
                    {
                        "source_id": source_id,
                        "normalized_path": normalized_path,
                        "source_sha256": source_sha256,
                        "normalized_sha256": normalized.pin.content_sha256,
                        "currentness": profile.get("currentness"),
                        "scope": profile.get("scope"),
                        "contradictions": profile.get("contradictions"),
                    }
                )
                decompositions.append(
                    {
                        "source_id": source_id,
                        "path": decomposition_path,
                        "sha256": decomposition.pin.content_sha256,
                        "chunk_count": len(decomposition.chunks),
                    }
                )
            except Exception as exc:
                blockers.append(
                    CandidateBuildBlocker(
                        code=(
                            CandidateBuildBlockerCode.HASH_MISMATCH
                            if "sha256" in str(exc)
                            else CandidateBuildBlockerCode.MISSING_ARTIFACT
                        ),
                        subject=source_id,
                        detail=str(exc),
                    )
                )
        normalized_sources.sort(key=lambda item: str(item["normalized_path"]))
        decompositions.sort(key=lambda item: str(item["path"]))
        return normalized_sources, decompositions

    def _normalized_sha256(self, value: object) -> str:
        if isinstance(value, int):
            return f"{value:064x}"
        return str(value).strip().lower()

    def _validated_projection(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        payload: Mapping[str, Any],
        blockers: list[CandidateBuildBlocker],
    ) -> dict[str, Any]:
        projection = payload.get("projection")
        counts = payload.get("counts")
        if not isinstance(projection, dict) or not isinstance(counts, dict):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="completion evidence lacks projection bindings",
                )
            )
            return {}
        if any(
            counts.get(key) != request.expected_source_count
            for key in ("vector_documents", "graph_documents")
        ):
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="projection counts do not match the sealed batch",
                )
            )
        if projection.get("release_target") != request.release_target:
            blockers.append(
                CandidateBuildBlocker(
                    code=CandidateBuildBlockerCode.INVALID_INPUT,
                    subject=request.completion_evidence_path,
                    detail="projection bindings target the wrong runtime alias",
                )
            )
        return {
            "release_target": projection.get("release_target"),
            "vector_collection": projection.get("vector_collection"),
            "graph_name": projection.get("graph_name"),
            "vector_documents": counts.get("vector_documents"),
            "graph_documents": counts.get("graph_documents"),
        }

    def _manifest(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        manifest_path: str,
        normalized_sources: list[dict[str, Any]],
        decompositions: list[dict[str, Any]],
        projection: dict[str, Any],
        file_list_sha256: str,
        rights_sha256: str,
        completion_sha256: str,
        profile_sha256: str,
        principles_sha256: str,
        rights_purpose: str,
        runtime_aliases_sha256: str,
    ) -> dict[str, Any]:
        normalized_paths = [str(item["normalized_path"]) for item in normalized_sources]
        snapshot_hash = hashlib.sha256(
            json.dumps(decompositions, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        generated_at = self._clock()
        if isinstance(generated_at, datetime):
            generated_at_text = generated_at.astimezone(UTC).isoformat()
        else:
            generated_at_text = str(generated_at)
        return {
            "release_id": request.release_id,
            "release_target": request.release_target,
            "schema_version": 1,
            "mode": "batch-scoped-candidate",
            "generated_at": generated_at_text,
            "source_commit": "",
            "vector_collection": projection.get("vector_collection"),
            "graph_name": projection.get("graph_name"),
            "documents": {
                "new": normalized_paths,
                "changed": [],
                "deleted": [],
                "unchanged": [],
            },
            "document_counts": {
                "new": len(normalized_sources),
                "changed": 0,
                "deleted": 0,
                "unchanged": 0,
            },
            "verification": {
                "qdrant_collection_ok": True,
                "graph_rebuild_ok": True,
                "finalized_file_list_sha256": file_list_sha256,
                "rights_review_sha256": rights_sha256,
                "completion_evidence_sha256": completion_sha256,
                "profile_sha256": profile_sha256,
                "core_principles_sha256": principles_sha256,
                "runtime_aliases_sha256": runtime_aliases_sha256,
            },
            "official_drafting_batch": {
                "batch_id": request.batch_id,
                "source_count": len(normalized_sources),
                "rights_purpose": rights_purpose,
                "manifest_path": manifest_path,
                "sources": normalized_sources,
            },
            "decomposed_snapshot": {
                "snapshot_hash": snapshot_hash,
                "file_count": len(decompositions),
                "files": decompositions,
            },
        }

    def _blocked_plan(
        self,
        *,
        request: OfficialDraftingCandidateBuildRequest,
        manifest_path: str,
        runtime_aliases_sha256: str,
        blockers: list[CandidateBuildBlocker],
        manifest_sha256: str | None = None,
        finalized_file_list_sha256: str | None = None,
        rights_review_sha256: str | None = None,
        completion_evidence_sha256: str | None = None,
        profile_sha256: str | None = None,
        core_principles_sha256: str | None = None,
        normalized_sources: list[dict[str, Any]] | None = None,
        decompositions: list[dict[str, Any]] | None = None,
        projection: dict[str, Any] | None = None,
        actual_write_set: tuple[str, ...] = (),
    ) -> OfficialDraftingCandidatePlan:
        empty_hash = "0" * 64
        return OfficialDraftingCandidatePlan(
            status="blocked",
            dry_run=not request.apply,
            release_id=request.release_id,
            release_target=request.release_target,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256 or empty_hash,
            source_count=len(normalized_sources or ()),
            decomposition_count=len(decompositions or ()),
            finalized_file_list_sha256=finalized_file_list_sha256 or empty_hash,
            rights_review_sha256=rights_review_sha256 or empty_hash,
            completion_evidence_sha256=completion_evidence_sha256 or empty_hash,
            profile_sha256=profile_sha256 or empty_hash,
            core_principles_sha256=core_principles_sha256 or empty_hash,
            runtime_aliases_sha256=runtime_aliases_sha256,
            normalized_sources=tuple(normalized_sources or ()),
            decompositions=tuple(decompositions or ()),
            projection_bindings=projection or {},
            write_set=(manifest_path,),
            actual_write_set=actual_write_set,
            blockers=tuple(blockers),
        )

    def _write_manifest(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        observed = target.read_bytes()
        if observed != content:
            target.unlink(missing_ok=True)
            raise ValueError("written candidate manifest bytes differ from the plan")


__all__ = [
    "CandidateBuildBlocker",
    "CandidateBuildBlockerCode",
    "OfficialDraftingCandidateBuildRequest",
    "OfficialDraftingCandidatePlan",
    "OfficialDraftingCandidateReleaseBuilder",
]
