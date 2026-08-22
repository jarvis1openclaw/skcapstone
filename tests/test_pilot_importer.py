from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sklegal_migration import PilotImporter

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def pin(path: str, digest: str) -> SimpleNamespace:
    return SimpleNamespace(
        relative_path=path,
        content_sha256=digest,
        observed_at=NOW,
    )


def record(
    legacy_id: str,
    path: str,
    digest: str,
    frontmatter: dict[str, object],
) -> SimpleNamespace:
    return SimpleNamespace(
        legacy_id=legacy_id,
        relative_path=path,
        frontmatter=frontmatter,
        body="source body is preserved by the adapter and not rewritten",
        pin=pin(path, digest),
        registry_pin=pin("incidents/_incident-registry.md", "f" * 64),
    )


def packet(version: int, path: str, digest: str) -> SimpleNamespace:
    return SimpleNamespace(
        packet_version=version,
        facts_pin=pin(path, digest),
        facts={"version": version},
        review_pin=None,
    )


def test_dry_run_is_deterministic_and_does_not_write_hammertime() -> None:
    importer = PilotImporter()
    records = (
        record(
            "PRB-2026-009",
            "incidents/problems/liberty/PROBLEM.md",
            "a" * 64,
            {"status": "open", "trust_name": "Liberty Auto"},
        ),
        record(
            "INC-016",
            "incidents/problems/liberty/incidents/INCIDENT.md",
            "b" * 64,
            {"execution_status": "not_started", "response_period": "business days"},
        ),
    )
    first = importer.build_plan(
        records,
        source_snapshot="hammertime-release-2026-08-19",
        packet_references=(
            packet(3, "reference/packet-v3-facts.json", "3" * 64),
            packet(4, "reference/packet-v4-facts.json", "4" * 64),
        ),
    )
    second = importer.build_plan(
        records,
        source_snapshot="hammertime-release-2026-08-19",
        packet_references=(
            packet(3, "reference/packet-v3-facts.json", "3" * 64),
            packet(4, "reference/packet-v4-facts.json", "4" * 64),
        ),
    )

    assert first.json() == second.json()
    assert first.hammer_time_mutation is False
    assert first.write_operations == ()
    assert first.review_required is True
    assert {item.packet_version for item in first.version_lineage} == {3, 4}
    assert [
        item.packet_version
        for item in first.version_lineage
        if item.current_review_baseline
    ] == [4]


def test_tensions_remain_unresolved_and_source_hashes_are_reconciled() -> None:
    importer = PilotImporter()
    plan = importer.build_plan(
        (
            record(
                "PRB-2026-009",
                "problem.md",
                "a" * 64,
                {"trust_name": "A", "response_period": "business days"},
            ),
            record(
                "INC-016",
                "incident.md",
                "b" * 64,
                {"trust_name": "B", "response_period": "calendar days"},
            ),
        ),
        source_snapshot="snapshot",
        previous_source_hashes={"problem.md": "changed"},
    )

    assert plan.changed_sources == ("problem.md",)
    assert {t.key for t in plan.tensions} == {"party_or_trust_name", "timing_rule"}
    assert all(t.status == "unresolved" and t.review_required for t in plan.tensions)
    assert all(f.review_status == "source_asserted" for f in plan.facts)


def test_idempotency_key_changes_when_source_or_mapping_changes() -> None:
    base = PilotImporter.idempotency_key(
        "matter.md", "a" * 64, "matter", "problem.matter@1"
    )
    assert base == PilotImporter.idempotency_key(
        "matter.md", "a" * 64, "matter", "problem.matter@1"
    )
    assert base != PilotImporter.idempotency_key(
        "matter.md", "b" * 64, "matter", "problem.matter@1"
    )
    assert base != PilotImporter.idempotency_key(
        "matter.md", "a" * 64, "matter", "problem.matter@2"
    )
