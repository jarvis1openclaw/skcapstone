from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sklegal_persistence.features.agent_runs.models import (
    AgentRunRecord,
    canonical_sha256,
)

from tests.features.agent_runs.helpers import fixture_json, fixture_record


def test_public_synthetic_fixture_round_trips_byte_stably() -> None:
    record = fixture_record()
    reparsed = AgentRunRecord.model_validate(
        record.model_dump(mode="json", by_alias=True)
    )
    assert reparsed == record
    assert canonical_sha256(reparsed) == canonical_sha256(record)
    assert record.request.classification == "public"
    assert record.request.public_synthetic is True
    assert record.dispositions == ()


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("request", "classification"), "confidential"),
        (("request", "publicSynthetic"), False),
        (("status",), "completed"),
    ),
)
def test_record_rejects_non_public_or_incomplete_completion(
    path: tuple[str, ...], value: object
) -> None:
    payload = deepcopy(fixture_json())
    cursor = payload
    for segment in path[:-1]:
        cursor = cursor[segment]  # type: ignore[assignment,index]
    cursor[path[-1]] = value
    if path == ("status",):
        payload["proposalPayloadSha256"] = None
    with pytest.raises(ValidationError):
        AgentRunRecord.model_validate(payload)


def test_record_rejects_cross_matter_request_scope() -> None:
    payload = fixture_json()
    request = payload["request"]
    assert isinstance(request, dict)
    request["matterId"] = "30000000-0000-4000-8000-000000000099"
    with pytest.raises(ValidationError, match="scope differs"):
        AgentRunRecord.model_validate(payload)


def test_recommendation_requires_exact_scoring_policy_dimensions() -> None:
    payload = fixture_json()
    recommendations = payload["recommendations"]
    assert isinstance(recommendations, list)
    recommendation = recommendations[0]
    assert isinstance(recommendation, dict)
    dimensions = recommendation["scoreDimensions"]
    assert isinstance(dimensions, list)
    assert isinstance(dimensions[1], dict)
    dimensions[1]["dimension"] = "urgency"
    with pytest.raises(ValidationError, match="four unique score dimensions"):
        AgentRunRecord.model_validate(payload)


def test_recommendation_requires_model_inference_lane() -> None:
    payload = fixture_json()
    recommendations = payload["recommendations"]
    assert isinstance(recommendations, list)
    recommendation = recommendations[0]
    assert isinstance(recommendation, dict)
    evidence = recommendation["evidence"]
    assert isinstance(evidence, list)
    recommendation["evidence"] = [
        item
        for item in evidence
        if isinstance(item, dict) and item["sourceRole"] != "model_inference"
    ]
    with pytest.raises(ValidationError, match="model inference evidence"):
        AgentRunRecord.model_validate(payload)
