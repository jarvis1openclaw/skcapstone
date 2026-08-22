"""Contract tests for the SKL-S5-01A pilot dry-run tooling.

All fixtures are synthetic and built in a temporary directory by the
shared HammerTime fixture builder. No real matter content, real legacy
identifiers, or HammerTime paths are used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sklegal_hammertime import (
    HammerTimeReleaseAdapter,
    MatterAccessDenied,
    MatterAccessRequest,
)
from sklegal_migration import (
    SourceInventoryEntry,
    prove_unchanged,
    render_mapping_review_markdown,
    render_sha256_manifest,
    run_pilot_dry_run,
)

from tests.support import hammertime_fixture as fixture

FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
SNAPSHOT = "fixture-snapshot-2099-01-02"


def allow_all(request: MatterAccessRequest) -> bool:
    return True


def deny_all(request: MatterAccessRequest) -> bool:
    return False


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return fixture.build_hammertime_fixture(tmp_path)


@pytest.fixture
def adapter(tree: Path) -> HammerTimeReleaseAdapter:
    return HammerTimeReleaseAdapter(
        root=tree,
        matter_authorizer=allow_all,
        clock=lambda: FIXED_NOW,
    )


def test_dry_run_proves_zero_source_changes_and_no_import_writes(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )

    assert report.zero_source_changes is True
    assert report.import_writes == 0
    assert report.plan.hammer_time_mutation is False
    assert report.plan.write_operations == ()
    proof = report.change_proof
    assert proof.pre_count == proof.post_count > 0
    assert len(proof.unchanged) == proof.pre_count
    assert proof.changed == proof.added == proof.removed == proof.mtime_changed == ()


def test_inventory_covers_the_full_matter_tree(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )
    paths = {entry.relative_path for entry in report.pre_inventory}

    assert fixture.PROBLEM_RELATIVE in paths
    assert fixture.INCIDENT_RELATIVE in paths
    assert f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json" in paths
    assert f"{fixture.INCIDENT_DIR_RELATIVE}/packet-v1-facts.json" in paths
    assert f"{fixture.INCIDENT_DIR_RELATIVE}/packet-v2-facts.json" in paths
    assert f"{fixture.INCIDENT_DIR_RELATIVE}/phase-0-wave-1-v2-review.md" in paths
    assert (
        f"{fixture.INCIDENT_DIR_RELATIVE}/correspondence/OWNER-DIRECTIONS.md" in paths
    )
    assert all("inbox" not in path.lower() for path in paths)
    for entry in report.pre_inventory:
        assert entry.content_sha256 == fixture.fixture_sha256(
            adapter.root, entry.relative_path
        )


def test_mapping_review_proposes_targets_without_advancing_state(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )
    review = {entry.legacy_id: entry for entry in report.mapping_review}

    assert review[fixture.PROBLEM_ID].target_type == "matter"
    assert review[fixture.PROBLEM_ID].mapping_rule == "problem.matter@1"
    assert review[fixture.INCIDENT_ID].target_type == "matter_event"
    assert review[fixture.INCIDENT_ID].mapping_rule == "incident.transaction_review@1"
    assert all(entry.mapping_status == "proposed" for entry in report.mapping_review)
    assert all(entry.review_decision == "pending" for entry in report.mapping_review)
    assert all(item.reviewed_by is None for item in report.plan.records)
    assert report.reconciliation["approval_states_advanced"] == 0
    assert report.reconciliation["execution_states_advanced"] == 0


def test_tensions_and_version_lineage_are_preserved(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )

    assert all(
        tension.status == "unresolved" and tension.review_required
        for tension in report.plan.tensions
    )
    lineage = {item.packet_version: item for item in report.plan.version_lineage}
    assert set(lineage) == {1, 2}
    assert lineage[2].current_review_baseline is True
    assert lineage[2].historical is False
    assert lineage[1].current_review_baseline is False
    assert lineage[1].historical is True


def test_source_keys_and_validation_summary_are_captured(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )

    assert "status" in report.frontmatter_keys[fixture.PROBLEM_ID]
    packet_path = f"{fixture.INCIDENT_DIR_RELATIVE}/packet-v2-facts.json"
    assert "packet_version" in report.json_keys[packet_path]
    validation_path = f"{fixture.INCIDENT_DIR_RELATIVE}/validation-report.json"
    assert "valid" in report.json_keys[validation_path]
    assert report.validation_summary["valid"] is True
    assert report.validation_summary["legacy_id"] == fixture.INCIDENT_ID
    assert report.owner_directions_path == (
        f"{fixture.INCIDENT_DIR_RELATIVE}/correspondence/OWNER-DIRECTIONS.md"
    )


def test_dry_run_is_deterministic_with_a_fixed_clock(adapter) -> None:
    first = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )
    second = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )

    assert first.json() == second.json()
    assert first.run_id == second.run_id
    assert first.plan.import_batch_id == second.plan.import_batch_id


def test_dry_run_fails_closed_without_matter_authorization(tree) -> None:
    adapter = HammerTimeReleaseAdapter(
        root=tree,
        matter_authorizer=deny_all,
        clock=lambda: FIXED_NOW,
    )
    with pytest.raises(MatterAccessDenied):
        run_pilot_dry_run(
            adapter,
            source_snapshot=SNAPSHOT,
            problem_id=fixture.PROBLEM_ID,
            incident_id=fixture.INCIDENT_ID,
        )


def test_missing_owner_directions_is_recorded_not_fabricated(adapter, tree) -> None:
    (
        tree / fixture.INCIDENT_DIR_RELATIVE / "correspondence" / "OWNER-DIRECTIONS.md"
    ).unlink()

    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )

    assert report.owner_directions_path is None
    assert report.zero_source_changes is True


def test_prove_unchanged_flags_any_difference() -> None:
    def entry(path: str, digest: str, mtime: str = "2099-01-01T00:00:00+00:00"):
        return SourceInventoryEntry(
            relative_path=path,
            content_sha256=digest,
            byte_count=1,
            modified_at=mtime,
            observed_at=mtime,
        )

    pre = (
        entry("a.md", "a" * 64),
        entry("b.md", "b" * 64),
        entry("c.md", "c" * 64),
        entry("d.md", "d" * 64),
    )
    post = (
        entry("a.md", "a" * 64),
        entry("b.md", "0" * 64),
        entry("c.md", "c" * 64, mtime="2099-01-02T00:00:00+00:00"),
        entry("e.md", "e" * 64),
    )
    proof = prove_unchanged(pre, post)

    assert proof.zero_source_changes is False
    assert proof.unchanged == ("a.md", "c.md")
    assert proof.changed == ("b.md",)
    assert proof.added == ("e.md",)
    assert proof.removed == ("d.md",)
    assert proof.mtime_changed == ("c.md",)


def test_sha256_manifest_uses_sha256sum_format(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )
    lines = render_sha256_manifest(report).strip().splitlines()

    assert len(lines) == report.change_proof.pre_count
    for line in lines:
        digest, _, path = line.partition("  ")
        assert len(digest) == 64
        assert digest == fixture.fixture_sha256(adapter.root, path)


def test_mapping_review_markdown_is_complete_and_dash_clean(adapter) -> None:
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=SNAPSHOT,
        problem_id=fixture.PROBLEM_ID,
        incident_id=fixture.INCIDENT_ID,
    )
    markdown = render_mapping_review_markdown(report)

    assert "\u2014" not in markdown
    assert "\u2013" not in markdown
    assert "## Source hash capture" in markdown
    assert "## Zero-source-change proof" in markdown
    assert "## Proposed mappings (human review pending)" in markdown
    assert "## Human mapping review decision" in markdown
    assert "Zero source changes: True" in markdown
    assert fixture.PROBLEM_ID in markdown
    assert fixture.INCIDENT_ID in markdown
    assert "- [x]" in markdown
    assert "- [ ]" not in markdown
