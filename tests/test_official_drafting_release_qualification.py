"""SKL-S6-05 official drafting release qualification tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sklegal_hammertime import (
    AliasRoundTripEvidence,
    HammerTimeReleaseAdapter,
    OfficialDraftingReleaseQualifier,
    OfficialDraftingReleaseRequest,
    OfficialDraftingSource,
    ProjectionQualificationEvidence,
    QualificationFindingCode,
    QualificationStatus,
    RetrievalHitEvidence,
    ScopedRetrievalEvidence,
    SecondaryReviewEvidence,
    SourceRightsState,
)

from tests.support import hammertime_fixture as fixture

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
SOURCE_ID = "OFFICIAL-FIXTURE-1"
ISSUING_BODY = "Fixture Government Publishing Office"
VERSION = "2099 edition"
SCOPE = "fixture government publications"


def _request(
    root: Path,
    *,
    release_id: str = fixture.RELEASE_ID,
    version: str = VERSION,
    hit_scope: str = SCOPE,
    conflict_source_ids: tuple[str, ...] = (),
    superseded: bool = False,
    presented_as_current: bool = True,
    projection_stale: bool = False,
    rights_state: SourceRightsState = SourceRightsState.VERIFIED,
    secondary_review_passed: bool = True,
    promotion_succeeded: bool = True,
    rollback_succeeded: bool = True,
) -> OfficialDraftingReleaseRequest:
    manifest_path = f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"
    decomposition_path = f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
    source_sha256 = fixture.SOURCE_SHA256
    normalized_sha256 = fixture.fixture_sha256(root, fixture.REFERENCE_RELATIVE)
    return OfficialDraftingReleaseRequest(
        release_id=release_id,
        release_manifest_sha256=fixture.fixture_sha256(root, manifest_path),
        target=fixture.ALIAS_TARGET,
        sources=(
            OfficialDraftingSource(
                source_id=SOURCE_ID,
                issuing_body=ISSUING_BODY,
                version=VERSION,
                scope=SCOPE,
                normalized_path=fixture.REFERENCE_RELATIVE,
                source_sha256=source_sha256,
                normalized_sha256=normalized_sha256,
                decomposition_id=fixture.DECOMPOSITION_ID,
                decomposition_sha256=fixture.fixture_sha256(root, decomposition_path),
                rights_state=rights_state,
                superseded=superseded,
            ),
        ),
        retrieval_checks=(
            ScopedRetrievalEvidence(
                query_id="fixture-query",
                expected_source_ids=(SOURCE_ID,),
                conflict_source_ids=conflict_source_ids,
                hits=(
                    RetrievalHitEvidence(
                        source_id=SOURCE_ID,
                        issuing_body=ISSUING_BODY,
                        version=version,
                        scope=hit_scope,
                        source_sha256=source_sha256,
                        locator="fixture-reference.md#fixture-reference",
                        presented_as_current=presented_as_current,
                    ),
                ),
            ),
        ),
        projection=ProjectionQualificationEvidence(
            release_id=release_id,
            release_manifest_sha256=fixture.fixture_sha256(root, manifest_path),
            lexical_release_id=release_id,
            vector_release_id=release_id,
            graph_release_id=release_id,
            deep_health_passed=not projection_stale,
            reconciliation_complete=not projection_stale,
            stale=projection_stale,
        ),
        secondary_review=SecondaryReviewEvidence(
            logical_route="sklegal.local-corpus-secondary-review",
            served_model="local-qwen3.8-fixture",
            prompt_sha256=HASH_A,
            output_sha256=HASH_B,
            reviewed_at=NOW,
            passed=secondary_review_passed,
        ),
        alias_round_trip=AliasRoundTripEvidence(
            target=fixture.ALIAS_TARGET,
            prior_current_release_id=fixture.PREVIOUS_RELEASE_ID,
            promoted_release_id=release_id,
            promoted_previous_release_id=fixture.PREVIOUS_RELEASE_ID,
            promotion_succeeded=promotion_succeeded,
            rollback_succeeded=rollback_succeeded,
            rollback_current_release_id=fixture.PREVIOUS_RELEASE_ID,
            rollback_previous_release_id=release_id,
        ),
    )


def _qualifier(root: Path) -> OfficialDraftingReleaseQualifier:
    return OfficialDraftingReleaseQualifier(
        HammerTimeReleaseAdapter(root=root, clock=lambda: NOW)
    )


def _codes(root: Path, request: OfficialDraftingReleaseRequest) -> set[object]:
    return {finding.code for finding in _qualifier(root).qualify(request).findings}


def test_healthy_candidate_is_qualified_with_immutable_references(
    tmp_path: Path,
) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    report = _qualifier(root).qualify(_request(root))
    assert report.status is QualificationStatus.QUALIFIED
    assert report.findings == ()
    assert report.aliases_sha256 == fixture.fixture_sha256(
        root, "json/state/runtime-aliases.json"
    )
    assert len(report.verified_artifact_sha256) == 2


def test_missing_artifact_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    request = _request(root)
    (root / f"json/decomposed/{fixture.DECOMPOSITION_ID}.json").unlink()
    assert QualificationFindingCode.MISSING_ARTIFACT in _codes(root, request)


def test_wrong_source_version_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.WRONG_SOURCE_VERSION in _codes(
        root, _request(root, version="obsolete fixture edition")
    )


def test_wrong_normalized_source_metadata_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    payload = _request(root).model_dump(mode="python")
    payload["sources"][0]["source_sha256"] = HASH_B
    request = OfficialDraftingReleaseRequest.model_validate(payload)
    assert QualificationFindingCode.WRONG_SOURCE_VERSION in _codes(root, request)


def test_missing_required_manifest_verification_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    request = _request(root)
    manifest_path = root / f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["verification"].pop("graph_rebuild_ok")
    manifest_path.write_text(json.dumps(manifest))
    assert QualificationFindingCode.RELEASE_MISMATCH in _codes(root, request)


def test_stale_projection_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.STALE_PROJECTION in _codes(
        root, _request(root, projection_stale=True)
    )


def test_quarantined_source_rights_block_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.SOURCE_RIGHTS_BLOCKED in _codes(
        root, _request(root, rights_state=SourceRightsState.QUARANTINED)
    )


def test_failed_secondary_qwen_review_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.SECONDARY_REVIEW_FAILED in _codes(
        root, _request(root, secondary_review_passed=False)
    )


def test_scope_collapse_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.SCOPE_COLLAPSE in _codes(
        root,
        _request(
            root,
            conflict_source_ids=(SOURCE_ID, "OFFICIAL-FIXTURE-CONFLICT"),
        ),
    )


def test_superseded_source_cannot_be_presented_as_current(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.SUPERSEDED_AS_CURRENT in _codes(
        root, _request(root, superseded=True, presented_as_current=True)
    )


def test_alias_drift_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    alias_path = root / "json/state/runtime-aliases.json"
    aliases = json.loads(alias_path.read_text())
    aliases["aliases"]["dev"]["current"]["graph_name"] = "stale-graph"
    alias_path.write_text(json.dumps(aliases))
    assert QualificationFindingCode.ALIAS_DRIFT in _codes(root, _request(root))


def test_failed_promotion_receipt_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.PROMOTION_FAILURE in _codes(
        root, _request(root, promotion_succeeded=False)
    )


def test_failed_rollback_receipt_blocks_release(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    assert QualificationFindingCode.ROLLBACK_FAILURE in _codes(
        root, _request(root, rollback_succeeded=False)
    )


def test_read_only_qualifier_exposes_no_mutation_method(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    qualifier = _qualifier(root)
    forbidden = {"promote", "rollback", "write", "delete", "move", "dispatch"}
    assert forbidden.isdisjoint(name.lower() for name in dir(qualifier))
