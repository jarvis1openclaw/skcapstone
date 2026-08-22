"""Bounded official drafting candidate inspection tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sklegal_hammertime import (
    HammerTimeReleaseAdapter,
    OfficialDraftingCandidateInspector,
    QualificationFindingCode,
    QualificationStatus,
)

from tests.support import hammertime_fixture as fixture

SOURCE_ID = "OFFICIAL-FIXTURE-1"


def _manifest_path() -> str:
    return f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"


def _write_candidate(root: Path) -> str:
    manifest_path = root / _manifest_path()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    decomposition_path = f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
    manifest["mode"] = "batch-scoped-candidate"
    manifest["official_drafting_batch"] = {
        "source_count": 1,
        "sources": [
            {
                "source_id": SOURCE_ID,
                "normalized_path": fixture.REFERENCE_RELATIVE,
                "source_sha256": fixture.SOURCE_SHA256,
                "normalized_sha256": fixture.fixture_sha256(
                    root, fixture.REFERENCE_RELATIVE
                ),
            }
        ],
    }
    manifest["decomposed_snapshot"] = {
        "file_count": 1,
        "files": [
            {
                "source_id": SOURCE_ID,
                "path": decomposition_path,
                "sha256": fixture.fixture_sha256(root, decomposition_path),
                "chunk_count": 1,
            }
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return fixture.fixture_sha256(root, _manifest_path())


def _inspect(root: Path, manifest_sha256: str):
    return OfficialDraftingCandidateInspector(
        HammerTimeReleaseAdapter(root=root)
    ).inspect(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        expected_source_count=1,
    )


def test_inspection_follows_only_pinned_candidate_artifacts(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)

    report = _inspect(root, manifest_sha256)

    assert report.status is QualificationStatus.QUALIFIED
    assert report.source_count == 1
    assert report.decomposition_count == 1
    assert len(report.verified_artifact_sha256) == 2
    assert report.findings == ()


@pytest.mark.parametrize(
    ("mutation", "finding_code"),
    [
        ("manifest-hash", QualificationFindingCode.RELEASE_MISMATCH),
        ("source-hash", QualificationFindingCode.MISSING_ARTIFACT),
        ("decomposition-parent", QualificationFindingCode.RELEASE_MISMATCH),
        ("extra-document", QualificationFindingCode.RELEASE_MISMATCH),
        ("missing-batch", QualificationFindingCode.RELEASE_MISMATCH),
    ],
)
def test_inspection_fails_closed(
    tmp_path: Path,
    mutation: str,
    finding_code: QualificationFindingCode,
) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    manifest_path = root / _manifest_path()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if mutation == "manifest-hash":
        manifest_sha256 = "0" * 64
    elif mutation == "source-hash":
        manifest["official_drafting_batch"]["sources"][0]["normalized_sha256"] = (
            "0" * 64
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        manifest_sha256 = fixture.fixture_sha256(root, _manifest_path())
    elif mutation == "decomposition-parent":
        decomposition_path = root / f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
        decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
        decomposition["source_file"] = "reference/legal/wrong-parent.md"
        decomposition_path.write_text(
            json.dumps(decomposition, indent=2, sort_keys=True), encoding="utf-8"
        )
        manifest["decomposed_snapshot"]["files"][0]["sha256"] = fixture.fixture_sha256(
            root, f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        manifest_sha256 = fixture.fixture_sha256(root, _manifest_path())
    elif mutation == "extra-document":
        manifest["documents"]["unchanged"] = ["reference/legal/unpinned.md"]
        manifest["document_counts"]["unchanged"] = 1
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        manifest_sha256 = fixture.fixture_sha256(root, _manifest_path())
    else:
        del manifest["official_drafting_batch"]
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        manifest_sha256 = fixture.fixture_sha256(root, _manifest_path())

    report = _inspect(root, manifest_sha256)

    assert report.status is QualificationStatus.BLOCKED
    assert finding_code in {finding.code for finding in report.findings}


def test_inspection_does_not_expose_mutation_methods(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    _write_candidate(root)
    inspector = OfficialDraftingCandidateInspector(HammerTimeReleaseAdapter(root=root))

    assert not hasattr(inspector, "promote")
    assert not hasattr(inspector, "rollback")
    assert not hasattr(inspector, "write")
