"""Deterministic qualification report for the recorded official drafting candidate.

Read-only against the HammerTime root. Follows only the pinned candidate
manifest paths through the read-only adapter, evaluates the typed scoped
retrieval evidence, and produces one typed report that combines:

- the bounded candidate inspection verdict,
- the scoped retrieval evidence bundle,
- the full fail-closed release qualification verdict,
- the exact immutable evidence pins (manifest, aliases, profile, rights,
  completion, core principles, review challenge and verdict).

It never promotes, rolls back, mutates aliases, or writes into the HammerTime
root. Genuinely pending gates (deep health, secondary review, guarded
promotion and rollback receipts) are reported as typed findings, not
assumptions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (
    REPO_ROOT / "packages/domain/src",
    REPO_ROOT / "packages/connectors/hammertime/src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sklegal_hammertime import (  # noqa: E402
    AliasRoundTripEvidence,
    HammerTimeReleaseAdapter,
    OfficialDraftingCandidateInspector,
    OfficialDraftingReleaseQualifier,
    OfficialDraftingReleaseRequest,
    OfficialDraftingSource,
    ProjectionQualificationEvidence,
    RetrievalQuerySpec,
    RetrievalSourceExpectation,
    SecondaryReviewEvidence,
    SourceRightsState,
)
from sklegal_hammertime.scoped_retrieval import (  # noqa: E402
    OfficialDraftingScopedRetrievalRunner,
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"evidence file must hold a JSON object: {path}")
    return payload


def require_sha256(path: Path, expected: str, label: str) -> str:
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(
            f"{label} pin mismatch: expected {expected}, observed {observed}"
        )
    return observed


class ReportError(ValueError):
    """Typed report failure; the run is blocked, not guessed."""


def build_report(
    *,
    root: Path,
    evidence_path: Path,
    profile_path: Path,
    rights_path: Path,
    completion_path: Path,
    core_principles_path: Path,
    review_challenge_path: Path,
    review_verdict_path: Path,
    now: datetime,
) -> dict[str, Any]:
    bundle = load_json(evidence_path)
    release_id = str(bundle["release_id"])
    expected_manifest_sha256 = str(bundle["expected_manifest_sha256"])
    expected_target = str(bundle["expected_target"])
    expected_source_count = int(bundle["expected_source_count"])

    adapter = HammerTimeReleaseAdapter(root=root, clock=lambda: now)

    # 1. Bounded candidate inspection over pinned paths only.
    inspector = OfficialDraftingCandidateInspector(adapter)
    candidate_report = inspector.inspect(
        release_id=release_id,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_target=expected_target,
        expected_source_count=expected_source_count,
    )

    # 2. Scoped retrieval over the same pinned chunks.
    profile_pin = bundle["profile_pin"]
    require_sha256(profile_path, str(profile_pin["sha256"]), "style profile")
    profile = load_json(profile_path)
    profile_sources = {
        str(source["source_id"]): source for source in profile.get("sources", [])
    }
    decisions = bundle["currentness_decisions"]
    if set(profile_sources) != set(decisions):
        raise ReportError(
            "currentness decisions must cover exactly the profile source set"
        )
    queries = tuple(
        RetrievalQuerySpec(
            query_id=str(query["query_id"]),
            terms=tuple(str(term) for term in query["terms"]),
            expected_source_ids=tuple(
                str(item) for item in query["expected_source_ids"]
            ),
            conflict_source_ids=tuple(
                str(item) for item in query.get("conflict_source_ids", ())
            ),
            derivation=str(query["derivation"]),
            derivation_sha256=str(profile_pin["sha256"]),
        )
        for query in bundle["queries"]
    )
    expectations = tuple(
        RetrievalSourceExpectation(
            source_id=str(source_id),
            issuing_body=str(profile_sources[source_id]["issuing_body"]),
            version=str(profile_sources[source_id]["version"]),
            scope=str(profile_sources[source_id]["scope"]),
            source_sha256=str(profile_sources[source_id]["source_sha256"]),
            currentness=str(decisions[source_id]["currentness"]),
            presented_as_current=bool(decisions[source_id]["presented_as_current"]),
        )
        for source_id in sorted(profile_sources)
    )
    for source_id, decision in decisions.items():
        if str(profile_sources[source_id]["currentness"]) != str(
            decision["currentness"]
        ):
            raise ReportError(
                f"currentness text for {source_id} differs from the pinned profile"
            )
    retrieval_bundle = OfficialDraftingScopedRetrievalRunner(adapter).run(
        release_id=release_id,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_target=expected_target,
        queries=queries,
        sources=expectations,
    )

    # 3. Rights and completion evidence, hash-pinned to the manifest.
    manifest = adapter.get_release_manifest(release_id)
    manifest_evidence = manifest.raw.get("official_drafting_batch", {}).get(
        "evidence", {}
    )

    def manifest_evidence_pin(key: str) -> tuple[str, str]:
        entry = manifest_evidence.get(key)
        if not isinstance(entry, dict) or "path" not in entry or "sha256" not in entry:
            raise ReportError(f"manifest lacks evidence pin {key!r}")
        return str(entry["path"]), str(entry["sha256"])

    require_sha256(
        rights_path, manifest_evidence_pin("source_rights")[1], "source rights review"
    )
    rights = load_json(rights_path)
    batch_sources = manifest.raw["official_drafting_batch"]["sources"]
    batch_source_ids = {str(source["source_id"]) for source in batch_sources}
    cleared = {str(item) for item in rights.get("cleared_for_internal_ingest", [])}
    quarantine = list(rights.get("rights_quarantine", []))
    if batch_source_ids != cleared or quarantine:
        raise ReportError(
            "rights review must clear exactly the batch source set with no "
            "rights quarantine"
        )
    require_sha256(
        completion_path, manifest_evidence_pin("completion")[1], "completion evidence"
    )
    completion = load_json(completion_path)
    require_sha256(
        core_principles_path,
        manifest_evidence_pin("core_principles")[1],
        "core principles",
    )

    # 4. Runtime alias state, read-only.
    aliases_snapshot = adapter.get_runtime_aliases()
    alias = aliases_snapshot.aliases.get(expected_target)
    if alias is None or alias.current is None or alias.previous is None:
        raise ReportError(f"runtime alias {expected_target!r} lacks both bindings")
    prior_current = alias.current.release_id

    # 5. Secondary review pins (recorded, not invoked).
    prompt_sha256 = sha256_file(review_challenge_path)
    output_sha256 = sha256_file(review_verdict_path)

    # 6. Full fail-closed qualification request.
    manifest_sources = {str(source["source_id"]): source for source in batch_sources}
    snapshot_files = {
        str(item.get("source_id", "")): item
        for item in (manifest.decomposed_snapshot or {}).get("files", [])
    }
    sources = tuple(
        OfficialDraftingSource(
            source_id=str(source_id),
            issuing_body=str(profile_sources[source_id]["issuing_body"]),
            version=str(profile_sources[source_id]["version"]),
            scope=str(profile_sources[source_id]["scope"]),
            normalized_path=str(manifest_sources[source_id]["normalized_path"]),
            source_sha256=str(manifest_sources[source_id]["source_sha256"]),
            normalized_sha256=str(manifest_sources[source_id]["normalized_sha256"]),
            decomposition_id=(
                str(snapshot_files[source_id]["path"]).rsplit("/", 1)[-1]
            ).removesuffix(".json"),
            decomposition_sha256=str(snapshot_files[source_id]["sha256"]),
            rights_state=SourceRightsState.VERIFIED,
            superseded=bool(decisions[source_id].get("superseded", False)),
        )
        for source_id in sorted(manifest_sources)
    )
    vector_binding = str(completion.get("vector_projection", {}).get("collection", ""))
    graph_binding = str(completion.get("graph_projection", {}).get("graph", ""))
    if (
        vector_binding != manifest.vector_collection
        or graph_binding != manifest.graph_name
    ):
        raise ReportError("completion projection bindings differ from the manifest")
    request = OfficialDraftingReleaseRequest(
        release_id=release_id,
        release_manifest_sha256=expected_manifest_sha256,
        target=expected_target,
        sources=sources,
        retrieval_checks=retrieval_bundle.checks,
        projection=ProjectionQualificationEvidence(
            release_id=release_id,
            release_manifest_sha256=expected_manifest_sha256,
            lexical_release_id=release_id,
            vector_release_id=release_id,
            graph_release_id=release_id,
            deep_health_passed=False,
            reconciliation_complete=candidate_report.status.name == "QUALIFIED",
        ),
        secondary_review=SecondaryReviewEvidence(
            logical_route="sklegal.local-corpus-secondary-review",
            served_model="local-qwen3.8-review-pending",
            prompt_sha256=prompt_sha256,
            output_sha256=output_sha256,
            reviewed_at=now,
            passed=False,
        ),
        alias_round_trip=AliasRoundTripEvidence(
            target=expected_target,
            prior_current_release_id=prior_current,
            promoted_release_id=release_id,
            promoted_previous_release_id=prior_current,
            promotion_succeeded=False,
            rollback_succeeded=False,
            rollback_current_release_id=prior_current,
            rollback_previous_release_id=release_id,
        ),
    )
    qualifier_report = OfficialDraftingReleaseQualifier(adapter).qualify(request)

    report = {
        "schema_version": "sklegal-official-drafting-qualification-report-v1",
        "card": "0ad49216",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "candidate": {
            "release_id": release_id,
            "release_target": expected_target,
            "release_manifest_sha256": expected_manifest_sha256,
        },
        "evidence_pins": {
            "release_manifest": {
                "path": f"json/releases/corpus-release-{release_id}.json",
                "sha256": manifest.pin.content_sha256,
            },
            "runtime_aliases": {
                "path": "json/state/runtime-aliases.json",
                "sha256": aliases_snapshot.pin.content_sha256,
                "dev_current_release_id": prior_current,
                "dev_previous_release_id": alias.previous.release_id,
            },
            "style_profile": profile_pin,
            "core_principles": manifest_evidence.get("core_principles"),
            "source_rights": manifest_evidence.get("source_rights"),
            "completion_evidence": manifest_evidence.get("completion"),
            "scoped_retrieval_evidence": {
                "path": str(evidence_path),
                "sha256": sha256_file(evidence_path),
            },
            "secondary_review_challenge": {
                "path": str(review_challenge_path),
                "sha256": prompt_sha256,
            },
            "secondary_review_verdict": {
                "path": str(review_verdict_path),
                "sha256": output_sha256,
            },
        },
        "candidate_inspection": candidate_report.model_dump(mode="json"),
        "scoped_retrieval": retrieval_bundle.model_dump(mode="json"),
        "full_qualification": qualifier_report.model_dump(mode="json"),
        "pending_gates": [
            "shallow and deep corpus validation (OneDrive-backed corpus path)",
            "secondary local Qwen3.8 review against the exact candidate",
            "guarded dev promotion with current and previous alias pins",
            "rollback and re-promotion receipts through HammerTime release tooling",
        ],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--rights", required=True, type=Path)
    parser.add_argument("--completion", required=True, type=Path)
    parser.add_argument("--core-principles", required=True, type=Path)
    parser.add_argument("--review-challenge", required=True, type=Path)
    parser.add_argument("--review-verdict", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--now", type=str, default=None)
    args = parser.parse_args(argv)
    now = (
        datetime.fromisoformat(args.now)
        if args.now
        else datetime.now(UTC).replace(microsecond=0)
    )
    report = build_report(
        root=args.root,
        evidence_path=args.evidence,
        profile_path=args.profile,
        rights_path=args.rights,
        completion_path=args.completion,
        core_principles_path=args.core_principles,
        review_challenge_path=args.review_challenge,
        review_verdict_path=args.review_verdict,
        now=now,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    inspection = report["candidate_inspection"]
    qualification = report["full_qualification"]
    print(
        "candidate_inspection: "
        f"{inspection['status']} sources={inspection['source_count']} "
        f"decompositions={inspection['decomposition_count']} "
        f"verified={len(inspection['verified_artifact_sha256'])}"
    )
    retrieval = report["scoped_retrieval"]
    print(
        "scoped_retrieval: "
        f"queries={len(retrieval['checks'])} "
        f"findings={len(retrieval['findings'])}"
    )
    print(
        "full_qualification: "
        f"{qualification['status']} findings="
        f"{[finding['code'] for finding in qualification['findings']]}"
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
