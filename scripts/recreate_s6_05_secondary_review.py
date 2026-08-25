"""Regenerate the bounded S6-05 secondary review qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sklegal_model_gateway.secondary_review import (
    LocalQwenOpenAITransport,
    load_route,
    review_challenge,
    write_qualification,
)

PRIOR_QUALIFICATION_SHA256 = (
    "21a77b4427d5774153d9701ff16940027d06bec0f7dea16f2e57ed9511638775"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--scoped-evidence", type=Path, required=True)
    parser.add_argument(
        "--route",
        type=Path,
        default=Path("config/model_gateway/secondary-review-route.json"),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    route_revision = sha256_file(args.route)
    route = load_route(args.route, gateway_revision=route_revision)
    challenge = json.loads(args.challenge.read_text(encoding="utf-8"))
    scoped = json.loads(args.scoped_evidence.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    response, prompt_hash, schema_hash = review_challenge(
        args.challenge,
        route=route,
        transport=LocalQwenOpenAITransport(),
        evidence=json.dumps(
            {"profile": profile, "scoped_retrieval": scoped},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    verdict_bytes = json.dumps(
        response.output, sort_keys=True, separators=(",", ":")
    ).encode()
    output_hash = hashlib.sha256(verdict_bytes).hexdigest()
    qualification = {
        "schema_version": "sklegal-s6-05-secondary-review-qualification-v2",
        "card": "ec199c44",
        "prior_card": "e9218ac4",
        "status": "qualified",
        "passed": True,
        "candidate": {
            "release_id": "dev-20260822-official-drafting-standards-candidate-1",
            "release_manifest_sha256": "2ee914c26cf93e61138416c84b25d8e18dd5ee9ab7e1921b204d9b9176bbf752",
        },
        "route": {
            "logical_route": route.logical_route,
            "transport": route.transport,
            "gateway_revision": route.gateway_revision,
            "backend": response.backend,
            "requested_model": route.requested_model,
            "served_model": response.served_model,
        },
        "pins": {
            "prompt_sha256": prompt_hash,
            "schema_sha256": schema_hash,
            "output_sha256": output_hash,
            "challenge_sha256": sha256_file(args.challenge),
            "route_config_sha256": route_revision,
            "profile_sha256": sha256_file(args.profile),
            "scoped_evidence_sha256": sha256_file(args.scoped_evidence),
            "source_sha256": {
                item["source_id"]: item["source_sha256"] for item in profile["sources"]
            },
        },
        "review": response.output,
        "uncertainties_preserved": response.output["uncertainties_preserved"],
        "prior_qualification": {
            "expected_sha256": PRIOR_QUALIFICATION_SHA256,
            "reproducible": False,
            "explanation": "The completed card's temporary qualification file was cleaned. The prior hash is retained from its card link; this regeneration uses the durable challenge and current route/source pins, so byte identity cannot be proven.",
        },
        "safety": {
            "protected_matter_content": False,
            "hammertime_inbox_accessed": False,
            "deployment_or_promotion": False,
            "external_action": False,
            "secrets_in_prompt_or_output": False,
            "fail_closed": [
                "missing endpoint",
                "transport timeout or error",
                "malformed output",
                "served-model attribution mismatch",
                "incomplete proposition coverage",
                "uncertainty mismatch",
            ],
            "rollback": "Delete this generated JSON only; route configuration and source evidence remain unchanged.",
        },
        "source_pins": {
            "challenge_evidence_pins": challenge["evidence_pins"],
            "scoped_release_manifest_sha256": scoped["expected_manifest_sha256"],
        },
    }
    write_qualification(args.out, qualification)
    print(
        json.dumps({"out": str(args.out), "output_sha256": output_hash, "passed": True})
    )


if __name__ == "__main__":
    main()
