"""SKL-S6-05 scoped lexical retrieval tests over pinned candidate chunks."""

from __future__ import annotations

import json
from pathlib import Path

from sklegal_hammertime import (
    OfficialDraftingScopedRetrievalRunner,
    QualificationFindingCode,
    QualificationStatus,
    RetrievalQuerySpec,
    RetrievalSourceExpectation,
)

from tests.support import hammertime_fixture as fixture

SOURCE_ID = "OFFICIAL-FIXTURE-1"
CONFLICT_SOURCE_ID = "OFFICIAL-FIXTURE-2"
CONFLICT_RELATIVE = "reference/legal/fixture-conflict.md"
CONFLICT_DECOMPOSITION_ID = "0002-fixture-conflict-abcdef"
CONFLICT_SOURCE_SHA256 = "c" * 64
ISSUING_BODY = "Fixture Government Publishing Office"
VERSION = "2099 edition"
SCOPE = "fixture government publications"
PROFILE_SHA256 = "d" * 64


def _write_candidate(root: Path) -> str:
    conflict_markdown = (
        "---\n"
        'title: "Fixture conflict"\n'
        "category: legal\n"
        f"source_sha256: {CONFLICT_SOURCE_SHA256}\n"
        "---\n"
        "\n"
        "# Fixture Conflict\n"
        "\n"
        "Synthetic conflict body.\n"
    )
    target = root / CONFLICT_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(conflict_markdown, encoding="utf-8")
    decomposition = {
        "source_file": CONFLICT_RELATIVE,
        "decomposed_at": "2099-01-02T00:00:00+00:00",
        "frontmatter": {
            "title": "Fixture conflict",
            "source_sha256": CONFLICT_SOURCE_SHA256,
        },
        "stats": {"chunks": 1, "claims": 0, "citations": 0, "entities": 0},
        "chunks": [
            {
                "chunk_id": "chk_fixture0002",
                "parent_doc": CONFLICT_RELATIVE,
                "chunk_index": 0,
                "section_title": "Fixture Conflict",
                "text": "# Fixture Conflict\n\nSynthetic conflict body.",
                "total_chunks": 1,
            }
        ],
        "claims": [],
        "citations": [],
        "entities": [],
        "relationships": [],
    }
    decomposition_path = root / f"json/decomposed/{CONFLICT_DECOMPOSITION_ID}.json"
    decomposition_path.write_text(
        json.dumps(decomposition, indent=2, sort_keys=True), encoding="utf-8"
    )

    manifest_path = root / f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["official_drafting_batch"] = {
        "source_count": 2,
        "sources": [
            {
                "source_id": SOURCE_ID,
                "normalized_path": fixture.REFERENCE_RELATIVE,
                "source_sha256": fixture.SOURCE_SHA256,
                "normalized_sha256": fixture.fixture_sha256(
                    root, fixture.REFERENCE_RELATIVE
                ),
            },
            {
                "source_id": CONFLICT_SOURCE_ID,
                "normalized_path": CONFLICT_RELATIVE,
                "source_sha256": CONFLICT_SOURCE_SHA256,
                "normalized_sha256": fixture.fixture_sha256(root, CONFLICT_RELATIVE),
            },
        ],
    }
    manifest["decomposed_snapshot"] = {
        "snapshot_hash": "fixture",
        "file_count": 2,
        "files": [
            {
                "source_id": SOURCE_ID,
                "path": f"json/decomposed/{fixture.DECOMPOSITION_ID}.json",
                "sha256": fixture.fixture_sha256(
                    root, f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
                ),
                "chunk_count": 1,
            },
            {
                "source_id": CONFLICT_SOURCE_ID,
                "path": f"json/decomposed/{CONFLICT_DECOMPOSITION_ID}.json",
                "sha256": fixture.fixture_sha256(
                    root, f"json/decomposed/{CONFLICT_DECOMPOSITION_ID}.json"
                ),
                "chunk_count": 1,
            },
        ],
    }
    manifest["documents"]["new"].append(CONFLICT_RELATIVE)
    manifest["document_counts"]["new"] = 2
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return fixture.fixture_sha256(
        root, f"json/releases/corpus-release-{fixture.RELEASE_ID}.json"
    )


def _sources(**overrides: object) -> tuple[RetrievalSourceExpectation, ...]:
    base = {
        "source_id": SOURCE_ID,
        "issuing_body": ISSUING_BODY,
        "version": VERSION,
        "scope": SCOPE,
        "source_sha256": fixture.SOURCE_SHA256,
        "currentness": "current official edition",
        "presented_as_current": True,
    }
    conflict = {
        "source_id": CONFLICT_SOURCE_ID,
        "issuing_body": ISSUING_BODY,
        "version": VERSION,
        "scope": SCOPE,
        "source_sha256": CONFLICT_SOURCE_SHA256,
        "currentness": "current official edition",
        "presented_as_current": True,
    }
    base.update(overrides)
    conflict.update(
        {key: value for key, value in overrides.items() if key != "source_id"}
    )
    return (
        RetrievalSourceExpectation.model_validate(base),
        RetrievalSourceExpectation.model_validate(conflict),
    )


