"""Provider-result parity fixtures for the model gateway (SKL-S3-02).

Equivalent Qwen and OpenAI responses for the pinned corpus-summary routes
must both validate to the same proposal payload shape.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from sklegal_domain import DataClassification
from sklegal_model_gateway import Provider

from tests.test_model_gateway import (
    FakeOpenAiTransport,
    FakeQwenTransport,
    FakeSecretResolver,
    make_gateway,
    make_openai_route,
    make_request,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "model_gateway"
QWEN_FIXTURE = FIXTURE_ROOT / "qwen-corpus-summary-response.json"
OPENAI_FIXTURE = FIXTURE_ROOT / "openai-corpus-summary-response.json"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"fixture must be a JSON object: {path}")
    return payload


class ProviderResultParityTests(unittest.TestCase):
    def test_qwen_and_openai_fixtures_validate_to_the_same_payload(self) -> None:
        qwen_gateway = make_gateway(
            qwen_transport=FakeQwenTransport(_load(QWEN_FIXTURE))
        )
        qwen_proposal = qwen_gateway.submit(make_request(), capability_ref="cap:test")
        openai_gateway = make_gateway(
            routes=(make_openai_route(),),
            openai_transport=FakeOpenAiTransport(_load(OPENAI_FIXTURE)),
            secret_resolver=FakeSecretResolver(),
        )
        openai_proposal = openai_gateway.submit(
            make_request(
                route_id="openai.corpus-summary.v1",
                classification=DataClassification.INTERNAL,
            ),
            capability_ref="cap:test",
        )
        self.assertEqual(Provider.QWEN_LOCAL, qwen_proposal.provider)
        self.assertEqual(Provider.OPENAI, openai_proposal.provider)
        self.assertEqual(qwen_proposal.payload, openai_proposal.payload)
        self.assertEqual(qwen_proposal.payload_sha256, openai_proposal.payload_sha256)
        self.assertEqual(
            qwen_proposal.output_schema_id, openai_proposal.output_schema_id
        )
        self.assertEqual(
            qwen_proposal.output_schema_sha256,
            openai_proposal.output_schema_sha256,
        )
        self.assertEqual(
            qwen_proposal.prompt_template_sha256,
            openai_proposal.prompt_template_sha256,
        )
        self.assertEqual(
            "qwen-fixture-0001", qwen_proposal.evidence.provider_request_id
        )
        self.assertEqual(
            "resp_fixture0001", openai_proposal.evidence.provider_request_id
        )
        self.assertEqual(
            qwen_proposal.evidence.token_usage,
            openai_proposal.evidence.token_usage,
        )


if __name__ == "__main__":
    unittest.main()
