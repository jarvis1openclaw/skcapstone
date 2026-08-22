#!/usr/bin/env python3
"""Freeze or verify the hash-frozen retrieval evaluation dataset (SKL-S3-04A).

verify (default) checks that documents.json, queries.json, judgments.json,
and manifest.json under the dataset root are byte-identical to the hashes
pinned in the manifest, that the freeze hash binds them, and that every
dataset leakage invariant holds.

emit writes manifest.json from the current file bytes. Emitting a new
manifest is a dataset revision: it must be reviewed and committed together
with the changed records.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "evals/retrieval/frozen-v1"

sys.path.insert(0, str(REPO_ROOT / "packages/retrieval/src"))

from sklegal_retrieval.evaluation_dataset import (  # noqa: E402
    DATASET_SCHEMA,
    DATASET_VERSION,
    DOCUMENTS_FILE,
    JUDGMENTS_FILE,
    MANIFEST_FILE,
    QUERIES_FILE,
    DatasetManifest,
    compute_file_sha256,
    compute_freeze_sha256,
    dataset_invariant_violations,
    load_frozen_dataset,
)

EXIT_OK = 0
EXIT_FAILURE = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="frozen dataset directory (default: %(default)s)",
    )
    parser.add_argument(
        "--emit",
        action="store_true",
        help="write manifest.json from the current dataset bytes",
    )
    parser.add_argument(
        "--frozen-at",
        default="2026-08-22T20:48:34Z",
        help="UTC freeze timestamp written by --emit (default: %(default)s)",
    )
    parser.add_argument(
        "--card",
        default="e1bc4552",
        help="board card the dataset is frozen under (default: %(default)s)",
    )
    parser.add_argument(
        "--task",
        default="SKL-S3-04A",
        help="task key for the manifest (default: %(default)s)",
    )
    return parser.parse_args()


def build_manifest(root: Path, args: argparse.Namespace) -> DatasetManifest:
    documents_sha256 = compute_file_sha256(root / DOCUMENTS_FILE)
    queries_sha256 = compute_file_sha256(root / QUERIES_FILE)
    judgments_sha256 = compute_file_sha256(root / JUDGMENTS_FILE)
    freeze_sha256 = compute_freeze_sha256(
        schema=DATASET_SCHEMA,
        dataset_version=DATASET_VERSION,
        documents_sha256=documents_sha256,
        queries_sha256=queries_sha256,
        judgments_sha256=judgments_sha256,
    )
    payload = {
        "schema": DATASET_SCHEMA,
        "card": args.card,
        "task": args.task,
        "parent_task": "SKL-S3-04",
        "amendment": "AMENDMENT-SKL-S2-10",
        "dataset_version": DATASET_VERSION,
        "frozen_at": args.frozen_at,
        "paired_document": "docs/development/RETRIEVAL-EVAL-FROZEN-DATASET.md",
        "documents_sha256": documents_sha256,
        "queries_sha256": queries_sha256,
        "judgments_sha256": judgments_sha256,
        "freeze_sha256": freeze_sha256,
        "synthetic_tenant_ids": [
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        ],
    }
    from pydantic import TypeAdapter

    return TypeAdapter(DatasetManifest).validate_json(json.dumps(payload))


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"dataset root not found: {root}", file=sys.stderr)
        return EXIT_FAILURE
    if args.emit:
        manifest = build_manifest(root, args)
        (root / MANIFEST_FILE).write_text(
            json.dumps(manifest.model_dump(mode="json", by_alias=True), indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {root / MANIFEST_FILE}")
    try:
        dataset = load_frozen_dataset(root)
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"FROZEN DATASET FAILED: {error}", file=sys.stderr)
        return EXIT_FAILURE
    violations = dataset_invariant_violations(dataset)
    if violations:
        for item in violations:
            print(f"INVARIANT VIOLATION: {item}", file=sys.stderr)
        return EXIT_FAILURE
    print(
        "frozen dataset verified:"
        f" {len(dataset.documents)} documents,"
        f" {len(dataset.queries)} queries,"
        f" {len(dataset.judgments)} judgments,"
        f" freeze {dataset.manifest.freeze_sha256[:16]}..."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