def _queries() -> tuple[RetrievalQuerySpec, ...]:
    return (
        RetrievalQuerySpec(
            query_id="q-targeted",
            terms=("synthetic", "reference"),
            expected_source_ids=(SOURCE_ID,),
            conflict_source_ids=(),
            derivation="fixture targeted retrieval over the pinned chunk",
            derivation_sha256=PROFILE_SHA256,
        ),
        RetrievalQuerySpec(
            query_id="q-conflict",
            terms=("synthetic",),
            expected_source_ids=(SOURCE_ID,),
            conflict_source_ids=(CONFLICT_SOURCE_ID,),
            derivation="fixture preserved conflict between two pinned sources",
            derivation_sha256=PROFILE_SHA256,
        ),
    )


def _runner(root: Path) -> OfficialDraftingScopedRetrievalRunner:
    return OfficialDraftingScopedRetrievalRunner(fixture_adapter(root))


def fixture_adapter(root: Path):
    from sklegal_hammertime import HammerTimeReleaseAdapter

    return HammerTimeReleaseAdapter(root=root)


def _run(root: Path, manifest_sha256: str):
    return _runner(root).run(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        queries=_queries(),
        sources=_sources(),
    )


def test_scoped_retrieval_reports_qualified_bundle(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)

    bundle = _run(root, manifest_sha256)

    assert bundle.status is QualificationStatus.QUALIFIED
    assert bundle.findings == ()
    assert [check.query_id for check in bundle.checks] == ["q-targeted", "q-conflict"]
    targeted = bundle.checks[0]
    assert [hit.source_id for hit in targeted.hits] == [SOURCE_ID]
    conflict = bundle.checks[1]
    assert {hit.source_id for hit in conflict.hits} == {
        SOURCE_ID,
        CONFLICT_SOURCE_ID,
    }


def test_hits_carry_source_linked_metadata(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)

    hit = _run(root, manifest_sha256).checks[0].hits[0]

    assert hit.issuing_body == ISSUING_BODY
    assert hit.version == VERSION
    assert hit.scope == SCOPE
    assert hit.source_sha256 == fixture.SOURCE_SHA256
    assert hit.presented_as_current is True
    assert hit.locator.startswith(f"json/decomposed/{fixture.DECOMPOSITION_ID}")
    assert "#chunk-" in hit.locator


