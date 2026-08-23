"""Deterministic official drafting candidate release builder tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sklegal_hammertime import (
    CandidateBuildBlockerCode,
    HammerTimeReleaseAdapter,
    OfficialDraftingCandidateBuildRequest,
    OfficialDraftingCandidateInspector,
    OfficialDraftingCandidateReleaseBuilder,
    QualificationStatus,
)

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
BATCH_ID = "2026-08-21-official-government-style-manuals"
RELEASE_ID = "dev-20260823-official-drafting-standards-candidate-1"
TARGET = "dev"
SOURCE_COUNT = 14


def _write_text(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _write_json(root: Path, relative: str, payload: dict[str, object]) -> None:
    _write_text(root, relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _sha256(root: Path, relative: str) -> str:
    import hashlib

    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def _source_sha256(index: int) -> str:
    return f"{index + 1:064x}"[-64:]


def _build_root(root: Path) -> dict[str, object]:
    normalized_paths: list[str] = []
    completion_sources: list[dict[str, object]] = []
    profile_sources: list[dict[str, object]] = []
    approved_source_ids: list[str] = []
    for index in range(SOURCE_COUNT):
        source_id = f"OFFICIAL-{index + 1:02d}"
        source_sha256 = _source_sha256(index)
        normalized_path = f"reference/legal/official-style-{index + 1:02d}.md"
        decomposition_id = f"official-style-{index + 1:02d}"
        decomposition_path = f"json/decomposed/{decomposition_id}.json"
        normalized_paths.append(normalized_path)
        approved_source_ids.append(source_id)
        _write_text(
            root,
            normalized_path,
            (
                "---\n"
                f"title: Official Style {index + 1:02d}\n"
                f"source_id: {source_id}\n"
                f"source_sha256: \"{source_sha256}\"\n"
                "---\n\n"
                f"# Official Style {index + 1:02d}\n\n"
                "Synthetic drafting guidance.\n"
            ),
        )
        _write_json(
            root,
            decomposition_path,
            {
                "source_file": normalized_path,
                "decomposed_at": NOW.isoformat(),
                "frontmatter": {
                    "title": f"Official Style {index + 1:02d}",
                    "source_sha256": source_sha256,
                },
                "stats": {
                    "chunks": 1,
                    "claims": 0,
                    "citations": 0,
                    "entities": 0,
                    "relationships": 0,
                },
                "chunks": [
                    {
                        "chunk_id": f"chk-{index + 1:02d}",
                        "chunk_index": 0,
                        "text": f"Official Style {index + 1:02d}",
                        "parent_doc": normalized_path,
                        "total_chunks": 1,
                    }
                ],
                "claims": [],
                "citations": [],
                "entities": [],
                "relationships": [],
            },
        )
        completion_sources.append(
            {
                "source_id": source_id,
                "normalized_path": normalized_path,
                "source_sha256": source_sha256,
                "decomposition_path": decomposition_path,
            }
        )
        profile_sources.append(
            {
                "source_id": source_id,
                "currentness": "current",
                "scope": f"scope-{index + 1:02d}",
                "contradictions": [],
            }
        )
    _write_json(
        root,
        "json/state/runtime-aliases.json",
        {
            "schema_version": 2,
            "updated_at": NOW.isoformat(),
            "aliases": {
                TARGET: {
                    "current": {
                        "release_id": "dev-prior-release",
                        "manifest_path": "json/releases/corpus-release-dev-prior-release.json",
                        "promoted_at": NOW.isoformat(),
                        "vector_collection": "hammertime-v3-dev",
                        "graph_name": "hammertime-v4-dev",
                    },
                    "previous": {
                        "release_id": "dev-earlier-release",
                        "manifest_path": "json/releases/corpus-release-dev-earlier-release.json",
                        "promoted_at": NOW.isoformat(),
                        "vector_collection": "hammertime-v3-dev",
                        "graph_name": "hammertime-v4-dev",
                    },
                }
            },
        },
    )
    _write_json(
        root,
        "docs/evidence/corpus/finalized-file-list.json",
        {"batch_id": BATCH_ID, "normalized_paths": normalized_paths},
    )
    _write_json(
        root,
        "docs/evidence/corpus/rights-review.json",
        {
            "batch_id": BATCH_ID,
            "purpose": "internal_release_candidate_assembly",
            "approved_source_ids": approved_source_ids,
            "quarantine": [],
        },
    )
    _write_json(
        root,
        "docs/evidence/corpus/completion-evidence.json",
        {
            "batch_id": BATCH_ID,
            "counts": {
                "sources": SOURCE_COUNT,
                "decompositions": SOURCE_COUNT,
                "vector_documents": SOURCE_COUNT,
                "graph_documents": SOURCE_COUNT,
            },
            "projection": {
                "release_target": TARGET,
                "vector_collection": "hammertime-v3-dev",
                "graph_name": "hammertime-v4-dev",
            },
            "sources": completion_sources,
        },
    )
    _write_json(
        root,
        "profiles/official-drafting-style-profiles.json",
        {
            "batch_id": BATCH_ID,
            "human_review_required": True,
            "sources": profile_sources,
        },
    )
    _write_text(
        root,
        "docs/drafting-styles/OFFICIAL-DRAFTING-CORE-PRINCIPLES.md",
        "# Core principles\n\nSynthetic guidance.\n",
    )
    return {
        "file_list_path": "docs/evidence/corpus/finalized-file-list.json",
        "rights_review_path": "docs/evidence/corpus/rights-review.json",
        "completion_evidence_path": "docs/evidence/corpus/completion-evidence.json",
        "profile_path": "profiles/official-drafting-style-profiles.json",
        "core_principles_path": "docs/drafting-styles/OFFICIAL-DRAFTING-CORE-PRINCIPLES.md",
        "runtime_aliases_path": "json/state/runtime-aliases.json",
    }


def _request(paths: dict[str, object], *, apply: bool = False):
    return OfficialDraftingCandidateBuildRequest(
        batch_id=BATCH_ID,
        release_id=RELEASE_ID,
        release_target=TARGET,
        finalized_file_list_path=str(paths["file_list_path"]),
        rights_review_path=str(paths["rights_review_path"]),
        completion_evidence_path=str(paths["completion_evidence_path"]),
        profile_path=str(paths["profile_path"]),
        core_principles_path=str(paths["core_principles_path"]),
        runtime_aliases_path=str(paths["runtime_aliases_path"]),
        apply=apply,
        expected_source_count=SOURCE_COUNT,
    )


def _builder(root: Path) -> OfficialDraftingCandidateReleaseBuilder:
    return OfficialDraftingCandidateReleaseBuilder(
        HammerTimeReleaseAdapter(root=root),
        clock=lambda: NOW,
    )


def _exclusive_manifest_sink(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(content)
    if target.read_bytes() != content:
        raise ValueError("written candidate manifest bytes differ from the plan")


def _applying_builder(root: Path) -> OfficialDraftingCandidateReleaseBuilder:
    return OfficialDraftingCandidateReleaseBuilder(
        HammerTimeReleaseAdapter(root=root),
        clock=lambda: NOW,
        manifest_sink=_exclusive_manifest_sink,
    )


def test_dry_run_builds_deterministic_plan_without_writes(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    aliases_before = _sha256(tmp_path, str(paths["runtime_aliases_path"]))
    request = _request(paths)

    first = _builder(tmp_path).build(request)
    second = _builder(tmp_path).build(request)

    assert request.apply is False
    assert first == second
    assert first.status == "ready"
    assert first.dry_run is True
    assert first.actual_write_set == ()
    assert first.write_set == (
        f"json/releases/corpus-release-{RELEASE_ID}.json",
    )
    assert first.source_count == SOURCE_COUNT
    assert first.decomposition_count == SOURCE_COUNT
    assert _sha256(tmp_path, str(paths["runtime_aliases_path"])) == aliases_before
    assert not (tmp_path / first.manifest_path).exists()


def test_apply_writes_one_manifest_with_exclusive_create(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    plan = _applying_builder(tmp_path).build(_request(paths, apply=True))

    assert plan.status == "ready"
    assert plan.dry_run is False
    assert plan.actual_write_set == (plan.manifest_path,)
    assert (tmp_path / plan.manifest_path).exists()
    inspector = OfficialDraftingCandidateInspector(HammerTimeReleaseAdapter(root=tmp_path))
    report = inspector.inspect(
        release_id=RELEASE_ID,
        expected_manifest_sha256=plan.manifest_sha256,
        expected_target=TARGET,
        expected_source_count=SOURCE_COUNT,
    )
    assert report.status is QualificationStatus.QUALIFIED


def test_apply_without_external_sink_fails_closed_without_writes(
    tmp_path: Path,
) -> None:
    paths = _build_root(tmp_path)
    plan = _builder(tmp_path).build(_request(paths, apply=True))

    assert plan.status == "blocked"
    assert plan.actual_write_set == ()
    assert CandidateBuildBlockerCode.INVALID_INPUT in {
        blocker.code for blocker in plan.blockers
    }
    assert not (tmp_path / plan.manifest_path).exists()


def test_external_sink_byte_drift_is_detected_without_package_cleanup(
    tmp_path: Path,
) -> None:
    paths = _build_root(tmp_path)

    def drifting_sink(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content + b"drift\n")

    builder = OfficialDraftingCandidateReleaseBuilder(
        HammerTimeReleaseAdapter(root=tmp_path),
        clock=lambda: NOW,
        manifest_sink=drifting_sink,
    )
    plan = builder.build(_request(paths, apply=True))

    assert plan.status == "blocked"
    assert plan.actual_write_set == (plan.manifest_path,)
    assert CandidateBuildBlockerCode.HASH_MISMATCH in {
        blocker.code for blocker in plan.blockers
    }
    assert (tmp_path / plan.manifest_path).read_bytes().endswith(b"drift\n")


def test_missing_normalized_artifact_blocks_without_alias_change(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    aliases_before = _sha256(tmp_path, str(paths["runtime_aliases_path"]))
    (tmp_path / "reference/legal/official-style-14.md").unlink()

    plan = _builder(tmp_path).build(_request(paths))

    assert plan.status == "blocked"
    assert CandidateBuildBlockerCode.MISSING_ARTIFACT in {
        blocker.code for blocker in plan.blockers
    }
    assert _sha256(tmp_path, str(paths["runtime_aliases_path"])) == aliases_before


def test_duplicate_or_unrelated_file_list_entry_blocks(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    file_list_path = tmp_path / str(paths["file_list_path"])
    payload = json.loads(file_list_path.read_text(encoding="utf-8"))
    payload["normalized_paths"][-1] = payload["normalized_paths"][0]
    file_list_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path).build(_request(paths))
    assert plan.status == "blocked"
    assert CandidateBuildBlockerCode.INVALID_INPUT in {
        blocker.code for blocker in plan.blockers
    }

    paths = _build_root(tmp_path / "other")
    file_list_path = (tmp_path / "other") / str(paths["file_list_path"])
    payload = json.loads(file_list_path.read_text(encoding="utf-8"))
    payload["normalized_paths"][-1] = "reference/legal/unrelated-source.md"
    file_list_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "other").build(_request(paths))
    assert plan.status == "blocked"
    assert any("unrelated normalized path" in blocker.detail for blocker in plan.blockers)


def test_inbox_and_traversal_paths_fail_closed_before_reads(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    file_list_path = tmp_path / str(paths["file_list_path"])
    payload = json.loads(file_list_path.read_text(encoding="utf-8"))
    payload["normalized_paths"][0] = "Inbox/forbidden.md"
    file_list_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path).build(_request(paths))
    assert plan.status == "blocked"
    assert any(blocker.subject == "Inbox/forbidden.md" for blocker in plan.blockers)

    paths = _build_root(tmp_path / "other")
    file_list_path = (tmp_path / "other") / str(paths["file_list_path"])
    payload = json.loads(file_list_path.read_text(encoding="utf-8"))
    payload["normalized_paths"][0] = "../escape.md"
    file_list_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "other").build(_request(paths))
    assert plan.status == "blocked"
    assert any(blocker.subject == "../escape.md" for blocker in plan.blockers)


def test_rights_quarantine_and_source_set_mismatch_block(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    rights_path = tmp_path / str(paths["rights_review_path"])
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    rights["quarantine"] = ["OFFICIAL-14"]
    rights_path.write_text(json.dumps(rights, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path).build(_request(paths))
    assert plan.status == "blocked"
    assert any("rights quarantine" in blocker.detail for blocker in plan.blockers)

    paths = _build_root(tmp_path / "other")
    rights_path = (tmp_path / "other") / str(paths["rights_review_path"])
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    rights["approved_source_ids"].pop()
    rights_path.write_text(json.dumps(rights, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "other").build(_request(paths))
    assert plan.status == "blocked"
    assert any("rights-cleared source identifiers" in blocker.detail for blocker in plan.blockers)


def test_rights_purpose_must_match_candidate_assembly(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    rights_path = tmp_path / str(paths["rights_review_path"])
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    rights["purpose"] = "internal_research"
    rights_path.write_text(json.dumps(rights, indent=2, sort_keys=True) + "\n")

    plan = _builder(tmp_path).build(_request(paths))

    assert plan.status == "blocked"
    assert any("rights review purpose must be" in blocker.detail for blocker in plan.blockers)


def test_changed_normalized_source_hash_blocks(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    normalized = tmp_path / "reference/legal/official-style-01.md"
    normalized.write_text(
        normalized.read_text(encoding="utf-8").replace(
            _source_sha256(0), "0" * 64, 1
        ),
        encoding="utf-8",
    )

    plan = _builder(tmp_path).build(_request(paths))

    assert plan.status == "blocked"
    assert CandidateBuildBlockerCode.HASH_MISMATCH in {
        blocker.code for blocker in plan.blockers
    }


def test_normalized_frontmatter_source_id_must_match_completion_evidence(
    tmp_path: Path,
) -> None:
    paths = _build_root(tmp_path)
    normalized = tmp_path / "reference/legal/official-style-01.md"
    normalized.write_text(
        normalized.read_text(encoding="utf-8").replace(
            "source_id: OFFICIAL-01",
            "source_id: OFFICIAL-99",
            1,
        ),
        encoding="utf-8",
    )

    plan = _builder(tmp_path).build(_request(paths))

    assert plan.status == "blocked"
    assert CandidateBuildBlockerCode.INVALID_INPUT in {
        blocker.code for blocker in plan.blockers
    }
    assert any("frontmatter source_id differs" in blocker.detail for blocker in plan.blockers)


def test_missing_duplicate_empty_and_wrong_parent_decomposition_block(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    (tmp_path / "json/decomposed/official-style-14.json").unlink()
    plan = _builder(tmp_path).build(_request(paths))
    assert plan.status == "blocked"
    assert CandidateBuildBlockerCode.MISSING_ARTIFACT in {
        blocker.code for blocker in plan.blockers
    }

    paths = _build_root(tmp_path / "duplicate")
    completion_path = (tmp_path / "duplicate") / str(paths["completion_evidence_path"])
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["sources"][1]["decomposition_path"] = payload["sources"][0]["decomposition_path"]
    completion_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "duplicate").build(_request(paths))
    assert plan.status == "blocked"
    assert any("duplicate decomposition_path" in blocker.detail for blocker in plan.blockers)

    paths = _build_root(tmp_path / "empty")
    decomposition_path = tmp_path / "empty" / "json/decomposed/official-style-01.json"
    decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
    decomposition["chunks"] = []
    decomposition_path.write_text(json.dumps(decomposition, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "empty").build(_request(paths))
    assert plan.status == "blocked"
    assert any("non-empty" in blocker.detail for blocker in plan.blockers)

    paths = _build_root(tmp_path / "wrong-parent")
    decomposition_path = tmp_path / "wrong-parent" / "json/decomposed/official-style-01.json"
    decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
    decomposition["source_file"] = "reference/legal/wrong-parent.md"
    decomposition_path.write_text(json.dumps(decomposition, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "wrong-parent").build(_request(paths))
    assert plan.status == "blocked"
    assert any("source_file differs" in blocker.detail for blocker in plan.blockers)


def test_completion_or_projection_count_mismatch_blocks(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    completion_path = tmp_path / str(paths["completion_evidence_path"])
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["counts"]["sources"] = SOURCE_COUNT - 1
    completion_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path).build(_request(paths))
    assert plan.status == "blocked"
    assert any("counts do not match" in blocker.detail for blocker in plan.blockers)

    paths = _build_root(tmp_path / "projection")
    completion_path = (tmp_path / "projection") / str(paths["completion_evidence_path"])
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["counts"]["vector_documents"] = SOURCE_COUNT - 1
    completion_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    plan = _builder(tmp_path / "projection").build(_request(paths))
    assert plan.status == "blocked"
    assert any("projection counts" in blocker.detail for blocker in plan.blockers)


def test_existing_release_collision_blocks_apply(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    target = tmp_path / f"json/releases/corpus-release-{RELEASE_ID}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("already exists\n", encoding="utf-8")

    plan = _applying_builder(tmp_path).build(_request(paths, apply=True))

    assert plan.status == "blocked"
    assert CandidateBuildBlockerCode.RELEASE_COLLISION in {
        blocker.code for blocker in plan.blockers
    }
    assert target.read_text(encoding="utf-8") == "already exists\n"


def test_runtime_alias_file_is_immutable_after_success_and_failures(tmp_path: Path) -> None:
    paths = _build_root(tmp_path)
    alias_before = _sha256(tmp_path, str(paths["runtime_aliases_path"]))
    ready = _applying_builder(tmp_path).build(_request(paths, apply=True))
    assert ready.status == "ready"
    assert _sha256(tmp_path, str(paths["runtime_aliases_path"])) == alias_before

    paths = _build_root(tmp_path / "blocked")
    alias_before = _sha256(tmp_path / "blocked", str(paths["runtime_aliases_path"]))
    rights_path = (tmp_path / "blocked") / str(paths["rights_review_path"])
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    rights["quarantine"] = ["OFFICIAL-14"]
    rights_path.write_text(json.dumps(rights, indent=2, sort_keys=True) + "\n")
    blocked = _builder(tmp_path / "blocked").build(_request(paths))
    assert blocked.status == "blocked"
    assert _sha256(tmp_path / "blocked", str(paths["runtime_aliases_path"])) == alias_before
