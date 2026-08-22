"""Read-only pilot dry-run orchestration for the Liberty Auto pilot.

This module executes pilot TDD phase A (source hash capture and
inventory) plus the dry-run mapping report on top of the S2-02 pilot
importer. It performs no import writes: every source touch goes through
the read-only HammerTime adapter, every mapping stays ``proposed`` with
a pending human review decision, and source preservation is proven by
hashing the full pilot matter tree before and after the run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sklegal_hammertime import HammerTimeReleaseAdapter, MissingPathError

from .pilot import LegacySourceFile, PilotImporter, PilotImportPlan

DEFAULT_PROBLEM_ID = "PRB-2026-009"
DEFAULT_INCIDENT_ID = "INC-016"
DEFAULT_ADAPTER_VERSION = "0.1.0"
SOURCE_SYSTEM = "hammertime"


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@dataclass(frozen=True)
class SourceInventoryEntry:
    """One pinned source file captured during the dry-run hash sweep."""

    relative_path: str
    content_sha256: str
    byte_count: int
    modified_at: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "content_sha256": self.content_sha256,
            "byte_count": self.byte_count,
            "modified_at": self.modified_at,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class SourceChangeProof:
    """Pre/post comparison proving the dry run changed no source bytes."""

    pre_count: int
    post_count: int
    unchanged: tuple[str, ...]
    changed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    mtime_changed: tuple[str, ...]

    @property
    def zero_source_changes(self) -> bool:
        return not (self.changed or self.added or self.removed or self.mtime_changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pre_count": self.pre_count,
            "post_count": self.post_count,
            "unchanged": list(self.unchanged),
            "changed": list(self.changed),
            "added": list(self.added),
            "removed": list(self.removed),
            "mtime_changed": list(self.mtime_changed),
            "zero_source_changes": self.zero_source_changes,
        }


def prove_unchanged(
    pre: tuple[SourceInventoryEntry, ...],
    post: tuple[SourceInventoryEntry, ...],
) -> SourceChangeProof:
    """Compare two inventory sweeps without harmonizing any difference."""
    pre_by_path = {entry.relative_path: entry for entry in pre}
    post_by_path = {entry.relative_path: entry for entry in post}
    unchanged = tuple(
        sorted(
            path
            for path, entry in pre_by_path.items()
            if path in post_by_path
            and post_by_path[path].content_sha256 == entry.content_sha256
        )
    )
    changed = tuple(
        sorted(
            path
            for path, entry in pre_by_path.items()
            if path in post_by_path
            and post_by_path[path].content_sha256 != entry.content_sha256
        )
    )
    added = tuple(sorted(set(post_by_path) - set(pre_by_path)))
    removed = tuple(sorted(set(pre_by_path) - set(post_by_path)))
    mtime_changed = tuple(
        sorted(
            path
            for path, entry in pre_by_path.items()
            if path in post_by_path
            and post_by_path[path].modified_at != entry.modified_at
        )
    )
    return SourceChangeProof(
        pre_count=len(pre),
        post_count=len(post),
        unchanged=unchanged,
        changed=changed,
        added=added,
        removed=removed,
        mtime_changed=mtime_changed,
    )


@dataclass(frozen=True)
class MappingReviewEntry:
    """One proposed mapping presented for human review."""

    legacy_id: str
    legacy_path: str
    target_type: str
    mapping_rule: str
    mapping_status: str
    idempotency_key: str
    review_decision: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy_id": self.legacy_id,
            "legacy_path": self.legacy_path,
            "target_type": self.target_type,
            "mapping_rule": self.mapping_rule,
            "mapping_status": self.mapping_status,
            "idempotency_key": self.idempotency_key,
            "review_decision": self.review_decision,
        }


@dataclass(frozen=True)
class PilotDryRunReport:
    """Complete dry-run evidence: hashes, plan, review, and change proof."""

    run_id: str
    adapter_version: str
    source_snapshot: str
    problem_legacy_id: str
    incident_legacy_id: str
    matter_root: str
    observed_at: str
    pre_inventory: tuple[SourceInventoryEntry, ...]
    post_inventory: tuple[SourceInventoryEntry, ...]
    change_proof: SourceChangeProof
    plan: PilotImportPlan
    mapping_review: tuple[MappingReviewEntry, ...]
    frontmatter_keys: dict[str, tuple[str, ...]]
    json_keys: dict[str, tuple[str, ...]]
    validation_summary: dict[str, Any]
    owner_directions_path: str | None
    skipped: tuple[str, ...]
    reconciliation: dict[str, Any]

    @property
    def zero_source_changes(self) -> bool:
        return self.change_proof.zero_source_changes

    @property
    def import_writes(self) -> int:
        return len(self.plan.write_operations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "adapter_version": self.adapter_version,
            "source_snapshot": self.source_snapshot,
            "problem_legacy_id": self.problem_legacy_id,
            "incident_legacy_id": self.incident_legacy_id,
            "matter_root": self.matter_root,
            "observed_at": self.observed_at,
            "pre_inventory": [entry.to_dict() for entry in self.pre_inventory],
            "post_inventory": [entry.to_dict() for entry in self.post_inventory],
            "change_proof": self.change_proof.to_dict(),
            "plan": self.plan.to_dict(),
            "mapping_review": [entry.to_dict() for entry in self.mapping_review],
            "frontmatter_keys": {
                key: list(value) for key, value in self.frontmatter_keys.items()
            },
            "json_keys": {key: list(value) for key, value in self.json_keys.items()},
            "validation_summary": self.validation_summary,
            "owner_directions_path": self.owner_directions_path,
            "skipped": list(self.skipped),
            "reconciliation": self.reconciliation,
            "zero_source_changes": self.zero_source_changes,
            "import_writes": self.import_writes,
        }

    def json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def _inventory_sweep(
    adapter: HammerTimeReleaseAdapter,
    legacy_id: str,
    *,
    parent_legacy_id: str | None,
) -> tuple[tuple[SourceInventoryEntry, ...], tuple[str, ...], str]:
    inventory = adapter.list_matter_artifacts(
        legacy_id,
        parent_legacy_id=parent_legacy_id,
    )
    entries = tuple(
        SourceInventoryEntry(
            relative_path=item.pin.relative_path,
            content_sha256=item.pin.content_sha256,
            byte_count=item.byte_count,
            modified_at=_iso(item.modified_at),
            observed_at=_iso(item.pin.observed_at),
        )
        for item in inventory.artifacts
    )
    return entries, tuple(inventory.skipped), inventory.matter_root


def _json_keys(
    adapter: HammerTimeReleaseAdapter,
    incident_id: str,
    *,
    parent_legacy_id: str,
    inventory: tuple[SourceInventoryEntry, ...],
) -> dict[str, tuple[str, ...]]:
    """Read top-level JSON keys for pilot inventory files, hash-checked."""
    keys: dict[str, tuple[str, ...]] = {}
    for entry in inventory:
        if not entry.relative_path.endswith(".json"):
            continue
        read = adapter.read_matter_artifact(
            incident_id,
            entry.relative_path,
            parent_legacy_id=parent_legacy_id,
            expected_sha256=entry.content_sha256,
        )
        try:
            payload = json.loads(read.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            keys[entry.relative_path] = ("<not-json-object>",)
            continue
        if isinstance(payload, dict):
            keys[entry.relative_path] = tuple(sorted(str(key) for key in payload))
        elif isinstance(payload, list):
            keys[entry.relative_path] = (f"<list:{len(payload)}>",)
        else:
            keys[entry.relative_path] = (f"<{type(payload).__name__}>",)
    return keys


def run_pilot_dry_run(
    adapter: HammerTimeReleaseAdapter,
    *,
    source_snapshot: str,
    problem_id: str = DEFAULT_PROBLEM_ID,
    incident_id: str = DEFAULT_INCIDENT_ID,
    adapter_version: str = DEFAULT_ADAPTER_VERSION,
) -> PilotDryRunReport:
    """Execute the read-only pilot dry run and return the full report.

    The matter tree is hashed before and after the importer plan is
    built. The plan contains proposals only; ``write_operations`` stays
    empty and every mapping remains ``proposed`` pending human review.
    """
    if not source_snapshot.strip():
        raise ValueError("source_snapshot must be non-empty")

    pre, skipped_pre, matter_root = _inventory_sweep(
        adapter, problem_id, parent_legacy_id=None
    )
    problem = adapter.resolve_legacy_matter(problem_id)
    incident = adapter.resolve_legacy_matter(
        incident_id,
        parent_legacy_id=problem_id,
    )
    packets = adapter.list_packet_references(incident_id, parent_legacy_id=problem_id)
    validation = adapter.get_matter_validation_report(
        incident_id,
        parent_legacy_id=problem_id,
    )
    try:
        directions = adapter.get_owner_directions(
            incident_id,
            parent_legacy_id=problem_id,
        )
        owner_directions_path: str | None = directions.pin.relative_path
    except MissingPathError:
        owner_directions_path = None

    json_keys = _json_keys(
        adapter,
        incident_id,
        parent_legacy_id=problem_id,
        inventory=pre,
    )

    importer = PilotImporter(adapter_version=adapter_version)
    probe = importer.build_plan(
        (problem, incident),
        source_snapshot=source_snapshot,
        packet_references=packets,
    )
    covered = {item.relative_path for item in probe.source_files}
    metadata_files = tuple(
        LegacySourceFile(
            relative_path=entry.relative_path,
            content_sha256=entry.content_sha256,
            observed_at=entry.observed_at,
            byte_count=entry.byte_count,
        )
        for entry in pre
        if entry.relative_path not in covered
    )
    plan = importer.build_plan(
        (problem, incident),
        source_snapshot=source_snapshot,
        packet_references=packets,
        metadata_files=metadata_files,
    )

    post, skipped_post, _ = _inventory_sweep(adapter, problem_id, parent_legacy_id=None)
    proof = prove_unchanged(pre, post)

    legacy_order = (problem, incident)
    mapping_review = tuple(
        MappingReviewEntry(
            legacy_id=record.legacy_id,
            legacy_path=record.relative_path,
            target_type=item.target_type,
            mapping_rule=item.mapping_rule,
            mapping_status=item.mapping_status,
            idempotency_key=item.idempotency_key,
        )
        for record, item in zip(legacy_order, plan.records, strict=True)
    )

    plan_paths = {item.relative_path for item in plan.source_files}
    inventory_paths = {entry.relative_path for entry in pre}
    excluded = tuple(sorted(inventory_paths - plan_paths))
    plan_only = tuple(sorted(plan_paths - inventory_paths))
    reconciliation = {
        "inventoried_files": len(pre),
        "plan_source_files": len(plan.source_files),
        "excluded_from_plan": list(excluded),
        "plan_sources_outside_matter_tree": list(plan_only),
        "records_proposed": len(plan.records),
        "fact_assertions_proposed": len(plan.facts),
        "tension_groups": len(plan.tensions),
        "packet_versions": [item.packet_version for item in plan.version_lineage],
        "mapping_statuses": sorted({item.mapping_status for item in plan.records}),
        "review_decisions_pending": len(mapping_review),
        "approval_states_advanced": 0,
        "execution_states_advanced": 0,
        "skipped_entries": sorted(set(skipped_pre) | set(skipped_post)),
    }
    validation_summary = {
        "legacy_id": validation.legacy_id,
        "valid": validation.valid,
        "validated_date": validation.validated_date,
        "error_count": len(validation.errors),
        "notice_count": len(validation.notices),
        "content_sha256": validation.pin.content_sha256,
    }
    run_id = str(
        uuid5(NAMESPACE_URL, f"dry-run:{SOURCE_SYSTEM}:{plan.import_batch_id}")
    )
    return PilotDryRunReport(
        run_id=run_id,
        adapter_version=adapter_version,
        source_snapshot=source_snapshot,
        problem_legacy_id=problem_id,
        incident_legacy_id=incident_id,
        matter_root=matter_root,
        observed_at=_iso(problem.pin.observed_at),
        pre_inventory=pre,
        post_inventory=post,
        change_proof=proof,
        plan=plan,
        mapping_review=mapping_review,
        frontmatter_keys={
            problem.legacy_id: tuple(sorted(str(key) for key in problem.frontmatter)),
            incident.legacy_id: tuple(sorted(str(key) for key in incident.frontmatter)),
        },
        json_keys=json_keys,
        validation_summary=validation_summary,
        owner_directions_path=owner_directions_path,
        skipped=tuple(sorted(set(skipped_pre) | set(skipped_post))),
        reconciliation=reconciliation,
    )


def render_sha256_manifest(report: PilotDryRunReport) -> str:
    """Render the pre-run hash manifest in sha256sum format."""
    lines = [
        f"{entry.content_sha256}  {entry.relative_path}"
        for entry in report.pre_inventory
    ]
    return "\n".join(lines) + "\n"


def render_mapping_review_markdown(report: PilotDryRunReport) -> str:
    """Render the human mapping-review artifact for the pilot dry run."""
    proof = report.change_proof
    plan = report.plan
    lines: list[str] = []
    add = lines.append
    add("# SKL-S5-01A pilot dry run and human mapping review")
    add("")
    add(f"Run id: `{report.run_id}`")
    add(f"Source snapshot: `{report.source_snapshot}`")
    add(f"Adapter version: `{report.adapter_version}`")
    add(f"Import batch proposal: `{plan.import_batch_id}`")
    add(f"Matter root: `{report.matter_root}`")
    add(f"Observed at: `{report.observed_at}`")
    add("")
    add("## Scope")
    add("")
    add(f"- Matter proposal: `{report.problem_legacy_id}` maps to Matter.")
    add(
        f"- Matter Event proposal: `{report.incident_legacy_id}` maps to a "
        "transaction-review Matter Event."
    )
    add("- This slice performs no import writes; every mapping is proposed only.")
    add("")
    add("## Provenance posture")
    add("")
    add(
        "- The source snapshot label denotes a pinned HammerTime working tree, "
        "not a corpus release: pilot matter records live under `incidents/`, "
        "which release manifests do not cover."
    )
    add(
        "- Every file is pinned by content SHA-256 and observation time, so "
        "any later source change produces a new batch id and a visible diff."
    )
    add("")
    add("## Source hash capture")
    add("")
    total_bytes = sum(entry.byte_count for entry in report.pre_inventory)
    add(f"- Files inventoried and hashed: {proof.pre_count} ({total_bytes} bytes)")
    add(f"- Skipped entries (never followed): {len(report.skipped)}")
    for entry in report.skipped:
        add(f"  - `{entry}`")
    add("")
    add("## Zero-source-change proof")
    add("")
    add(f"- Pre-run files hashed: {proof.pre_count}")
    add(f"- Post-run files hashed: {proof.post_count}")
    add(f"- Unchanged content: {len(proof.unchanged)}")
    add(f"- Changed content: {len(proof.changed)}")
    add(f"- Added paths: {len(proof.added)}")
    add(f"- Removed paths: {len(proof.removed)}")
    add(f"- Modification time changed: {len(proof.mtime_changed)}")
    add(f"- Zero source changes: {proof.zero_source_changes}")
    add(f"- Import write operations: {report.import_writes}")
    add("")
    add("## Proposed mappings (human review pending)")
    add("")
    add("| Legacy record | Legacy path | Target | Mapping rule | Status | Decision |")
    add("|---|---|---|---|---|---|")
    for item in report.mapping_review:
        add(
            f"| `{item.legacy_id}` | `{item.legacy_path}` | {item.target_type} "
            f"| `{item.mapping_rule}` | {item.mapping_status} | {item.review_decision} |"
        )
    add("")
    add("## Fact assertions and tension groups")
    add("")
    add(f"- Atomic fact assertions proposed: {len(plan.facts)}")
    add(f"- Tension groups (all unresolved, review required): {len(plan.tensions)}")
    for tension in plan.tensions:
        add(
            f"  - `{tension.key}`: {len(tension.assertion_ids)} assertions, "
            f"status {tension.status}"
        )
    add("")
    add("## Work product version lineage")
    add("")
    if plan.version_lineage:
        add("| Packet version | Source path | Historical | Current review baseline |")
        add("|---|---|---|---|")
        for version in plan.version_lineage:
            add(
                f"| v{version.packet_version} | `{version.source_path}` "
                f"| {version.historical} | {version.current_review_baseline} |"
            )
    else:
        add("No packet versions observed.")
    add("")
    add("## Source keys observed")
    add("")
    for legacy_id, keys in sorted(report.frontmatter_keys.items()):
        add(f"- `{legacy_id}` frontmatter keys: {', '.join(keys)}")
    for path, keys in sorted(report.json_keys.items()):
        add(f"- `{path}` JSON keys: {', '.join(keys)}")
    add("")
    add("## Validation report")
    add("")
    summary = report.validation_summary
    add(f"- Legacy record: `{summary['legacy_id']}`")
    add(f"- Valid: {summary['valid']}")
    add(f"- Validated date: {summary['validated_date']}")
    add(f"- Errors: {summary['error_count']}")
    add(f"- Notices: {summary['notice_count']}")
    add(f"- Report hash: `{summary['content_sha256']}`")
    add(f"- Owner directions pinned: `{report.owner_directions_path}`")
    add("")
    add("## Count reconciliation")
    add("")
    for key, value in sorted(report.reconciliation.items()):
        add(f"- {key}: {value}")
    add("")
    add("## Semantic verification (pilot TDD section 9 subset)")
    add("")
    matter_rules = {
        item.mapping_rule
        for item in report.mapping_review
        if item.target_type == "matter"
    }
    event_rules = {
        item.mapping_rule
        for item in report.mapping_review
        if item.target_type == "matter_event"
    }
    checks = (
        (
            f"`{report.problem_legacy_id}` maps to Matter, not Problem",
            matter_rules == {"problem.matter@1"},
        ),
        (
            f"`{report.incident_legacy_id}` maps to a transaction-review "
            "Matter Event, not a generic Incident",
            event_rules == {"incident.transaction_review@1"},
        ),
        (
            "Tension groups remain unresolved and visible",
            all(item.status == "unresolved" for item in plan.tensions),
        ),
        (
            "Latest packet version is the current review baseline; earlier "
            "versions stay historical",
            all(
                item.current_review_baseline != item.historical
                for item in plan.version_lineage
            )
            and any(item.current_review_baseline for item in plan.version_lineage),
        ),
        (
            "No approval or execution state advanced",
            report.reconciliation["approval_states_advanced"] == 0
            and report.reconciliation["execution_states_advanced"] == 0
            and report.import_writes == 0,
        ),
        (
            "Every mapping stays proposed with no reviewer recorded",
            all(item.mapping_status == "proposed" for item in plan.records)
            and all(item.reviewed_by is None for item in plan.records),
        ),
        (
            "Zero source changes proven by pre/post hash and mtime sweep",
            proof.zero_source_changes,
        ),
    )
    for label, passed in checks:
        mark = "x" if passed else " "
        add(f"- [{mark}] {label}")
    add("")
    add("## Human mapping review decision")
    add("")
    add("Status: pending. No mapping is approved by this artifact.")
    add("")
    add("- Reviewer: (to be completed by the human reviewer)")
    add("- Decision: approve mappings for structured import / request revision")
    add("- Decision date: (to be completed by the human reviewer)")
    add("")
    return "\n".join(lines)


def report_fingerprint(report: PilotDryRunReport) -> str:
    """Fingerprint of the report content; identical only for matching pins and clocks."""
    return hashlib.sha256(report.json().encode("utf-8")).hexdigest()