def test_missing_expected_source_blocks(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    bundle = _runner(root).run(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        queries=(
            RetrievalQuerySpec(
                query_id="q-missing",
                terms=("synthetic", "reference"),
                expected_source_ids=(CONFLICT_SOURCE_ID,),
                conflict_source_ids=(),
                derivation="fixture missing expected source",
                derivation_sha256=PROFILE_SHA256,
            ),
        ),
        sources=_sources(),
    )

    assert bundle.status is QualificationStatus.BLOCKED
    assert QualificationFindingCode.MISSING_ARTIFACT in {
        finding.code for finding in bundle.findings
    }


def test_collapsed_conflict_blocks(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    bundle = _runner(root).run(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        queries=(
            RetrievalQuerySpec(
                query_id="q-collapsed",
                terms=("synthetic", "reference"),
                expected_source_ids=(SOURCE_ID,),
                conflict_source_ids=(CONFLICT_SOURCE_ID,),
                derivation="fixture collapsed conflict",
                derivation_sha256=PROFILE_SHA256,
            ),
        ),
        sources=_sources(),
    )

    assert QualificationFindingCode.SCOPE_COLLAPSE in {
        finding.code for finding in bundle.findings
    }


def test_unknown_source_in_query_blocks(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    bundle = _runner(root).run(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        queries=(
            RetrievalQuerySpec(
                query_id="q-unknown",
                terms=("synthetic",),
                expected_source_ids=(SOURCE_ID, "OFFICIAL-FIXTURE-UNKNOWN"),
                conflict_source_ids=(),
                derivation="fixture unknown source reference",
                derivation_sha256=PROFILE_SHA256,
            ),
        ),
        sources=_sources(),
    )

    assert bundle.status is QualificationStatus.BLOCKED
    assert QualificationFindingCode.MISSING_ARTIFACT in {
        finding.code for finding in bundle.findings
    }


def test_source_hash_mismatch_blocks(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    bundle = _runner(root).run(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        queries=_queries(),
        sources=_sources(source_sha256="e" * 64),
    )

    assert QualificationFindingCode.WRONG_SOURCE_VERSION in {
        finding.code for finding in bundle.findings
    }


def test_manifest_pin_mismatch_blocks(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    _write_candidate(root)

    bundle = _run(root, "0" * 64)

    assert bundle.status is QualificationStatus.BLOCKED
    assert QualificationFindingCode.RELEASE_MISMATCH in {
        finding.code for finding in bundle.findings
    }


def test_missing_currentness_record_blocks(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    only_primary = (
        RetrievalSourceExpectation.model_validate(
            {
                "source_id": SOURCE_ID,
                "issuing_body": ISSUING_BODY,
                "version": VERSION,
                "scope": SCOPE,
                "source_sha256": fixture.SOURCE_SHA256,
                "currentness": "current official edition",
                "presented_as_current": True,
            }
        ),
    )
    bundle = _runner(root).run(
        release_id=fixture.RELEASE_ID,
        expected_manifest_sha256=manifest_sha256,
        expected_target=fixture.ALIAS_TARGET,
        queries=_queries(),
        sources=only_primary,
    )

    assert QualificationFindingCode.RETRIEVAL_EVIDENCE_MISMATCH in {
        finding.code for finding in bundle.findings
    }


def test_runner_exposes_no_mutation_method(tmp_path: Path) -> None:
    root = fixture.build_hammertime_fixture(tmp_path)
    runner = _runner(root)
    forbidden = {"promote", "rollback", "write", "delete", "move", "dispatch"}
    assert forbidden.isdisjoint(name.lower() for name in dir(runner))


def test_scoped_retrieval_evidence_feeds_qualified_release(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from sklegal_hammertime import (
        AliasRoundTripEvidence,
        HammerTimeReleaseAdapter,
        OfficialDraftingReleaseQualifier,
        OfficialDraftingReleaseRequest,
        OfficialDraftingSource,
        ProjectionQualificationEvidence,
        QualificationStatus,
        SecondaryReviewEvidence,
        SourceRightsState,
    )

    root = fixture.build_hammertime_fixture(tmp_path)
    manifest_sha256 = _write_candidate(root)
    bundle = _run(root, manifest_sha256)
    assert bundle.status is QualificationStatus.QUALIFIED

    decomposition_path = f"json/decomposed/{fixture.DECOMPOSITION_ID}.json"
    request = OfficialDraftingReleaseRequest(
        release_id=fixture.RELEASE_ID,
        release_manifest_sha256=manifest_sha256,
        target=fixture.ALIAS_TARGET,
        sources=(
            OfficialDraftingSource(
                source_id=SOURCE_ID,
                issuing_body=ISSUING_BODY,
                version=VERSION,
                scope=SCOPE,
                normalized_path=fixture.REFERENCE_RELATIVE,
                source_sha256=fixture.SOURCE_SHA256,
                normalized_sha256=fixture.fixture_sha256(
                    root, fixture.REFERENCE_RELATIVE
                ),
                decomposition_id=fixture.DECOMPOSITION_ID,
                decomposition_sha256=fixture.fixture_sha256(root, decomposition_path),
                rights_state=SourceRightsState.VERIFIED,
            ),
            OfficialDraftingSource(
                source_id=CONFLICT_SOURCE_ID,
                issuing_body=ISSUING_BODY,
                version=VERSION,
                scope=SCOPE,
                normalized_path=CONFLICT_RELATIVE,
                source_sha256=CONFLICT_SOURCE_SHA256,
                normalized_sha256=fixture.fixture_sha256(root, CONFLICT_RELATIVE),
                decomposition_id=CONFLICT_DECOMPOSITION_ID,
                decomposition_sha256=fixture.fixture_sha256(
                    root, f"json/decomposed/{CONFLICT_DECOMPOSITION_ID}.json"
                ),
                rights_state=SourceRightsState.VERIFIED,
            ),
        ),
        retrieval_checks=bundle.checks,
        projection=ProjectionQualificationEvidence(
            release_id=fixture.RELEASE_ID,
            release_manifest_sha256=manifest_sha256,
            lexical_release_id=fixture.RELEASE_ID,
            vector_release_id=fixture.RELEASE_ID,
            graph_release_id=fixture.RELEASE_ID,
            deep_health_passed=True,
            reconciliation_complete=True,
        ),
        secondary_review=SecondaryReviewEvidence(
            logical_route="sklegal.local-corpus-secondary-review",
            served_model="local-qwen3.8-fixture",
            prompt_sha256="a" * 64,
            output_sha256="b" * 64,
            reviewed_at=datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC),
            passed=True,
        ),
        alias_round_trip=AliasRoundTripEvidence(
            target=fixture.ALIAS_TARGET,
            prior_current_release_id=fixture.PREVIOUS_RELEASE_ID,
            promoted_release_id=fixture.RELEASE_ID,
            promoted_previous_release_id=fixture.PREVIOUS_RELEASE_ID,
            promotion_succeeded=True,
            rollback_succeeded=True,
            rollback_current_release_id=fixture.PREVIOUS_RELEASE_ID,
            rollback_previous_release_id=fixture.RELEASE_ID,
        ),
    )

    report = OfficialDraftingReleaseQualifier(
        HammerTimeReleaseAdapter(
            root=root, clock=lambda: datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
        )
    ).qualify(request)

    assert report.status is QualificationStatus.QUALIFIED
    assert report.findings == ()
