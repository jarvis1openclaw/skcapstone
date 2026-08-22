#!/usr/bin/env python3
"""Execute the SKL-S5-01A read-only pilot dry run and write evidence.

Runs the pilot TDD phase A inventory plus the S2-02 importer dry run
against the Liberty Auto pilot source set through the read-only
HammerTime adapter, then writes three evidence artifacts:

- a sha256 source-hash manifest
- a machine-readable dry-run report (JSON)
- a human mapping-review artifact (Markdown)

The script never writes to HammerTime and exits non-zero (fail closed)
when the pre/post comparison detects any source change.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sklegal_hammertime import (
    ROOT_ENV_VAR,
    HammerTimeReleaseAdapter,
    MatterAccessRequest,
)
from sklegal_migration import (
    DEFAULT_INCIDENT_ID,
    DEFAULT_PROBLEM_ID,
    render_mapping_review_markdown,
    render_sha256_manifest,
    run_pilot_dry_run,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "evidence" / "migration"
ARTIFACT_PREFIX = "SKL-S5-01A-PILOT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"HammerTime corpus root (default: ${ROOT_ENV_VAR})",
    )
    parser.add_argument(
        "--problem-id",
        default=DEFAULT_PROBLEM_ID,
        help="legacy matter container id for the pilot",
    )
    parser.add_argument(
        "--incident-id",
        default=DEFAULT_INCIDENT_ID,
        help="legacy matter activity id for the pilot",
    )
    parser.add_argument(
        "--source-snapshot",
        default=None,
        help="snapshot label pinned into the dry run (default: date-stamped)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="directory for evidence artifacts",
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
    source_snapshot = args.source_snapshot or (f"hammertime-working-tree-{run_date}")

    allowed = {args.problem_id, args.incident_id}

    def pilot_authorizer(request: MatterAccessRequest) -> bool:
        """Narrow pilot capability: only the two pilot legacy ids."""
        if request.legacy_id not in allowed:
            return False
        return request.parent_legacy_id is None or request.parent_legacy_id in allowed

    adapter = HammerTimeReleaseAdapter(
        root=args.root,
        matter_authorizer=pilot_authorizer,
    )
    report = run_pilot_dry_run(
        adapter,
        source_snapshot=source_snapshot,
        problem_id=args.problem_id,
        incident_id=args.incident_id,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / f"{ARTIFACT_PREFIX}-SOURCE-HASHES-{run_date}.sha256"
    report_path = args.out_dir / f"{ARTIFACT_PREFIX}-DRY-RUN-{run_date}.json"
    review_path = args.out_dir / f"{ARTIFACT_PREFIX}-MAPPING-REVIEW-{run_date}.md"
    manifest_path.write_text(render_sha256_manifest(report), encoding="utf-8")
    report_path.write_text(report.json(), encoding="utf-8")
    review_path.write_text(
        render_mapping_review_markdown(report),
        encoding="utf-8",
    )

    print(f"run_id={report.run_id}")
    print(f"import_batch_id={report.plan.import_batch_id}")
    print(f"files_hashed={report.change_proof.pre_count}")
    print(f"zero_source_changes={report.zero_source_changes}")
    print(f"import_writes={report.import_writes}")
    print(f"manifest={manifest_path}")
    print(f"report={report_path}")
    print(f"review={review_path}")
    if not report.zero_source_changes:
        print("SOURCE CHANGE DETECTED; failing closed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
