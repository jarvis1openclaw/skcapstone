"""Unit tests for the provider-neutral model gateway (SKL-S3-02).

All provider transports and gates are fakes. No test performs live network,
live Qwen, or live OpenAI calls.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sklegal_domain import DataClassification
from sklegal_model_gateway import (
    CORPUS_SUMMARY_SCHEMA_ID,
    CancellationToken,
    CapabilityDeniedError,
    ContextBudgetExceededError,
    CorpusSummaryProposalPayload,
    EgressDecision,
    EgressDeniedError,
    GatewayValue,
    ModelCancelledError,
    ModelGateway,
    ModelPin,
    ModelRouteRecord,
    ModelTimeoutError,
    OpenAiResponsesProvider,
    PolicyFileEgressGate,
    PolicyUnavailableError,
    Proposal,
    ProposalRequest,
    Provider,
    ProviderContractError,
    ProviderSaturationError,
    ProviderUnavailableError,
    QwenLocalProvider,
    RouteDisabledError,
    RouteIntegrityError,
    RouteNotFoundError,
    RouteRegistry,
    SchemaRegistry,
    SchemaValidationError,
    schema_sha256,
    sha256_text,
)
from sklegal_model_gateway.prompts import FilePromptStore

ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = ROOT / "config" / "model_gateway" / "prompts"
POLICY_PATH = ROOT / "config" / "security" / "policy.json"
TEMPLATE_TEXT = (PROMPT_ROOT / "corpus-summary.v1.prompt.txt").read_text(
    encoding="utf-8"
)
TEMPLATE_HASH = sha256_text(TEMPLATE_TEXT)
SCHEMA_HASH = schema_sha256(CorpusSummaryProposalPayload)
REGISTRY_REVISION = "a" * 64
FIXED_NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

INNER_PAYLOAD = {
    "summary": "The synthetic matter bundle describes a boundary easement "
    "dispute between two neighboring landowners.",
    "key_points": ["A recorded survey places the fence line inside the parcel."],
    "open_questions": ["Is the survey certified by a licensed surveyor?"],
    "confidence": "medium",
}


def _inner_text() -> str:
    return json.dumps(INNER_PAYLOAD)


def make_route(**overrides: Any) -> ModelRouteRecord:
    values: dict[str, Any] = {
        "route_id": "qwen.corpus-summary.v1",
        "provider": Provider.QWEN_LOCAL,
        "enabled": True,
        "model": ModelPin(name="qwen3-32b", revision="2025-04-28"),
        "prompt_template_id": "corpus-summary.v1",
        "prompt_template_sha256": TEMPLATE_HASH,
        "output_schema_id": CORPUS_SUMMARY_SCHEMA_ID,
        "output_schema_sha256": SCHEMA_HASH,
        "context_token_budget": 24000,
        "max_output_tokens": 2048,
        "timeout_seconds": 30.0,
        "retry_class": "model",
        "egress_classification_ceiling": DataClassification.HIGHLY_RESTRICTED,
        "max_concurrent": 4,
    }
    values.update(overrides)
    return ModelRouteRecord.model_validate(values)


def make_openai_route(**overrides: Any) -> ModelRouteRecord:
    values: dict[str, Any] = {
        "route_id": "openai.corpus-summary.v1",
        "provider": Provider.OPENAI,
        "model": ModelPin(name="gpt-4o", revision="2024-08-06"),
        "egress_classification_ceiling": DataClassification.CONFIDENTIAL,
        "secret_reference": "vault:sklegal/openai/platform-api-key",
    }
    values.update(overrides)
    return make_route(**values)


def make_request(**overrides: Any) -> ProposalRequest:
    values: dict[str, Any] = {
        "request_id": "req-1",
        "tenant_id": "tenant-1",
        "matter_id": "matter-1",
        "route_id": "qwen.corpus-summary.v1",
        "purpose": "corpus_analysis",
        "classification": DataClassification.INTERNAL,
        "prompt_inputs": {
            "instructions": "Summarize the bundle.",
            "context": "Synthetic corpus excerpt.",
        },
    }
    values.update(overrides)
    return ProposalRequest.model_validate(values)


def qwen_payload(
    text: str | None = None,
    *,
    revision: str = "2025-04-28",
) -> dict[str, Any]:
    return {
        "model": "qwen3-32b-2025-04-28",
        "revision": revision,
        "request_id": "qwen-test-0001",
        "response": text if text is not None else _inner_text(),
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def openai_payload(text: str | None = None) -> dict[str, Any]:
    return {
        "id": "resp_test0001",
        "model": "gpt-4o-2024-08-06",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text if text is not None else _inner_text(),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


class FakeQwenTransport:
    def __init__(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        error: Exception | None = None,
        block_event: threading.Event | None = None,
        wait_on_token: bool = False,
    ) -> None:
        self.payload = dict(payload) if payload is not None else qwen_payload()
        self.error = error
        self.block_event = block_event
        self.wait_on_token = wait_on_token
        self.calls: list[dict[str, Any]] = []
        self.observed_cancel = False

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_output_tokens: int,
        timeout_seconds: float,
        cancel_token: CancellationToken,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        if self.block_event is not None:
            self.block_event.wait(5.0)
        if self.wait_on_token:
            cancel_token.wait(5.0)
            self.observed_cancel = cancel_token.cancelled
        return self.payload


class FakeOpenAiTransport:
    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.payload = dict(payload) if payload is not None else openai_payload()
        self.calls: list[dict[str, Any]] = []

    def create_response(
        self,
        *,
        body: Mapping[str, Any],
        api_key: str,
        timeout_seconds: float,
        cancel_token: CancellationToken,
    ) -> Mapping[str, Any]:
        self.calls.append({"body": dict(body), "api_key": api_key})
        return self.payload


class FakeSecretResolver:
    def __init__(self, secret: str = "test-openai-key-placeholder") -> None:
        self.secret = secret
        self.references: list[str] = []

    def resolve(self, reference: str) -> str:
        self.references.append(reference)
        return self.secret


class AllowGate:
    def verify(
        self,
        *,
        route: ModelRouteRecord,
        request: ProposalRequest,
        capability_ref: str,
    ) -> bool:
        return True


class DenyGate:
    def verify(
        self,
        *,
        route: ModelRouteRecord,
        request: ProposalRequest,
        capability_ref: str,
    ) -> bool:
        return False


class ExplodingGate:
    def verify(
        self,
        *,
        route: ModelRouteRecord,
        request: ProposalRequest,
        capability_ref: str,
    ) -> bool:
        raise RuntimeError("capability backend unavailable")


class ExplodingEgressGate:
    def decide(
        self,
        *,
        route: ModelRouteRecord,
        classification: DataClassification,
        human_approval_ref: str | None,
    ) -> EgressDecision:
        raise RuntimeError("policy backend unavailable")


def make_gateway(
    *,
    routes: tuple[ModelRouteRecord, ...] | None = None,
    qwen_transport: FakeQwenTransport | None = None,
    openai_transport: FakeOpenAiTransport | None = None,
    secret_resolver: FakeSecretResolver | None = None,
    capability_gate: Any = None,
    egress_gate: Any = None,
) -> ModelGateway:
    route_records = routes if routes is not None else (make_route(),)
    registry = RouteRegistry(route_records, registry_revision=REGISTRY_REVISION)
    providers: dict[Provider, Any] = {}
    if qwen_transport is not None:
        providers[Provider.QWEN_LOCAL] = QwenLocalProvider(qwen_transport)
    if openai_transport is not None:
        providers[Provider.OPENAI] = OpenAiResponsesProvider(
            openai_transport,
            secret_resolver or FakeSecretResolver(),
        )
    return ModelGateway(
        registry=registry,
        prompt_store=FilePromptStore(PROMPT_ROOT),
        schema_registry=SchemaRegistry.default(),
        egress_gate=egress_gate or PolicyFileEgressGate(POLICY_PATH),
        capability_gate=capability_gate or AllowGate(),
        providers=providers,
        clock=lambda: FIXED_NOW,
    )


class GatewayHappyPathTests(unittest.TestCase):
    def test_qwen_submission_returns_evidence_rich_proposal(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(qwen_transport=transport)
        proposal = gateway.submit(make_request(), capability_ref="cap:test")
        self.assertEqual("qwen.corpus-summary.v1", proposal.route_id)
        self.assertEqual(Provider.QWEN_LOCAL, proposal.provider)
        self.assertEqual("qwen3-32b", proposal.model.name)
        self.assertEqual("2025-04-28", proposal.model.revision)
        self.assertEqual(TEMPLATE_HASH, proposal.prompt_template_sha256)
        self.assertEqual(SCHEMA_HASH, proposal.output_schema_sha256)
        self.assertEqual(REGISTRY_REVISION, proposal.registry_revision)
        self.assertEqual(INNER_PAYLOAD["summary"], proposal.payload["summary"])
        self.assertEqual("medium", proposal.payload["confidence"])
        self.assertEqual("qwen-test-0001", proposal.evidence.provider_request_id)
        self.assertEqual(FIXED_NOW, proposal.evidence.started_at)
        self.assertEqual(0, proposal.evidence.duration_ms)
        self.assertEqual(30, proposal.evidence.token_usage.total_tokens)
        self.assertEqual("local_provider", proposal.policy.egress_rule)
        self.assertEqual("qwen3-32b-2025-04-28", transport.calls[0]["model"])
        self.assertIn("Summarize the bundle.", transport.calls[0]["prompt"])
        self.assertEqual(0, gateway.admission.in_flight(Provider.QWEN_LOCAL))

    def test_openai_confidential_requires_and_records_human_approval(self) -> None:
        transport = FakeOpenAiTransport()
        gateway = make_gateway(
            routes=(make_openai_route(),),
            openai_transport=transport,
        )
        request = make_request(
            route_id="openai.corpus-summary.v1",
            classification=DataClassification.CONFIDENTIAL,
            human_approval_ref="approval-2026-08-22-001",
        )
        proposal = gateway.submit(request, capability_ref="cap:test")
        self.assertEqual(Provider.OPENAI, proposal.provider)
        self.assertEqual("conditional_human_approval", proposal.policy.egress_rule)
        self.assertEqual("approval-2026-08-22-001", proposal.policy.human_approval_ref)
        self.assertEqual("resp_test0001", proposal.evidence.provider_request_id)


class GatewayDenialTests(unittest.TestCase):
    def test_unknown_route_fails_closed(self) -> None:
        gateway = make_gateway(qwen_transport=FakeQwenTransport())
        with self.assertRaises(RouteNotFoundError):
            gateway.submit(
                make_request(route_id="qwen.unknown.v1"),
                capability_ref="cap:test",
            )

    def test_disabled_route_fails_closed(self) -> None:
        gateway = make_gateway(
            routes=(make_route(enabled=False),),
            qwen_transport=FakeQwenTransport(),
        )
        with self.assertRaises(RouteDisabledError):
            gateway.submit(make_request(), capability_ref="cap:test")

    def test_capability_denial_blocks_provider_call(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(qwen_transport=transport, capability_gate=DenyGate())
        with self.assertRaises(CapabilityDeniedError):
            gateway.submit(make_request(), capability_ref="cap:test")
        self.assertEqual([], transport.calls)

    def test_capability_verifier_exception_fails_closed(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(
            qwen_transport=transport, capability_gate=ExplodingGate()
        )
        with self.assertRaises(CapabilityDeniedError):
            gateway.submit(make_request(), capability_ref="cap:test")
        self.assertEqual([], transport.calls)

    def test_empty_capability_reference_fails_closed(self) -> None:
        gateway = make_gateway(qwen_transport=FakeQwenTransport())
        with self.assertRaises(CapabilityDeniedError):
            gateway.submit(make_request(), capability_ref="")

    def test_egress_gate_exception_fails_closed(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(
            qwen_transport=transport, egress_gate=ExplodingEgressGate()
        )
        with self.assertRaises(EgressDeniedError):
            gateway.submit(make_request(), capability_ref="cap:test")
        self.assertEqual([], transport.calls)

    def test_egress_policy_file_unavailable_fails_closed(self) -> None:
        with self.assertRaises(PolicyUnavailableError):
            PolicyFileEgressGate(ROOT / "config" / "security" / "missing.json")


class GatewayEgressClassificationTests(unittest.TestCase):
    def _openai_gateway(self, ceiling: DataClassification) -> ModelGateway:
        return make_gateway(
            routes=(make_openai_route(egress_classification_ceiling=ceiling),),
            openai_transport=FakeOpenAiTransport(),
        )

    def _deny_reason(
        self,
        classification: DataClassification,
        *,
        ceiling: DataClassification = DataClassification.HIGHLY_RESTRICTED,
        approval: str | None = None,
    ) -> str:
        gateway = self._openai_gateway(ceiling)
        request = make_request(
            route_id="openai.corpus-summary.v1",
            classification=classification,
            human_approval_ref=approval,
        )
        with self.assertRaises(EgressDeniedError) as caught:
            gateway.submit(request, capability_ref="cap:test")
        return str(caught.exception)

    def test_privileged_work_product_egress_denied(self) -> None:
        reason = self._deny_reason(DataClassification.PRIVILEGED_WORK_PRODUCT)
        self.assertIn("classification_egress_denied", reason)

    def test_highly_restricted_egress_denied(self) -> None:
        reason = self._deny_reason(DataClassification.HIGHLY_RESTRICTED)
        self.assertIn("classification_egress_denied", reason)

    def test_confidential_without_human_approval_denied(self) -> None:
        reason = self._deny_reason(DataClassification.CONFIDENTIAL)
        self.assertIn("human_egress_approval_required", reason)

    def test_confidential_with_human_approval_allowed(self) -> None:
        gateway = self._openai_gateway(DataClassification.CONFIDENTIAL)
        proposal = gateway.submit(
            make_request(
                route_id="openai.corpus-summary.v1",
                classification=DataClassification.CONFIDENTIAL,
                human_approval_ref="approval-1",
            ),
            capability_ref="cap:test",
        )
        self.assertEqual(Provider.OPENAI, proposal.provider)

    def test_route_ceiling_denies_above_ceiling_classification(self) -> None:
        reason = self._deny_reason(
            DataClassification.PRIVILEGED_WORK_PRODUCT,
            ceiling=DataClassification.CONFIDENTIAL,
        )
        self.assertIn("route_ceiling_exceeded", reason)

    def test_local_qwen_accepts_restricted_classification(self) -> None:
        gateway = make_gateway(qwen_transport=FakeQwenTransport())
        proposal = gateway.submit(
            make_request(classification=DataClassification.HIGHLY_RESTRICTED),
            capability_ref="cap:test",
        )
        self.assertEqual("local_provider", proposal.policy.egress_rule)


class GatewayRedactionTests(unittest.TestCase):
    def test_protected_fields_are_redacted_before_provider_call(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(qwen_transport=transport)
        protected_value = "Attorney draft strategy note seven."
        request = make_request(
            prompt_inputs={
                "instructions": "Summarize the bundle.",
                "context": protected_value,
            },
            protected_fields=frozenset({"context"}),
        )
        proposal = gateway.submit(request, capability_ref="cap:test")
        sent_prompt = transport.calls[0]["prompt"]
        self.assertIn("[REDACTED:", sent_prompt)
        self.assertNotIn(protected_value, sent_prompt)
        self.assertEqual(1, len(proposal.redactions))
        record = proposal.redactions[0]
        self.assertEqual("context", record.field)
        self.assertEqual(sha256_text(protected_value), record.value_sha256)

    def test_unprotected_fields_pass_through_unmodified(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(qwen_transport=transport)
        gateway.submit(make_request(), capability_ref="cap:test")
        self.assertIn("Synthetic corpus excerpt.", transport.calls[0]["prompt"])


class GatewayBudgetTests(unittest.TestCase):
    def test_context_budget_exceeded_blocks_provider_call(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(qwen_transport=transport)
        request = make_request(
            prompt_inputs={
                "instructions": "Summarize the bundle.",
                "context": "x" * 100_000,
            }
        )
        with self.assertRaises(ContextBudgetExceededError):
            gateway.submit(request, capability_ref="cap:test")
        self.assertEqual([], transport.calls)
        self.assertEqual(0, gateway.admission.in_flight(Provider.QWEN_LOCAL))


class GatewayIntegrityTests(unittest.TestCase):
    def test_prompt_template_hash_mismatch_fails_closed(self) -> None:
        gateway = make_gateway(
            routes=(make_route(prompt_template_sha256="0" * 64),),
            qwen_transport=FakeQwenTransport(),
        )
        with self.assertRaises(RouteIntegrityError):
            gateway.submit(make_request(), capability_ref="cap:test")

    def test_output_schema_hash_mismatch_fails_closed(self) -> None:
        gateway = make_gateway(
            routes=(make_route(output_schema_sha256="0" * 64),),
            qwen_transport=FakeQwenTransport(),
        )
        with self.assertRaises(RouteIntegrityError):
            gateway.submit(make_request(), capability_ref="cap:test")

    def test_provider_model_revision_mismatch_fails_closed(self) -> None:
        gateway = make_gateway(
            qwen_transport=FakeQwenTransport(qwen_payload(revision="1999-01-01"))
        )
        with self.assertRaises(RouteIntegrityError):
            gateway.submit(make_request(), capability_ref="cap:test")


class GatewayAdmissionTests(unittest.TestCase):
    def test_fifth_concurrent_submission_gets_saturation_error(self) -> None:
        block = threading.Event()
        transport = FakeQwenTransport(block_event=block)
        gateway = make_gateway(qwen_transport=transport)
        results: list[Proposal] = []
        errors: list[Exception] = []

        def worker(index: int) -> None:
            try:
                results.append(
                    gateway.submit(
                        make_request(request_id=f"req-{index}"),
                        capability_ref="cap:test",
                    )
                )
            except Exception as exc:  # collected for assertions
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5.0
        while (
            gateway.admission.in_flight(Provider.QWEN_LOCAL) < 4
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(4, gateway.admission.in_flight(Provider.QWEN_LOCAL))
        with self.assertRaises(ProviderSaturationError):
            gateway.submit(
                make_request(request_id="req-overflow"),
                capability_ref="cap:test",
            )
        block.set()
        for thread in threads:
            thread.join(5.0)
        self.assertEqual([], errors)
        self.assertEqual(4, len(results))
        self.assertEqual(0, gateway.admission.in_flight(Provider.QWEN_LOCAL))


class GatewayTimeoutTests(unittest.TestCase):
    def test_timeout_raises_typed_error_and_releases_slot(self) -> None:
        transport = FakeQwenTransport(wait_on_token=True)
        gateway = make_gateway(
            routes=(make_route(timeout_seconds=0.1),),
            qwen_transport=transport,
        )
        started = time.monotonic()
        with self.assertRaises(ModelTimeoutError):
            gateway.submit(make_request(), capability_ref="cap:test")
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(0, gateway.admission.in_flight(Provider.QWEN_LOCAL))


class GatewayCancellationTests(unittest.TestCase):
    def test_cancelled_token_blocks_submission_before_provider_call(self) -> None:
        transport = FakeQwenTransport()
        gateway = make_gateway(qwen_transport=transport)
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(ModelCancelledError):
            gateway.submit(
                make_request(), capability_ref="cap:test", cancel_token=token
            )
        self.assertEqual([], transport.calls)

    def test_mid_call_cancellation_discards_result_and_releases_slot(self) -> None:
        transport = FakeQwenTransport(wait_on_token=True)
        gateway = make_gateway(qwen_transport=transport)
        token = CancellationToken()
        outcome: list[Exception] = []

        def run() -> None:
            try:
                gateway.submit(
                    make_request(), capability_ref="cap:test", cancel_token=token
                )
            except Exception as exc:  # collected for assertions
                outcome.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.1)
        token.cancel()
        thread.join(5.0)
        self.assertEqual(1, len(outcome))
        self.assertIsInstance(outcome[0], ModelCancelledError)
        self.assertTrue(transport.observed_cancel)
        self.assertEqual(0, gateway.admission.in_flight(Provider.QWEN_LOCAL))


class GatewaySchemaFailureTests(unittest.TestCase):
    def _assert_schema_failure(self, text: str) -> None:
        gateway = make_gateway(qwen_transport=FakeQwenTransport(qwen_payload(text)))
        with self.assertRaises(SchemaValidationError):
            gateway.submit(make_request(), capability_ref="cap:test")

    def test_non_json_output_is_schema_failure(self) -> None:
        self._assert_schema_failure("this is not json")

    def test_non_object_json_output_is_schema_failure(self) -> None:
        self._assert_schema_failure("[1, 2, 3]")

    def test_missing_required_field_is_schema_failure(self) -> None:
        broken = dict(INNER_PAYLOAD)
        del broken["confidence"]
        self._assert_schema_failure(json.dumps(broken))

    def test_wrong_field_type_is_schema_failure(self) -> None:
        broken = dict(INNER_PAYLOAD)
        broken["confidence"] = "certain"
        self._assert_schema_failure(json.dumps(broken))


class ProviderOutageTests(unittest.TestCase):
    def test_transport_error_is_typed_outage_and_slot_released(self) -> None:
        transport = FakeQwenTransport(error=ConnectionError("endpoint down"))
        gateway = make_gateway(qwen_transport=transport)
        with self.assertRaises(ProviderUnavailableError):
            gateway.submit(make_request(), capability_ref="cap:test")
        self.assertEqual(0, gateway.admission.in_flight(Provider.QWEN_LOCAL))

    def test_gateway_recovers_after_outage(self) -> None:
        transport = FakeQwenTransport(error=ConnectionError("endpoint down"))
        gateway = make_gateway(qwen_transport=transport)
        with self.assertRaises(ProviderUnavailableError):
            gateway.submit(make_request(), capability_ref="cap:test")
        transport.error = None
        proposal = gateway.submit(make_request(), capability_ref="cap:test")
        self.assertEqual(INNER_PAYLOAD["summary"], proposal.payload["summary"])


class OpenAiAdapterTests(unittest.TestCase):
    def test_structured_output_request_shape_and_secret_indirection(self) -> None:
        transport = FakeOpenAiTransport()
        resolver = FakeSecretResolver()
        gateway = make_gateway(
            routes=(make_openai_route(),),
            openai_transport=transport,
            secret_resolver=resolver,
        )
        proposal = gateway.submit(
            make_request(
                route_id="openai.corpus-summary.v1",
                classification=DataClassification.INTERNAL,
            ),
            capability_ref="cap:test",
        )
        self.assertEqual(1, len(transport.calls))
        call = transport.calls[0]
        body = call["body"]
        self.assertEqual("gpt-4o-2024-08-06", body["model"])
        self.assertEqual("gpt-4o", proposal.model.name)
        self.assertEqual("2024-08-06", proposal.model.revision)
        response_format = body["text"]["format"]
        self.assertEqual("json_schema", response_format["type"])
        self.assertTrue(response_format["strict"])
        self.assertEqual("sklegal.corpus-summary_v1", response_format["name"])
        self.assertIn("properties", response_format["schema"])
        self.assertEqual(2048, body["max_output_tokens"])
        self.assertFalse(body["store"])
        self.assertEqual(["vault:sklegal/openai/platform-api-key"], resolver.references)
        self.assertEqual("test-openai-key-placeholder", call["api_key"])
        self.assertNotIn(call["api_key"], json.dumps(body))
        self.assertNotIn(call["api_key"], json.dumps(proposal.model_dump(mode="json")))

    def test_openai_model_mismatch_is_provider_contract_error(self) -> None:
        payload = openai_payload()
        payload["model"] = "gpt-4o-other"
        gateway = make_gateway(
            routes=(make_openai_route(),),
            openai_transport=FakeOpenAiTransport(payload),
        )
        with self.assertRaises(ProviderContractError):
            gateway.submit(
                make_request(
                    route_id="openai.corpus-summary.v1",
                    classification=DataClassification.INTERNAL,
                ),
                capability_ref="cap:test",
            )


class ProposalImmutabilityTests(unittest.TestCase):
    def test_proposal_is_frozen_and_carries_no_mutating_handle(self) -> None:
        gateway = make_gateway(qwen_transport=FakeQwenTransport())
        proposal = gateway.submit(make_request(), capability_ref="cap:test")
        with self.assertRaises(ValidationError):
            proposal.payload = {}  # type: ignore[misc]
        for field_name in Proposal.model_fields:
            value = getattr(proposal, field_name)
            self.assertFalse(
                callable(value), f"proposal field is a callable: {field_name}"
            )
        base_members = set(dir(GatewayValue))
        added_public = {
            name
            for name in dir(Proposal)
            if not name.startswith("_") and name not in base_members
        }
        added_callables = {
            name for name in added_public if callable(getattr(Proposal, name, None))
        }
        self.assertEqual(set(), added_callables)


if __name__ == "__main__":
    unittest.main()
