from __future__ import annotations

import json
from pathlib import Path

import pytest
from sklegal_model_gateway.secondary_review import (
    EXPECTED_UNCERTAINTIES,
    ReviewResponse,
    ReviewRoute,
    ReviewRouteError,
    review_challenge,
    rollback_qualification,
    validate_verdict,
)

ROOT = Path(__file__).parents[1]
CHALLENGE = (
    ROOT / "docs/evidence/corpus/SKL-S6-05-SECONDARY-REVIEW-CHALLENGE-2026-08-22.json"
)


def valid_output() -> dict[str, object]:
    challenge = json.loads(CHALLENGE.read_text())
    return {
        "verdicts": [
            {
                "proposition_id": item["id"],
                "decision": "upheld",
                "reasoning": "The pinned evidence supports the proposition within its stated scope.",
                "citations": item["source_ids"] or ["pinned challenge schema"],
            }
            for item in challenge["propositions"]
        ],
        "overall": "pass_with_uncertainty",
        "uncertainties_preserved": list(EXPECTED_UNCERTAINTIES),
    }


def test_exact_challenge_is_eight_propositions_and_five_uncertainties() -> None:
    challenge = json.loads(CHALLENGE.read_text())
    assert len(challenge["propositions"]) == 8
    assert len(EXPECTED_UNCERTAINTIES) == 5


def test_valid_output_is_accepted() -> None:
    assert validate_verdict(
        valid_output(), proposition_ids=[f"P{i}" for i in range(1, 9)]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["verdicts"].pop(),
        lambda value: value["verdicts"][0].update(decision="bad"),
        lambda value: value["uncertainties_preserved"].pop(),
        lambda value: value.update(overall="fail"),
    ],
)
def test_malformed_output_fails_closed(mutation) -> None:
    value = valid_output()
    mutation(value)
    with pytest.raises(ReviewRouteError):
        validate_verdict(value, proposition_ids=[f"P{i}" for i in range(1, 9)])


def test_missing_route_and_timeout_are_transport_failures() -> None:
    class MissingTransport:
        def complete(self, **kwargs):
            raise ReviewRouteError("review endpoint environment reference is unset")

    class TimeoutTransport:
        def complete(self, **kwargs):
            raise ReviewRouteError("local review transport failed: timeout")

    route = ReviewRoute(
        logical_route="sklegal.local-corpus-secondary-review",
        transport="direct_qwen",
        endpoint_env="MISSING",
        gateway_revision="a" * 64,
        backend="llama.cpp-local",
        requested_model="qwen3.8-27b-huihui-abliterated-q4_k_m",
        served_model="qwen3.8-27b-huihui-abliterated-q4_k_m",
    )
    with pytest.raises(ReviewRouteError):
        review_challenge(CHALLENGE, route=route, transport=MissingTransport())
    with pytest.raises(ReviewRouteError, match="timeout"):
        review_challenge(CHALLENGE, route=route, transport=TimeoutTransport())


def test_served_model_mismatch_fails_closed() -> None:
    class MismatchTransport:
        def complete(self, **kwargs):
            return ReviewResponse(
                output=valid_output(),
                served_model="other-model",
                backend="llama.cpp-local",
            )

    route = ReviewRoute(
        logical_route="sklegal.local-corpus-secondary-review",
        transport="direct_qwen",
        endpoint_env="TEST",
        gateway_revision="a" * 64,
        backend="llama.cpp-local",
        requested_model="qwen3.8-27b-huihui-abliterated-q4_k_m",
        served_model="qwen3.8-27b-huihui-abliterated-q4_k_m",
    )
    with pytest.raises(ReviewRouteError):
        review_challenge(CHALLENGE, route=route, transport=MismatchTransport())


def test_rollback_removes_only_generated_qualification(tmp_path: Path) -> None:
    output = tmp_path / "qualification.json"
    output.write_text("{}")
    rollback_qualification(output)
    assert not output.exists()
