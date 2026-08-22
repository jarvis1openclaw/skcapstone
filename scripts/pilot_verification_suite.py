#!/usr/bin/env python3
"""Produce the SKL-S5-01C pilot verification suite replay evidence.

Assembles the full pilot verification stack over the shared synthetic
HammerTime fixture tree in a temporary directory: the SKL-S5-01A dry run,
the SKL-S5-01B approved import into the in-memory pilot store, the
SKL-S4-02 workspace projection, and the SKL-S2-05 materialized corpus
registry built through the real projector, deep reconciler, and bounded
health reader. The suite then runs twice over the identical pinned
inputs: the first run is recorded as the original and the second run is
verified as a deterministic replay of it. The script is read-only for
every real system: no HammerTime path, no live Tenant, no database, and
no external action is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sklegal_api.pilot_verification import run_pilot_verification
from sklegal_api.workspace import InMemoryWorkspaceReadStore
from sklegal_api.workspace_pilot import project_pilot_matter_workspace
from sklegal_hammertime import HammerTimeReleaseAdapter, MatterAccessRequest
from sklegal_migration import (
    InMemoryPilotImportStore,
    MappingApproval,
    run_approved_import,
    run_pilot_dry_run,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "evidence" / "migration"
ARTIFACT_PREFIX = "SKL-S5-01C-PILOT-VERIFICATION-SUITE"
FIXED_NOW = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)
SNAPSHOT = "fixture-snapshot-2099-01-02"
TENANT_ID = UUID("a5b70000-0000-4000-8000-000000000101")
CLIENT_ID = UUID("a5b70000-0000-4000-8000-000000000202")
MEMBER_ID = UUID("a5b70000-0000-4000-8000-000000000303")
ORIGINAL_RUN_AT = datetime(2099, 1, 5, 7, 0, 0, tzinfo=UTC)
REPLAY_RUN_AT = datetime(2099, 1, 6, 8, 30, 0, tzinfo=UTC)
REVIEWER = "Synthetic Human Reviewer"
REVIEW_ARTIFACT = "SKL-S5-01A-PILOT-MAPPING-REVIEW-2099-01-02.md#approved"

sys.path.insert(0, str(REPO_ROOT))
from tests.support import hammertime_fixture as fixture  # noqa: E402
from tests.support import pilot_corpus  # noqa: E402


def _allow_all(request: MatterAccessRequest) -> bool:
    return True


def _approve(plan: Any) -> MappingApproval:
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


def _build_stack() -> dict[str, Any]:
    """Run the dry run, approved import, projection, and corpus state."""

    with tempfile.TemporaryDirectory(prefix="sklegal-s501c-suite-") as tempdir:
        root = fixture.build_hammertime_fixture(Path(tempdir))
        adapter = HammerTimeReleaseAdapter(
            root=root,
            matter_authorizer=_allow_all,
            clock=lambda: FIXED_NOW,
        )
        report = run_pilot_dry_run(
            adapter,
            source_snapshot=SNAPSHOT,
            problem_id=fixture.PROBLEM_ID,
            incident_id=fixture.INCIDENT_ID,
        )

    import_store = InMemoryPilotImportStore()
    import_result = run_approved_import(
        report.plan,
        approval=_approve(report.plan),
        inventory=report.pre_inventory,
        store=import_store,
        tenant_id=str(TENANT_ID),
    )
    workspace_store = InMemoryWorkspaceReadStore()
    view = project_pilot_matter_workspace(
        plan=report.plan,
        store=import_store,
        workspace_store=workspace_store,
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_display_name="Synthetic Pilot Client",
        matter_status="open",
        matter_summary="Synthetic pilot matter summary for the verification suite.",
        member_principal_ids=(MEMBER_ID,),
    )
    corpus = pilot_corpus.build_pilot_corpus_state(
        tenant_id=TENANT_ID,
        release_id=fixture.RELEASE_ID,
        source_pins=pilot_corpus.pilot_source_pins(report.plan),
    )
    return {
        "report": report,
        "import_result": import_result,
        "import_store": import_store,
        "workspace_store": workspace_store,
        "view": view,
        "corpus": corpus,
    }


def _run(stack: dict[str, Any], **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "report": stack["report"],
        "approval": _approve(stack["report"].plan),
        "import_result": stack["import_result"],
        "import_store": stack["import_store"],
        "workspace_view": stack["view"],
        "workspace_store": stack["workspace_store"],
        "tenant_id": TENANT_ID,
        "member_principal_ids": (MEMBER_ID,),
        "release_id": fixture.RELEASE_ID,
        "registry": stack["corpus"].registry,
        "reconciliation": stack["corpus"].reconciliation,
        "health": stack["corpus"].health,
    }
    values.update(overrides)
    return run_pilot_verification(**values)


def run_suite() -> dict[str, Any]:
    """Build the stack, run the suite, and replay it deterministically."""

    stack = _build_stack()
    original = _run(stack, observed_at=ORIGINAL_RUN_AT)
    replayed = _run(stack, replay=original, observed_at=REPLAY_RUN_AT)
    return {
        "suite": replayed.model_dump(mode="json"),
        "original_run_id": original.run_id,
        "original_generated_at": original.generated_at,
        "replayed_generated_at": replayed.generated_at,
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
    evidence = run_suite()
    suite = evidence["suite"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = args.out_dir / f"{ARTIFACT_PREFIX}-{run_date}.json"
    artifact_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"artifact={artifact_path}")
    print(f"run_id={suite['run_id']}")
    failed = [key for key, value in suite["checks"].items() if not value]
    print(f"checks_total={len(suite['checks'])}")
    print(f"checks_failed={len(failed)}")
    for key in failed:
        print(f"failed_check={key}")
    if not suite["passed"]:
        print("PILOT VERIFICATION SUITE FAILED; failing closed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
