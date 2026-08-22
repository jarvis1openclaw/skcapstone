#!/usr/bin/env python3
"""Replay the SKL-S5-01B approved pilot import on a synthetic fixture.

Builds the shared synthetic HammerTime fixture tree in a temporary
directory, then runs the approved import lifecycle against the in-memory
pilot store: initial import, idempotent rerun, changed-source revision,
withdrawal, and disposable reset. The replay writes one machine-readable
evidence artifact and never touches a real HammerTime path, a real matter,
or a live database.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklegal_hammertime import HammerTimeReleaseAdapter, MatterAccessRequest
from sklegal_migration import (
    InMemoryPilotImportStore,
    MappingApproval,
    PilotImportPlan,
    reset_disposable_batch,
    run_approved_import,
    run_pilot_dry_run,
    withdraw_batch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "evidence" / "migration"
ARTIFACT_PREFIX = "SKL-S5-01B-APPROVED-IMPORT"
FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
SNAPSHOT = "fixture-snapshot-2099-01-02"
TENANT_ID = "a5b70000-0000-4000-8000-000000000101"
REVIEWER = "Synthetic Human Reviewer"
REVIEW_ARTIFACT = "SKL-S5-01A-PILOT-MAPPING-REVIEW-2099-01-02.md#approved"

sys.path.insert(0, str(REPO_ROOT))
from tests.support import hammertime_fixture as fixture  # noqa: E402


def _allow_all(request: MatterAccessRequest) -> bool:
    return True


def _approve(plan: PilotImportPlan) -> MappingApproval:
    return MappingApproval(
        import_batch_id=plan.import_batch_id,
        reviewer=REVIEWER,
        decided_at="2099-01-03T00:00:00Z",
        decision="approved",
        approved_idempotency_keys=tuple(
            record.idempotency_key for record in plan.records
        ),
        review_artifact=REVIEW_ARTIFACT,
    )


def run_replay() -> dict[str, Any]:
    """Execute the synthetic approved-import replay and return evidence."""
    with tempfile.TemporaryDirectory(prefix="sklegal-s501b-replay-") as tempdir:
        root = fixture.build_hammertime_fixture(Path(tempdir))
        adapter = HammerTimeReleaseAdapter(
            root=root,
            matter_authorizer=_allow_all,
            clock=lambda: FIXED_NOW,
        )
        store = InMemoryPilotImportStore()

        first_report = run_pilot_dry_run(
            adapter,
            source_snapshot=SNAPSHOT,
            problem_id=fixture.PROBLEM_ID,
            incident_id=fixture.INCIDENT_ID,
        )
        first = run_approved_import(
            first_report.plan,
            approval=_approve(first_report.plan),
            inventory=first_report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )
        rerun = run_approved_import(
            first_report.plan,
            approval=_approve(first_report.plan),
            inventory=first_report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )
        counts_after_rerun = dict(store.counts())

        incident_path = root / fixture.INCIDENT_RELATIVE
        incident_path.write_text(
            incident_path.read_text(encoding="utf-8") + "Additional synthetic note.\n",
            encoding="utf-8",
        )
        second_report = run_pilot_dry_run(
            adapter,
            source_snapshot=SNAPSHOT,
            problem_id=fixture.PROBLEM_ID,
            incident_id=fixture.INCIDENT_ID,
        )
        revision = run_approved_import(
            second_report.plan,
            approval=_approve(second_report.plan),
            inventory=second_report.pre_inventory,
            store=store,
            tenant_id=TENANT_ID,
        )
        revisions = sorted(
            (
                f"{row['target_type']}:{row['mapping_rule']}",
                int(row["revision"]),
            )
            for row in store.records.values()
        )

        withdrawn = withdraw_batch(
            store,
            second_report.plan.import_batch_id,
            withdrawn_at="2099-01-04T00:00:00Z",
        )
        deleted = reset_disposable_batch(store, second_report.plan.import_batch_id)
        counts_after_reset = dict(store.counts())

    return {
        "artifact": "SKL-S5-01B approved import replay (synthetic fixture)",
        "source_snapshot": SNAPSHOT,
        "tenant_id": TENANT_ID,
        "reviewer": REVIEWER,
        "review_artifact": REVIEW_ARTIFACT,
        "environment": "in-memory pilot store over the synthetic fixture tree",
        "inventoried_files": len(first_report.pre_inventory),
        "zero_source_changes": first_report.zero_source_changes,
        "first_import": first.to_dict(),
        "rerun": rerun.to_dict(),
        "counts_after_rerun": counts_after_rerun,
        "changed_source_revision": revision.to_dict(),
        "record_revisions": [
            {"target": target, "revision": number} for target, number in revisions
        ],
        "withdrawn": withdrawn,
        "disposable_reset_deleted": dict(deleted),
        "counts_after_reset": counts_after_reset,
        "invariants": {
            "rerun_created_no_duplicates": (
                rerun.rerun
                and rerun.records_created == 0
                and rerun.facts_created == 0
                and rerun.tensions_created == 0
                and rerun.states_created == 0
            ),
            "changed_source_created_new_revision": (
                revision.records_created == 1 and revision.records_suppressed == 1
            ),
            "negative_states_only": counts_after_rerun["states"]
            == 2 * len(first_report.plan.records),
            "rollback_removed_withdrawn_batch_rows": (
                deleted["records"] == revision.records_created
                and deleted["facts"] == revision.facts_created
                and deleted["source_files"] == revision.source_files_recorded
                and counts_after_reset["records"] == counts_after_rerun["records"]
            ),
            "withdrawn_targets_pinned": counts_after_reset["withdrawn_targets"] > 0,
            "batch_tombstones_preserved": counts_after_reset["batches"] == 2,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="directory for the evidence artifact",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="artifact date stamp YYYY-MM-DD (default: today, UTC)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_date = args.date or datetime.now(UTC).date().isoformat()
    evidence = run_replay()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = args.out_dir / f"{ARTIFACT_PREFIX}-{run_date}.json"
    artifact_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"artifact={artifact_path}")
    for name, passed in sorted(evidence["invariants"].items()):
        print(f"invariant {name}: {passed}")
    if not all(evidence["invariants"].values()):
        print("REPLAY INVARIANT FAILED; failing closed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
