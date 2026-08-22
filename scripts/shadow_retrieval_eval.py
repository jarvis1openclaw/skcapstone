#!/usr/bin/env python3
"""Run the deterministic shadow comparison harness (SKL-S3-04B).

Loads the SKL-S3-04A frozen evaluation dataset, builds the two deterministic
fixture shadow projection generations (custom legal route and base BGE-M3
route), executes every frozen query through the PostgreSQL retrieval plane
adapter, and prints the deterministic metric set: Recall@k, nDCG@k, MRR,
citation accuracy, latency, and leakage.

The committed embedders are deterministic hash fixtures standing in for the
candidate models. These numbers exercise and pin the harness itself. They
never measure, qualify, promote, or demote any real model; binding the real
models behind the provider-neutral gateway is a later qualification step, and
approval thresholds stay a human decision.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "evals/retrieval/frozen-v1"

sys.path.insert(0, str(REPO_ROOT / "packages/retrieval/src"))

from sklegal_retrieval.evaluation_dataset import load_frozen_dataset  # noqa: E402
from sklegal_retrieval.shadow_comparison import (  # noqa: E402
    HARNESS_VERSION,
    build_fixture_candidates,
    compare_shadow_routes,
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
    return parser.parse_args()


def format_k_vector(values: tuple[float, ...]) -> str:
    return " ".join(f"{value:.4f}" for value in values)


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"dataset root not found: {root}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        dataset = load_frozen_dataset(root)
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"FROZEN DATASET FAILED: {error}", file=sys.stderr)
        return EXIT_FAILURE
    candidates = build_fixture_candidates(dataset)
    report = compare_shadow_routes(dataset, candidates)
    print(
        "shadow comparison report:"
        f" harness {HARNESS_VERSION},"
        f" freeze {report.dataset_freeze_sha256[:16]}...,"
        f" documents {len(dataset.documents)},"
        f" queries {len(dataset.queries)},"
        f" judgments {len(dataset.judgments)}"
    )
    print(
        "fixture embedder notice: the pinned embedders are deterministic hash"
        " fixtures. These numbers exercise the harness and never measure or"
        " qualify a real model."
    )
    print(f"k values: {' '.join(str(value) for value in report.k_values)}")
    for route in report.routes:
        pins = route.route
        print()
        print(f"route {pins.label.value}:")
        print(f"  model id: {pins.vector.embedding_model_id}")
        print(f"  model revision: {pins.vector.embedding_model_revision}")
        print(f"  embedder kind: {pins.embedder_kind}")
        print(f"  projection generation: {route.projection_generation}")
        print(f"  macro recall@k:  {format_k_vector(route.macro_recall_at_k)}")
        print(f"  macro ndcg@k:    {format_k_vector(route.macro_ndcg_at_k)}")
        print(f"  macro mrr:       {route.macro_mrr:.4f}")
        print(f"  macro citation accuracy: {route.macro_citation_accuracy:.4f}")
        print(f"  macro latency mean (s): {route.macro_latency_mean_seconds:.6f}")
        print(f"  max latency (s):        {route.max_latency_seconds:.6f}")
        print(f"  leakage count:  {route.leakage_count}")
        for metric in route.partitions:
            print(
                f"    partition tenant={metric.tenant_id}"
                f" matter={metric.matter_id}"
                f" queries={metric.queries_evaluated}"
                f" recall_eligible={metric.recall_eligible_queries}"
                f" citation_eligible={metric.citation_eligible_queries}"
                f" recall@k=[{format_k_vector(metric.recall_at_k)}]"
                f" ndcg@k=[{format_k_vector(metric.ndcg_at_k)}]"
                f" mrr={metric.mrr:.4f}"
                f" citation_accuracy={metric.citation_accuracy:.4f}"
                f" leakage={len(metric.leakage_codes)}"
            )
    for route in report.routes:
        if route.leakage_count:
            print()
            print(f"LEAKAGE DETECTED on route {route.route.label.value}")
            for metric in route.partitions:
                for code in metric.leakage_codes:
                    print(f"  {code}")
            return EXIT_FAILURE
    print()
    print(
        "result: harness verified deterministic over the frozen dataset;"
        " no cross-partition or privilege leakage detected"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
