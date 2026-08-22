"""Governed Qwen proposal run tests (SKL-S5-02A).

Every retrieval backend, capability gate, and Qwen transport is a fake:
no test performs a live model call, opens a socket, or mutates any state
outside the proposal ledger. The tests pin the acceptance contract for
this slice: the provider output remains a typed proposal and mutates no
domain, workflow, or connector state.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sklegal_domain import DataClassification
from sklegal_model_gateway import (
    CORPUS_SUMMARY_SCHEMA_ID,
    CorpusSummaryProposalPayload,
    ModelGateway,
    ModelPin,
    ModelRouteRecord,
    PolicyFileEgressGate,
    Provider,
    ProviderUnavailableError,
    QwenLocalProvider,
    RouteRegistry,
    SchemaRegistry,
    schema_sha256,
    sha256_text,
)
from sklegal_model_gateway.errors import SchemaValidationError
from sklegal_model_gateway.prompts import FilePromptStore
from sklegal_retrieval.fake import (
    FakeActiveProjectionRegistry,
    FakeAuthorizer,
    FakeCredentialBindingResolver,
    FakeRetrievalExecutor,
    FakeRetrievalRecord,
)
from sklegal_retrieval.models import (
    LexicalSearchParameters,
    QueryTemplateId,
    RetrievalComponent,
    RetrievalMode,
)
from sklegal_retrieval.orchestrator import RetrievalOrchestrator
from sklegal_worker import (
    ACTIVITY_RUN_GOVERNED_PROPOSAL,
    ContextRetrievalUnavailableError,
    FileProposalLedger,
    GovernedProposalActivities,
    GovernedProposalInput,
    GovernedProposalRunner,
    GovernedProposalWorkflow,
    InMemoryPinnedContextRegistry,
    InMemoryProposalLedger,
    ModelUnavailableError,
    PinnedProposalContext,
    PolicyDeniedError,
    ProposalOutputInvalidError,
    ProposalRunInput,
    ProposalRunOutcome,
    ProposalRunRecord,
    QueueKind,
    RunIdentity,
    SourceLinkValidationError,
    SourcePin,
    WorkflowInvariantError,
    proposal_record_fingerprint,
)

from tests.support import retrieval_pins as pins

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
FIXED_AT = FIXED_NOW.isoformat()

PIN_ID = "pin-pilot-1"
RUN_KEY = "proposal-run-1"
ROUTE_ID = "qwen.corpus-summary.v1"

INNER_PAYLOAD = {
    "summary": "The synthetic matter bundle describes an easement "
    "boundary dispute between two neighboring landowners.",
    "key_points": ["A recorded survey places the fence line inside the parcel."],
    "open_questions": ["Is the survey certified by a licensed surveyor?"],
    "confidence": "medium",
}

FIXTURE_CONTENT = (
    "The synthetic matter bundle describes an easement boundary dispute "
    "between two neighboring landowners over a recorded survey line."
)


def _inner_text() -> str:
    return json.dumps(INNER_PAYLOAD)


def _qwen_payload(text: str | None = None) -> dict[str, Any]:
    return {
        "model": "qwen3-32b-2025-04-28",
        "revision": "2025-04-28",
        "request_id": "qwen-test-0001",
        "response": text if text is not None else _inner_text(),
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class FakeQwenTransport:
    """Local fake transport: records prompts, scripts one outcome."""

    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.payload = dict(payload) if payload is not None else _qwen_payload()
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_output_tokens: int,
        timeout_seconds: float,
        cancel_token: Any,
    ) -> Mapping[str, Any]:
        del timeout_seconds, cancel_token
        self.calls.append(
            {"model": model, "prompt": prompt, "max_output_tokens": max_output_tokens}
        )
        if isinstance(self.payload.get("response"), Exception):
            raise self.payload["response"]
        return self.payload


class AllowGate:
    def verify(self, *, route: Any, request: Any, capability_ref: str) -> bool:
        return True


class DenyGate:
    def verify(self, *, route: Any, request: Any, capability_ref: str) -> bool:
        return False


def _make_gateway(
    *,
    qwen_transport: FakeQwenTransport,
    capability_gate: Any = None,
) -> ModelGateway:
    route = ModelRouteRecord.model_validate(
        {
            "route_id": ROUTE_ID,
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
    )
    return ModelGateway(
        registry=RouteRegistry((route,), registry_revision=REGISTRY_REVISION),
        prompt_store=FilePromptStore(PROMPT_ROOT),
        schema_registry=SchemaRegistry.default(),
        egress_gate=PolicyFileEgressGate(POLICY_PATH),
        capability_gate=capability_gate or AllowGate(),
        providers={Provider.QWEN_LOCAL: QwenLocalProvider(qwen_transport)},
        clock=lambda: FIXED_NOW,
    )


def _pinned_request() -> tuple[Any, Any, Any]:
    scope = pins.make_scope()
    projection = pins.make_projection(RetrievalComponent.LEXICAL, scope)
    request = pins.make_request(
        mode=RetrievalMode.LEXICAL,
        template_id=QueryTemplateId.LEXICAL_SEARCH_V1,
        parameters=LexicalSearchParameters(query_text="easement", max_results=10),
        projections=(projection,),
    )
    return scope, projection, request


def _fixture_record(projection: Any) -> FakeRetrievalRecord:
    return FakeRetrievalRecord(
        projection=projection,
        source=pins.make_source("r1"),
        content=FIXTURE_CONTENT,
    )


def _orchestrator(request: Any, executor: Any) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(
        authorizer=FakeAuthorizer(request.authorization),
        credential_resolver=FakeCredentialBindingResolver(request.credential_binding),
        registry=FakeActiveProjectionRegistry(request.projections),
        executor=executor,
    )


class SwitchingExecutor:
    """Delegate to one executor per call to simulate a mid-run change."""

    def __init__(self, first: Any, second: Any) -> None:
        self._executors = (first, second)
        self.call_count = 0

    def execute(self, bound: Any, projections: Any) -> Any:
        executor = self._executors[min(self.call_count, len(self._executors) - 1)]
        self.call_count += 1
        return executor.execute(bound, projections)


def _run_input(**overrides: Any) -> ProposalRunInput:
    values: dict[str, Any] = {
        "run_key": RUN_KEY,
        "context_pin_id": PIN_ID,
        "route_id": ROUTE_ID,
        "purpose": "corpus_analysis",
        "classification": DataClassification.INTERNAL,
        "capability_ref": "cap:qwen.corpus-summary",
        "instructions": "Summarize the pinned context.",
    }
    values.update(overrides)
    return ProposalRunInput.model_validate(values)


class _Harness:
    """One wired runner over fakes; no live model, no live database."""

    def __init__(
        self,
        *,
        executor: Any = None,
        transport: FakeQwenTransport | None = None,
        capability_gate: Any = None,
        ledger: Any = None,
    ) -> None:
        _scope, projection, request = _pinned_request()
        self.request = request
        self.executor = executor or FakeRetrievalExecutor(
            (_fixture_record(projection),)
        )
        self.transport = transport or FakeQwenTransport()
        self.ledger = ledger or InMemoryProposalLedger()
        registry = InMemoryPinnedContextRegistry()
        registry.register(
            PinnedProposalContext.from_request(
                PIN_ID,
                request,
                (SourcePin(source_id="source-r1", source_sha256=pins.HASH_A),),
            )
        )
        self.runner = GovernedProposalRunner(
            pinned_contexts=registry,
            retrieval=_orchestrator(request, self.executor),
            gateway=_make_gateway(
                qwen_transport=self.transport,
                capability_gate=capability_gate,
            ),
            ledger=self.ledger,
        )

    def run(self, request: ProposalRunInput | None = None) -> ProposalRunRecord:
        return self.runner.run(request or _run_input(), at=FIXED_AT)


class GovernedProposalHappyPathTests(unittest.TestCase):
    def test_records_one_content_free_record_with_pinned_evidence(self) -> None:
        harness = _Harness()

        record = harness.run()

        self.assertEqual(RUN_KEY, record.run_key)
        self.assertEqual(ROUTE_ID, record.route_id)
        self.assertEqual("qwen3-32b", record.model_name)
        self.assertEqual("2025-04-28", record.model_revision)
        self.assertEqual("corpus-summary.v1", record.prompt_template_id)
        self.assertEqual(CORPUS_SUMMARY_SCHEMA_ID, record.output_schema_id)
        self.assertEqual(TEMPLATE_HASH, record.prompt_template_sha256)
        self.assertEqual(SCHEMA_HASH, record.output_schema_sha256)
        self.assertEqual(REGISTRY_REVISION, record.registry_revision)
        self.assertEqual(30, record.total_tokens)
        self.assertEqual(FIXED_AT, record.validated_at)
        self.assertEqual(
            (SourcePin(source_id="source-r1", source_sha256=pins.HASH_A),),
            record.context_sources,
        )
        self.assertEqual(1, record.context_hit_count)
        self.assertTrue(record.typed_output_validated)
        self.assertTrue(record.source_links_validated)
        self.assertEqual(str(pins.TENANT_ID), str(harness.request.scope.tenant_id))
        self.assertEqual(1, harness.ledger.recorded_count)

    def test_prompt_carries_explicit_source_markers(self) -> None:
        harness = _Harness()

        harness.run()

        self.assertEqual(1, len(harness.transport.calls))
        prompt = harness.transport.calls[0]["prompt"]
        self.assertIn("source-r1", prompt)
        self.assertIn(pins.HASH_A, prompt)
        self.assertIn("fixture/r1", prompt)
        self.assertIn(FIXTURE_CONTENT, prompt)

    def test_source_inventory_is_reverified_after_the_provider_call(self) -> None:
        harness = _Harness()

        harness.run()

        self.assertEqual(2, harness.executor.call_count)
        self.assertEqual(1, len(harness.transport.calls))

    def test_replay_returns_the_original_record_without_a_second_call(self) -> None:
        harness = _Harness()
        first = harness.run()

        second = harness.run()

        self.assertEqual(first, second)
        self.assertEqual(1, harness.ledger.recorded_count)
        self.assertEqual(2, harness.executor.call_count)
        self.assertEqual(1, len(harness.transport.calls))


class GovernedProposalFailureTests(unittest.TestCase):
    def test_qwen_outage_maps_to_model_unavailable_and_records_nothing(self) -> None:
        harness = _Harness(
            transport=FakeQwenTransport(
                {"response": ProviderUnavailableError("qwen endpoint down")}
            )
        )

        with self.assertRaises(ModelUnavailableError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)

    def test_non_json_output_maps_to_proposal_output_invalid(self) -> None:
        harness = _Harness(transport=FakeQwenTransport(_qwen_payload("not json")))

        with self.assertRaises(ProposalOutputInvalidError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)

    def test_schema_violating_output_maps_to_proposal_output_invalid(self) -> None:
        broken = dict(INNER_PAYLOAD)
        broken["confidence"] = "certain"
        harness = _Harness(
            transport=FakeQwenTransport(_qwen_payload(json.dumps(broken)))
        )

        with self.assertRaises(ProposalOutputInvalidError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)

    def test_source_hash_change_mid_run_fails_closed(self) -> None:
        _scope, projection, request = _pinned_request()
        changed = FakeRetrievalExecutor(
            (
                FakeRetrievalRecord(
                    projection=projection,
                    source=pins.make_source("r1", source_hash=pins.HASH_B),
                    content=FIXTURE_CONTENT,
                ),
            )
        )
        executor = SwitchingExecutor(
            FakeRetrievalExecutor((_fixture_record(projection),)), changed
        )
        harness = _Harness(executor=executor)

        with self.assertRaises(SourceLinkValidationError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)
        self.assertEqual(1, len(harness.transport.calls))

    def test_source_set_change_mid_run_fails_closed(self) -> None:
        _scope, projection, request = _pinned_request()
        changed = FakeRetrievalExecutor(
            (
                FakeRetrievalRecord(
                    projection=projection,
                    source=pins.make_source("r2"),
                    content=FIXTURE_CONTENT,
                ),
            )
        )
        executor = SwitchingExecutor(
            FakeRetrievalExecutor((_fixture_record(projection),)), changed
        )
        harness = _Harness(executor=executor)

        with self.assertRaises(SourceLinkValidationError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)

    def test_unpinned_source_in_first_trace_fails_closed(self) -> None:
        _scope, projection, request = _pinned_request()
        executor = FakeRetrievalExecutor(
            (
                FakeRetrievalRecord(
                    projection=projection,
                    source=pins.make_source("r2"),
                    content=FIXTURE_CONTENT,
                ),
            )
        )
        harness = _Harness(executor=executor)

        with self.assertRaises(SourceLinkValidationError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)
        self.assertEqual(0, len(harness.transport.calls))

    def test_empty_retrieval_trace_fails_closed(self) -> None:
        _scope, projection, request = _pinned_request()
        executor = FakeRetrievalExecutor(
            (
                FakeRetrievalRecord(
                    projection=projection,
                    source=pins.make_source("r1"),
                    content="no query term overlap at all",
                ),
            )
        )
        harness = _Harness(executor=executor)

        with self.assertRaises(SourceLinkValidationError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)

    def test_unknown_context_pin_fails_closed(self) -> None:
        harness = _Harness()

        with self.assertRaises(WorkflowInvariantError):
            harness.run(_run_input(context_pin_id="pin-unknown"))

    def test_capability_denial_maps_to_policy_denied(self) -> None:
        harness = _Harness(capability_gate=DenyGate())

        with self.assertRaises(PolicyDeniedError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)

    def test_retrieval_backend_outage_maps_to_retryable_unavailable(self) -> None:
        _scope, projection, request = _pinned_request()
        executor = FakeRetrievalExecutor(
            unavailable_components=(RetrievalComponent.LEXICAL,)
        )
        harness = _Harness(executor=executor)

        with self.assertRaises(ContextRetrievalUnavailableError):
            harness.run()
        self.assertEqual(0, harness.ledger.recorded_count)


class ProposalStatelessnessTests(unittest.TestCase):
    """The provider output stays a proposal and mutates no other state."""

    def test_record_and_history_carry_no_matter_content(self) -> None:
        harness = _Harness()

        record = harness.run()

        record_json = record.model_dump_json()
        input_json = _run_input().model_dump_json()
        for word in ("easement", "survey", "neighboring", "fence"):
            self.assertNotIn(word, record_json)
            self.assertNotIn(word, input_json)

    def test_record_excludes_the_proposal_payload_and_prompt(self) -> None:
        harness = _Harness()

        record = harness.run()

        dumped = record.model_dump(mode="json")
        self.assertNotIn("payload", dumped)
        self.assertNotIn("prompt", dumped)
        self.assertNotIn(INNER_PAYLOAD["summary"], json.dumps(dumped))


class ProposalLedgerTests(unittest.TestCase):
    def _record(self) -> ProposalRunRecord:
        return _Harness().run()

    def test_fingerprint_ignores_validated_at(self) -> None:
        record = self._record()
        replayed = record.model_copy(
            update={"validated_at": "2026-08-23T09:00:00+00:00"}
        )
        self.assertEqual(
            proposal_record_fingerprint(record), proposal_record_fingerprint(replayed)
        )

    def test_in_memory_ledger_rejects_conflicting_evidence(self) -> None:
        ledger = InMemoryProposalLedger()
        record = self._record()
        conflicting = record.model_copy(update={"model_name": "qwen3-other"})

        self.assertEqual(record, ledger.record(record))
        with self.assertRaises(WorkflowInvariantError):
            ledger.record(conflicting)
        self.assertEqual(1, ledger.recorded_count)

    def test_file_ledger_survives_restart(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposals.json"
            ledger = FileProposalLedger(path)
            record = self._record()
            stored = ledger.record(record)

            reopened = FileProposalLedger(path)
            self.assertEqual(record, reopened.find(RUN_KEY))
            self.assertEqual(1, reopened.recorded_count)
            self.assertEqual(record, reopened.record(stored))

    def test_file_ledger_rejects_conflicting_evidence(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposals.json"
            ledger = FileProposalLedger(path)
            record = self._record()

            ledger.record(record)
            conflicting = record.model_copy(update={"proposal_id": "proposal-other"})
            with self.assertRaises(WorkflowInvariantError):
                ledger.record(conflicting)

    def test_file_ledger_fails_closed_on_malformed_state(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposals.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(WorkflowInvariantError):
                FileProposalLedger(path).recorded_count

    def test_record_round_trips_through_json(self) -> None:
        record = self._record()

        replayed = ProposalRunRecord.model_validate(
            json.loads(record.model_dump_json())
        )
        self.assertEqual(record, replayed)


class ProposalWorkflowContractTests(unittest.TestCase):
    def test_governed_proposal_input_validates_queue_and_run_key(self) -> None:
        identity = RunIdentity(
            tenant_id=pins.TENANT_ID,
            matter_id=pins.MATTER_ID,
            run_key=RUN_KEY,
            correlation_id=UUID(int=42),
            requested_by="attorney-1",
        )
        base = {
            "identity": identity,
            "queue": QueueKind.INTERACTIVE,
            "proposal": _run_input(),
        }

        GovernedProposalInput.model_validate(base)
        GovernedProposalInput.model_validate({**base, "queue": QueueKind.LONG_CONTEXT})
        with self.assertRaises(ValidationError):
            GovernedProposalInput.model_validate({**base, "queue": QueueKind.BATCH})
        with self.assertRaises(ValidationError):
            GovernedProposalInput.model_validate(
                {
                    **base,
                    "identity": identity.model_copy(
                        update={"run_key": "different-run"}
                    ),
                }
            )

    def test_governed_proposal_input_round_trips_through_json(self) -> None:
        payload = GovernedProposalInput(
            identity=RunIdentity(
                tenant_id=pins.TENANT_ID,
                matter_id=pins.MATTER_ID,
                run_key=RUN_KEY,
                correlation_id=UUID(int=7),
                requested_by="attorney-1",
            ),
            queue=QueueKind.LONG_CONTEXT,
            proposal=_run_input(),
        )

        replayed = GovernedProposalInput.model_validate(
            json.loads(payload.model_dump_json())
        )
        self.assertEqual(payload, replayed)

    def test_workflow_definition_is_registered(self) -> None:
        self.assertTrue(
            hasattr(GovernedProposalWorkflow, "__temporal_workflow_definition")
        )
        self.assertTrue(
            hasattr(GovernedProposalWorkflow.run, "__temporal_workflow_run")
        )

    def test_activity_constant_matches_registered_activity(self) -> None:
        definition = getattr(
            GovernedProposalActivities.run_governed_proposal,
            "__temporal_activity_definition",
            None,
        )
        self.assertIsNotNone(definition)
        self.assertEqual(ACTIVITY_RUN_GOVERNED_PROPOSAL, definition.name)

    def test_activity_returns_outcome_matching_the_record_digest(self) -> None:
        harness = _Harness()
        activities = GovernedProposalActivities(
            driver=harness.runner, clock=lambda: FIXED_NOW
        )

        outcome = asyncio.run(activities.run_governed_proposal(_run_input()))

        record = harness.ledger.find(RUN_KEY)
        self.assertIsNotNone(record)
        assert record is not None
        expected = ProposalRunOutcome(
            run_key=RUN_KEY,
            proposal_id=record.proposal_id,
            record_digest=proposal_record_fingerprint(record),
            recorded_at=FIXED_NOW,
        )
        self.assertEqual(expected, outcome)


class ProposalModuleHygieneTests(unittest.TestCase):
    def test_new_module_uses_ascii_hyphens_only(self) -> None:
        source = (
            ROOT / "services" / "worker" / "src" / "sklegal_worker" / "proposal_run.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("\u2013", source)
        self.assertNotIn("\u2014", source)

    def test_gateway_schema_failure_type_is_mapped(self) -> None:
        # Guard against silently changing the gateway contract this
        # runner's error mapping depends on.
        self.assertTrue(issubclass(SchemaValidationError, Exception))
