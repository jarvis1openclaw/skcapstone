#!/usr/bin/env python3
"""Apply the approved embedding thresholds and exercise alias rollback (SKL-S3-04C).

Loads the SKL-S3-04A frozen evaluation dataset and the approved, versioned
qualification threshold set, reruns the SKL-S3-04B shadow comparison over the
two deterministic fixture routes, and applies every approved bound. The
verdict cites the exact measured value and the exact bound for every check on
every route.

The script then exercises the serving alias end to end: it binds the custom
route on a qualifying verdict, serves one frozen query through it, forces a
failing custom verdict through the leakage simulation seam, rolls the alias
back to the base BGE-M3 route, and serves the same query again through the
rolled-back route. Rollback is a real state transition, not a documented
intent, and the script exits nonzero unless every gate holds.

The committed embedders are deterministic hash fixtures standing in for the
candidate models. These numbers exercise and pin the qualification machinery
and never measure or qualify a real model.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "evals/retrieval/frozen-v1"

sys.path.insert(0, str(REPO_ROOT / "packages/retrieval/src"))

from sklegal_retrieval.embedding_alias import (  # noqa: E402
    AliasStatus,
    EmbeddingAliasRegistry,
)
from sklegal_retrieval.embedding_qualification import (  # noqa: E402
    THRESHOLDS_DEFAULT_PATH,
    EmbeddingQualificationVerdict,
    QualificationDecision,
    apply_qualification_thresholds,
    load_qualification_thresholds,
)
from sklegal_retrieval.evaluation_dataset import load_frozen_dataset  # noqa: E402
from sklegal_retrieval.shadow_comparison import (  # noqa: E402
    HARNESS_VERSION,
    ShadowRouteLabel,
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
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=THRESHOLDS_DEFAULT_PATH,
        help="approved threshold file (default: %(default)s)",
    )
    return parser.parse_args()


def print_verdict(verdict: EmbeddingQualificationVerdict) -> None:
    print(
        "qualification verdict:"
        f" decision {verdict.decision.value},"
        f" thresholds {verdict.thresholds_version}"
        f" ({verdict.thresholds_sha256[:16]}...),"
        f" harness {verdict.harness_version},"
        f" freeze {verdict.dataset_freeze_sha256[:16]}...,"
        f" k values {' '.join(str(value) for value in verdict.k_values)}"
    )
    for route in verdict.routes:
        print()
        print(f"route {route.route_label.value}:")
        print(f"  model id: {route.embedding_model_id}")
        print(f"  model revision: {route.embedding_model_revision}")
        print(f"  embedder kind: {route.embedder_kind}")
        print(f"  projection generation: {route.projection_generation}")
        print(f"  qualified: {route.qualified}")
        for check in route.checks:
            line = (
                f"  {check.metric}: {check.outcome.value}"
                f" ({check.measured} {check.operator} {check.bound})"
            )
            if check.waiver_reason:
                line += f" waiver: {check.waiver_reason}"
            print(line)


def print_history(registry: EmbeddingAliasRegistry) -> None:
    print()
    print(
        f"alias {registry.state.alias_name}:"
        f" status {registry.state.status.value},"
        f" thresholds {registry.state.thresholds_version}"
        f" ({registry.state.thresholds_sha256[:16]}...)"
    )
    for revision in registry.state.history:
        target = revision.binding.route_label.value if revision.binding else "none"
        print(
            f"  revision {revision.sequence}: {revision.action.value}"
            f" -> {target} (verdict {revision.verdict_sha256[:16]}...)"
        )
        print(f"    reason: {revision.reason}")


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"dataset root not found: {root}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        dataset = load_frozen_dataset(root)
        thresholds = load_qualification_thresholds(Path(args.thresholds))
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"INPUT FAILED: {error}", file=sys.stderr)
        return EXIT_FAILURE

    custom, base = build_fixture_candidates(dataset)
    clean_report = compare_shadow_routes(dataset, (custom, base))
    clean_verdict = apply_qualification_thresholds(clean_report, thresholds)
    print(
        "fixture embedder notice: the pinned embedders are deterministic hash"
        " fixtures. These numbers exercise the qualification machinery and"
        " never measure or qualify a real model."
    )
    print(
        f"clean comparison: harness {HARNESS_VERSION},"
        f" freeze {clean_report.dataset_freeze_sha256[:16]}...,"
        f" documents {len(dataset.documents)},"
        f" queries {len(dataset.queries)}"
    )
    print_verdict(clean_verdict)

    registry = EmbeddingAliasRegistry(thresholds)
    registry.register_candidate(custom)
    registry.register_candidate(base)
    query = dataset.queries[0]
    registry.bind_candidate(custom, clean_verdict)
    served_custom = registry.serve_query(query)
    print()
    print(
        f"alias served query {served_custom.query_id} through"
        f" {served_custom.route_label.value}"
        f" ({served_custom.embedding_model_id})"
        f" at revision {served_custom.revision_sequence}:"
        f" {len(served_custom.ranked_document_ids)} documents ranked"
    )

    leaking_custom, _ = build_fixture_candidates(dataset, leak_partitions=True)
    failing_report = compare_shadow_routes(dataset, (leaking_custom, base))
    failing_verdict = apply_qualification_thresholds(failing_report, thresholds)
    if failing_verdict.decision is not QualificationDecision.ROLLBACK_TO_BASE:
        print(
            "ROLLBACK DRILL FAILED: the leakage seam did not fail the custom"
            " route while the base route qualified;"
            f" decision was {failing_verdict.decision.value}",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    print()
    print(
        "rollback drill: forcing a failing custom verdict through the"
        " cross-partition leakage simulation seam"
    )
    print_verdict(failing_verdict)
    revision = registry.rollback(base, failing_verdict)
    served_base = registry.serve_query(query)
    print(
        f"alias served query {served_base.query_id} through"
        f" {served_base.route_label.value}"
        f" ({served_base.embedding_model_id})"
        f" at revision {served_base.revision_sequence}:"
        f" {len(served_base.ranked_document_ids)} documents ranked"
    )
    if served_base.route_label is not ShadowRouteLabel.BASE_BGE_M3:
        print(
            "ROLLBACK DRILL FAILED: serving did not switch to the base route",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    if served_base.revision_sequence != revision.sequence:
        print(
            "ROLLBACK DRILL FAILED: serving did not cite the rollback revision",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    print_history(registry)

    status = registry.state.status
    binding = registry.resolve()
    if status is not AliasStatus.ACTIVE or binding.route_label is not (
        ShadowRouteLabel.BASE_BGE_M3
    ):
        print(
            "ROLLBACK DRILL FAILED: the resolved binding is not the base route",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    print()
    print(
        "result: thresholds applied with exact citations on both fixture"
        " routes; the alias bound the qualifying custom route, then rolled"
        " back to base BGE-M3 on the failing custom verdict and served"
        " through it"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
