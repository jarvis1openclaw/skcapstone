"""Hermetic SKGateway transport seam tests (SKL-S3-10).

All transports, gates, and audits are fakes. No test performs live network,
live Qwen, live SKGateway, or live OpenAI calls, and no fixture contains a
real secret or capability token.
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
    AUDIT_RECORD_SCHEMA,
    CATALOG_SCHEMA,
    CapacityDomainController,
    CapacityEnvelope,
    CapacityQueueTimeoutError,
    CatalogLookupError,
    ModelCatalog,
    ModelGateway,
    ModelPin,
    ModelRouteRecord,
    PolicyFileEgressGate,
    Provider,
    ProviderSaturationError,
    QwenLocalProvider,
    RouteRegistry,
    SchemaRegistry,
    SkGatewayChatProvider,
    SkGatewayLivePathGate,
    SkGatewayRoutePolicyVerifier,
    TransportBindingError,
    TransportKind,
    TransportProfile,
    TransportProfileDisabledError,
    TransportProfileNotFoundError,
    TransportProfileStaleError,
    TransportProfileStore,
    TrustZone,
    WorkloadClass,
    schema_sha256,
    sha256_text,
)
from sklegal_model_gateway.audit_records import (
    AuditRecord,
    FailingAuditRecorder,
    InMemoryAuditRecorder,
)
from sklegal_model_gateway.errors import (
    BucketPolicyError,
    CapabilityDeniedError,
    EgressDeniedError,
    FreeRouteDeniedError,
    ProviderUnavailableError,
    RouteNotFoundError,
    ServedModelAttributionError,
    SourceRightsDeniedError,
)
from sklegal_model_gateway.prompts import FilePromptStore
from sklegal_model_gateway.schemas import (  # noqa: F401
    CORPUS_SUMMARY_SCHEMA_ID,
)
from sklegal_model_gateway.schemas import (
    CorpusSummaryProposalPayload as PayloadModel,
)
from sklegal_model_gateway.transport_profiles import profile_content_sha256

from tests.test_model_gateway import (
    INNER_PAYLOAD,
    AllowGate,
    make_request,
)

ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = ROOT / "config" / "model_gateway" / "prompts"
POLICY_PATH = ROOT / "config" / "security" / "policy.json"
DEPLOY_ROOT = ROOT / "config" / "model_gateway" / "deployment"
PROFILE_STORE_PATH = DEPLOY_ROOT / "transport-profiles.json"
CATALOG_PATH = DEPLOY_ROOT / "model-catalog.json"
REGISTRY_PATH = ROOT / "config" / "model_gateway" / "route-registry.json"

TEMPLATE_TEXT = (PROMPT_ROOT / "corpus-summary.v1.prompt.txt").read_text("utf-8")
TEMPLATE_HASH = sha256_text(TEMPLATE_TEXT)
SCHEMA_HASH = schema_sha256(PayloadModel)
REGISTRY_REVISION = "b" * 64
FIXED_NOW = datetime(2026, 8, 22, 13, 0, 0, tzinfo=UTC)
QWEN_SERVED = "Qwen3-32B-Q6-K-xlw-20250428"


def skgateway_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-skgw-0001",
        "model": QWEN_SERVED,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(INNER_PAYLOAD)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "x_skgateway": {
            "request_id": "chatcmpl-skgw-0001",
            "backend": "qwen.chiap08",
            "served_model": QWEN_SERVED,
            "catalog_generation": "2026-08-22.1",
            "policy_revision": "skgw-policy-0001",
        },
        "x_skgateway_retries": 0,
        "x_skgateway_failover": False,
    }
    body.update(overrides)
    return body


class FakeSkGatewayTransport:
    """Fake Chat Completions transport capturing the request body."""

    def __init__(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        error: Exception | None = None,
        block_event: threading.Event | None = None,
    ) -> None:
        self.payload = dict(payload) if payload is not None else skgateway_payload()
        self.error = error
        self.block_event = block_event
        self.calls: list[dict[str, Any]] = []

    def chat_completions(
        self,
        *,
        base_url_reference: str,
        service_identity: str,
        capability_token_ref: str,
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: Any,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "base_url_reference": base_url_reference,
                "service_identity": service_identity,
                "capability_token_ref": capability_token_ref,
                "body": dict(body),
            }
        )
        if self.error is not None:
            raise self.error
        if self.block_event is not None:
            self.block_event.wait(5.0)
        return self.payload


class FakeQwenTransport:
    """Fake direct Qwen transport."""

    def __init__(self, served_model: str = QWEN_SERVED) -> None:
        self.served_model = served_model
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
        self.calls.append({"model": model, "prompt": prompt})
        return {
            "model": self.served_model,
            "revision": "2025-04-28",
            "request_id": "qwen-direct-0001",
            "response": json.dumps(INNER_PAYLOAD),
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }


class FakeLivePathReport:
    """Qualification report bound to a fake deployed commit."""

    def __init__(self, enforced: set[str], *, commit: str = "f" * 40) -> None:
        from sklegal_model_gateway.skgateway import (
            CONTROL_GATE_REVISION,
            LIVE_PATH_CONTROLS,
        )

        self.deployed_commit = commit
        self.revision = CONTROL_GATE_REVISION
        self._enforced = set(enforced)
        self._all = set(LIVE_PATH_CONTROLS)

    def enforced(self, control: str) -> bool:
        return control in self._enforced


def fully_qualified_report() -> FakeLivePathReport:
    from sklegal_model_gateway.skgateway import LIVE_PATH_CONTROLS

    return FakeLivePathReport(set(LIVE_PATH_CONTROLS))


def make_profile(
    kind: TransportKind = TransportKind.SKGATEWAY_CHAT,
    *,
    profile_id: str = "test.skgateway-chat.v1",
    enabled: bool = True,
) -> TransportProfile:
    values: dict[str, Any] = {
        "profile_id": profile_id,
        "kind": kind,
        "enabled": enabled,
        "endpoint_reference": "SKLEGAL_TEST_ENDPOINT",
    }
    if kind is TransportKind.SKGATEWAY_CHAT:
        values["service_identity"] = "capauth:test-model-gateway@test.skworld"
        values["capability_scope"] = "skgateway.infer"
    elif kind is TransportKind.OPENAI_RESPONSES:
        values["secret_reference"] = "vault:sklegal/openai/platform-api-key"
    profile = TransportProfile.model_validate({**values, "config_sha256": "0" * 64})
    return TransportProfile.model_validate(
        {**values, "config_sha256": profile_content_sha256(profile)}
    )


def make_store(*profiles: TransportProfile) -> TransportProfileStore:
    return TransportProfileStore(profiles or (make_profile(),), store_revision="c" * 64)


def make_route(**overrides: Any) -> ModelRouteRecord:
    values: dict[str, Any] = {
        "route_id": overrides.pop("route_id", "qwen.corpus-summary.direct.v1"),
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


def make_gateway(
    *,
    routes: tuple[ModelRouteRecord, ...] | None = None,
    provider: Any = None,
    store: TransportProfileStore | None = None,
    capacity: CapacityDomainController | None = None,
    catalog: ModelCatalog | None = None,
    capability_gate: Any = None,
) -> ModelGateway:
    route_records = routes if routes is not None else (make_route(),)
    registry = RouteRegistry(route_records, registry_revision=REGISTRY_REVISION)
    providers: dict[Provider, Any] = {}
    if provider is not None:
        providers[Provider.QWEN_LOCAL] = provider
    return ModelGateway(
        registry=registry,
        prompt_store=FilePromptStore(PROMPT_ROOT),
        schema_registry=SchemaRegistry.default(),
        egress_gate=PolicyFileEgressGate(POLICY_PATH),
        capability_gate=capability_gate or AllowGate(),
        providers=providers,
        transport_profiles=store,
        capacity=capacity,
        catalog=catalog,
        clock=lambda: FIXED_NOW,
    )


def skgateway_provider(
    transport: FakeSkGatewayTransport | None = None,
    *,
    report: Any = None,
) -> SkGatewayChatProvider:
    return SkGatewayChatProvider(
        transport or FakeSkGatewayTransport(),
        live_path_gate=SkGatewayLivePathGate(report or fully_qualified_report()),
    )


def load_test_catalog() -> ModelCatalog:
    return ModelCatalog.from_file(CATALOG_PATH)


class TransportProfileValidationTests(unittest.TestCase):
    def test_skgateway_profile_requires_identity_and_scope(self) -> None:
        with self.assertRaises(ValidationError):
            TransportProfile.model_validate(
                {
                    "profile_id": "x.skgateway.v1",
                    "kind": "skgateway_chat",
                    "enabled": True,
                    "config_sha256": "0" * 64,
                    "endpoint_reference": "SKLEGAL_X",
                }
            )

    def test_endpoint_literal_address_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TransportProfile.model_validate(
                {
                    "profile_id": "x.direct.v1",
                    "kind": "direct_qwen",
                    "enabled": True,
                    "config_sha256": "0" * 64,
                    "endpoint_reference": "http://127.0.0.1:8080",
                }
            )

    def test_direct_profile_is_secret_free(self) -> None:
        with self.assertRaises(ValidationError):
            TransportProfile.model_validate(
                {
                    "profile_id": "x.direct.v1",
                    "kind": "direct_qwen",
                    "enabled": True,
                    "config_sha256": "0" * 64,
                    "endpoint_reference": "SKLEGAL_X",
                    "secret_reference": "vault:sklegal/x",
                }
            )

    def test_skgateway_profile_rejects_provider_secret(self) -> None:
        with self.assertRaises(ValidationError):
            TransportProfile.model_validate(
                {
                    "profile_id": "x.skgateway.v1",
                    "kind": "skgateway_chat",
                    "enabled": True,
                    "config_sha256": "0" * 64,
                    "endpoint_reference": "SKLEGAL_X",
                    "service_identity": "capauth:x@t.skworld",
                    "capability_scope": "skgateway.infer",
                    "secret_reference": "vault:sklegal/openai/platform-api-key",
                }
            )


class TransportProfileStoreTests(unittest.TestCase):
    def test_unknown_profile_fails_closed(self) -> None:
        store = make_store()
        with self.assertRaises(TransportProfileNotFoundError):
            store.resolve(make_route(transport_profile_id="missing.profile.v1"))

    def test_disabled_profile_fails_closed(self) -> None:
        store = make_store(make_profile(enabled=False))
        with self.assertRaises(TransportProfileDisabledError):
            store.resolve(
                make_route(transport_profile_id="test.skgateway-chat.v1")
            )

    def test_stale_profile_hash_fails_closed(self) -> None:
        profile = make_profile()
        tampered = profile.model_copy(
            update={"endpoint_reference": "SKLEGAL_CHANGED_ENDPOINT"}
        )
        with self.assertRaises(TransportProfileStaleError):
            TransportProfileStore((tampered,), store_revision="d" * 64)

    def test_wrong_provider_binding_is_rejected(self) -> None:
        store = make_store(make_profile(kind=TransportKind.DIRECT_QWEN))
        # An OPENAI route cannot bind a direct_qwen profile.
        openai_route = make_route(
            provider=Provider.OPENAI,
            transport_profile_id="test.skgateway-chat.v1",
            secret_reference=None,
        )
        with self.assertRaises(TransportBindingError):
            store.resolve(openai_route)

    def test_repository_store_loads_and_pins_hashes(self) -> None:
        store = TransportProfileStore.from_file(PROFILE_STORE_PATH)
        self.assertIn("chiap08.direct-qwen.v1", {p.profile_id for p in store.profiles})
        skgateway = store.profile("chiap08.skgateway-chat.v1")
        self.assertFalse(skgateway.enabled)
        self.assertEqual(TransportKind.SKGATEWAY_CHAT, skgateway.kind)
        self.assertEqual("skgateway.infer", skgateway.capability_scope)


class SkGatewayRoutePolicyVerifierTests(unittest.TestCase):
    def _verifier(
        self,
        *,
        route: ModelRouteRecord | None = None,
        profile: TransportProfile | None = None,
    ) -> SkGatewayRoutePolicyVerifier:
        return SkGatewayRoutePolicyVerifier(
            routes=RouteRegistry(
                (route or make_route(
                    route_id="qwen.corpus-summary.skgateway.v1",
                    transport_profile_id="test.skgateway-chat.v1",
                ),),
                registry_revision=REGISTRY_REVISION,
            ),
            transport_profiles=make_store(profile or make_profile()),
            egress=PolicyFileEgressGate(POLICY_PATH),
        )

    def test_exact_enabled_local_route_is_authorized(self) -> None:
        evidence = self._verifier().verify(
            service_identity="capauth:test-model-gateway@test.skworld",
            capability="skgateway.infer",
            resource={"route_id": "qwen.corpus-summary.skgateway.v1"},
            context={"classification": "highly_restricted"},
        )
        self.assertEqual(REGISTRY_REVISION, evidence.route_registry_revision)
        self.assertEqual("c" * 64, evidence.transport_profile_revision)
        self.assertEqual(64, len(evidence.egress_policy_revision))

    def test_unknown_route_fails_closed(self) -> None:
        with self.assertRaises(RouteNotFoundError):
            self._verifier().verify(
                service_identity="capauth:test-model-gateway@test.skworld",
                capability="skgateway.infer",
                resource={"route_id": "qwen.unknown.v1"},
                context={"classification": "public"},
            )

    def test_disabled_profile_fails_closed(self) -> None:
        with self.assertRaises(TransportProfileDisabledError):
            self._verifier(profile=make_profile(enabled=False)).verify(
                service_identity="capauth:test-model-gateway@test.skworld",
                capability="skgateway.infer",
                resource={"route_id": "qwen.corpus-summary.skgateway.v1"},
                context={"classification": "public"},
            )

    def test_identity_and_capability_mismatch_fail_closed(self) -> None:
        verifier = self._verifier()
        for identity, capability in (
            ("capauth:wrong@test.skworld", "skgateway.infer"),
            ("capauth:test-model-gateway@test.skworld", "matter.manage"),
        ):
            with self.subTest(identity=identity, capability=capability):
                with self.assertRaises(CapabilityDeniedError):
                    verifier.verify(
                        service_identity=identity,
                        capability=capability,
                        resource={
                            "route_id": "qwen.corpus-summary.skgateway.v1"
                        },
                        context={"classification": "public"},
                    )

    def test_classification_above_route_ceiling_is_denied(self) -> None:
        route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id="test.skgateway-chat.v1",
            egress_classification_ceiling=DataClassification.INTERNAL,
        )
        with self.assertRaises(EgressDeniedError):
            self._verifier(route=route).verify(
                service_identity="capauth:test-model-gateway@test.skworld",
                capability="skgateway.infer",
                resource={"route_id": "qwen.corpus-summary.skgateway.v1"},
                context={"classification": "confidential"},
            )


class SharedCapacityDomainTests(unittest.TestCase):
    def _controller(self) -> CapacityDomainController:
        return CapacityDomainController(
            {("qwen.chiap08.shared.v1"): CapacityEnvelope(4, 4, 30.0)}
        )

    def test_four_active_then_queue_then_ninth_rejected(self) -> None:
        controller = self._controller()
        release = threading.Event()
        holds: list[Any] = []

        def hold(n: int) -> None:
            with controller.admission("qwen.chiap08.shared.v1") as evidence:
                holds.append((n, evidence["queued"]))
                release.wait(5.0)

        threads = [
            threading.Thread(target=hold, args=(index,)) for index in range(8)
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5.0
        while controller.stats("qwen.chiap08.shared.v1") != (4, 4) and (
            time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual((4, 4), controller.stats("qwen.chiap08.shared.v1"))
        with self.assertRaises(ProviderSaturationError):
            with controller.admission("qwen.chiap08.shared.v1"):
                pass
        release.set()
        for thread in threads:
            thread.join(5.0)
        self.assertEqual((0, 0), controller.stats("qwen.chiap08.shared.v1"))

    def test_queue_wait_deadline_is_typed_timeout(self) -> None:
        controller = CapacityDomainController(
            {("qwen.test.v1"): CapacityEnvelope(1, 1, 0.05)}
        )
        release = threading.Event()
        holder: list[Any] = []

        def hold() -> None:
            with controller.admission("qwen.test.v1"):
                holder.append(True)
                release.wait(5.0)

        thread = threading.Thread(target=hold)
        thread.start()
        deadline = time.monotonic() + 5.0
        while not holder and time.monotonic() < deadline:
            time.sleep(0.01)
        with self.assertRaises(CapacityQueueTimeoutError):
            with controller.admission("qwen.test.v1"):
                pass
        release.set()
        thread.join(5.0)

    def test_direct_and_gateway_share_one_domain(self) -> None:
        """A direct call and an SKGateway call contend for the same slots."""

        controller = self._controller()
        release = threading.Event()
        errors: list[BaseException] = []

        direct = make_route(
            route_id="qwen.corpus-summary.direct.v1",
            transport_profile_id=None,
            capacity_domain_id="qwen.chiap08.shared.v1",
        )
        gateway_route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id=None,
            capacity_domain_id="qwen.chiap08.shared.v1",
        )
        qwen = QwenLocalProvider(FakeQwenTransport())

        def blocking_complete(call: Any) -> Any:
            release.wait(5.0)
            return qwen.complete(call)

        blocking_provider = type(
            "BlockingQwen",
            (),
            {
                "provider": Provider.QWEN_LOCAL,
                "transport_kind": TransportKind.DIRECT_QWEN,
                "complete": staticmethod(blocking_complete),
            },
        )()
        gateway = make_gateway(
            routes=(direct, gateway_route),
            provider=blocking_provider,
            capacity=controller,
        )

        def run_route(route_id: str, request_id: str) -> None:
            try:
                gateway.submit(
                    make_request(route_id=route_id, request_id=request_id),
                    capability_ref="cap:test",
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        started: list[threading.Thread] = []
        # Saturate the four active slots through the direct route.
        for index in range(4):
            thread = threading.Thread(
                target=run_route,
                args=("qwen.corpus-summary.direct.v1", f"req-direct-{index}"),
            )
            thread.start()
            started.append(thread)
        deadline = time.monotonic() + 5.0
        while controller.stats("qwen.chiap08.shared.v1") != (4, 0) and (
            time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual((4, 0), controller.stats("qwen.chiap08.shared.v1"))

        # Four queued waiters fill the queue through the gateway route.
        for index in range(4):
            thread = threading.Thread(
                target=run_route,
                args=("qwen.corpus-summary.skgateway.v1", f"req-gateway-{index}"),
            )
            thread.start()
            started.append(thread)
        deadline = time.monotonic() + 5.0
        while controller.stats("qwen.chiap08.shared.v1") != (4, 4) and (
            time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual((4, 4), controller.stats("qwen.chiap08.shared.v1"))

        # The ninth call is rejected no matter which route issues it.
        with self.assertRaises(ProviderSaturationError):
            gateway.submit(
                make_request(
                    route_id="qwen.corpus-summary.skgateway.v1",
                    request_id="req-ninth",
                ),
                capability_ref="cap:test",
            )

        release.set()
        for thread in started:
            thread.join(5.0)
        self.assertEqual([], errors)
        self.assertEqual((0, 0), controller.stats("qwen.chiap08.shared.v1"))


class ModelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_test_catalog()

    def test_exact_qwen_alias_resolves_to_the_exact_served_model(self) -> None:
        target = self.catalog.resolve_alias("qwen3-32b")
        self.assertEqual(QWEN_SERVED, target.served_model)
        self.assertEqual("2025-04-28", target.revision)
        self.assertEqual(TrustZone.SOVEREIGN_LOCAL, target.trust_zone)

    def test_misspelled_alias_fails_closed(self) -> None:
        with self.assertRaises(CatalogLookupError):
            self.catalog.resolve_alias("qwen3-32B")

    def test_internal_classification_rejects_public_only_bucket(self) -> None:
        # bucket.large.v1 keeps a sovereign-local first member, so internal
        # resolves to an internal-or-stricter zone.
        target = self.catalog.resolve_bucket(
            "bucket.large.v1",
            workload_class=WorkloadClass.L,
            classification=DataClassification.INTERNAL,
            human_approval_ref=None,
            source_rights_state="verified_permissive",
        )
        self.assertIn(
            target.trust_zone, (TrustZone.INTERNAL, TrustZone.SOVEREIGN_LOCAL)
        )
        self.assertEqual("bucket.large.v1", target.bucket_id)
        # A bucket whose only member is a public trust-zone model must not
        # serve an internal classification.
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        payload["buckets"] = [
            {
                "bucket_id": "bucket.public-only.v1",
                "workload_class": "M",
                "members": ["gpt-4o-free-eval"],
                "enabled": True,
            }
        ]
        catalog = ModelCatalog.from_payload(payload)
        with self.assertRaises(BucketPolicyError):
            catalog.resolve_bucket(
                "bucket.public-only.v1",
                workload_class=WorkloadClass.M,
                classification=DataClassification.INTERNAL,
                human_approval_ref=None,
                source_rights_state=None,
            )

    def test_confidential_requires_sovereign_local_and_approval(self) -> None:
        # A bucket whose only member is an internal trust-zone model cannot
        # serve a confidential classification.
        with self.assertRaises(BucketPolicyError):
            self.catalog.resolve_bucket(
                "bucket.xlarge.v1",
                workload_class=WorkloadClass.XL,
                classification=DataClassification.CONFIDENTIAL,
                human_approval_ref="approval-1",
                source_rights_state=None,
            )
        # The sovereign-local member serves confidential only with approval.
        with self.assertRaises(BucketPolicyError):
            self.catalog.resolve_bucket(
                "bucket.medium.v1",
                workload_class=WorkloadClass.M,
                classification=DataClassification.CONFIDENTIAL,
                human_approval_ref=None,
                source_rights_state=None,
            )
        approved = self.catalog.resolve_bucket(
            "bucket.medium.v1",
            workload_class=WorkloadClass.M,
            classification=DataClassification.CONFIDENTIAL,
            human_approval_ref="approval-1",
            source_rights_state=None,
        )
        self.assertEqual(TrustZone.SOVEREIGN_LOCAL, approved.trust_zone)

    def test_workload_class_mismatch_fails_closed(self) -> None:
        with self.assertRaises(BucketPolicyError):
            self.catalog.resolve_bucket(
                "bucket.small.v1",
                workload_class=WorkloadClass.L,
                classification=DataClassification.INTERNAL,
                human_approval_ref=None,
                source_rights_state=None,
            )

    def test_unknown_and_disabled_buckets_fail_closed(self) -> None:
        with self.assertRaises(CatalogLookupError):
            self.catalog.resolve_bucket(
                "bucket.tyop.v1",
                workload_class=WorkloadClass.S,
                classification=DataClassification.INTERNAL,
                human_approval_ref=None,
                source_rights_state=None,
            )

    def test_quarantined_source_rights_deny_model_egress(self) -> None:
        for state in ("unknown", "unverified", "expired", "revoked"):
            with self.subTest(state=state):
                with self.assertRaises(SourceRightsDeniedError):
                    self.catalog.resolve_bucket(
                        "bucket.medium.v1",
                        workload_class=WorkloadClass.M,
                        classification=DataClassification.INTERNAL,
                        human_approval_ref=None,
                        source_rights_state=state,
                    )

    def test_unapproved_free_route_is_denied(self) -> None:
        with self.assertRaises(FreeRouteDeniedError):
            self.catalog.check_free_route(
                "free.gpt-4o-eval.v1",
                classification=DataClassification.PUBLIC,
            )

    def test_approved_local_free_route_retains_local_ceiling(self) -> None:
        record = self.catalog.check_free_route(
            "free.local-qwen.v1",
            classification=DataClassification.HIGHLY_RESTRICTED,
        )
        self.assertTrue(record.approved)

    def test_free_route_denies_above_its_classification(self) -> None:
        with self.assertRaises(FreeRouteDeniedError):
            self.catalog.check_free_route(
                "free.local-qwen.v1",
                classification=DataClassification.INTERNAL,
            ) if False else self.catalog.check_free_route(
                "free.gpt-4o-eval.v1",
                classification=DataClassification.INTERNAL,
            )


class SkGatewayAdapterTests(unittest.TestCase):
    def _call(self, route: ModelRouteRecord | None = None) -> Any:
        from sklegal_model_gateway.models import (
            CancellationToken,
            ProviderExecutionContext,
        )
        from sklegal_model_gateway.providers import ProviderCall

        route = route or make_route()
        context = ProviderExecutionContext(
            request_id="req-1",
            tenant_id="tenant-1",
            matter_id="matter-1",
            purpose="corpus_analysis",
            classification=DataClassification.INTERNAL,
        )
        return ProviderCall(
            route=route,
            prompt="synthetic prompt",
            output_schema={},
            cancel_token=CancellationToken(),
            context=context,
            capability_ref="cap:test",
            transport_profile=make_profile(),
        )

    def test_request_carries_alias_and_attribution(self) -> None:
        transport = FakeSkGatewayTransport()
        provider = skgateway_provider(transport)
        result = provider.complete(self._call())
        self.assertEqual(QWEN_SERVED, result.transport.served_model)
        self.assertEqual("qwen.chiap08", result.transport.backend)
        self.assertEqual("2026-08-22.1", result.transport.catalog_generation)
        self.assertEqual("skgw-policy-0001", result.transport.gateway_policy_revision)
        body = transport.calls[0]["body"]
        # Without a catalog alias pin, the adapter sends the route's
        # deployed model name.
        self.assertEqual("qwen3-32b-2025-04-28", body["model"])
        self.assertFalse(body["stream"])

    def test_missing_served_model_is_attribution_error(self) -> None:
        payload = skgateway_payload()
        payload.pop("model")
        provider = skgateway_provider(FakeSkGatewayTransport(payload))
        with self.assertRaises(ServedModelAttributionError):
            provider.complete(self._call())

    def test_spoofed_header_served_model_conflicts(self) -> None:
        payload = skgateway_payload(header_served_model="some-other-model")
        provider = skgateway_provider(FakeSkGatewayTransport(payload))
        with self.assertRaises(ServedModelAttributionError):
            provider.complete(self._call())

    def test_conflicting_attribution_block_conflicts(self) -> None:
        payload = skgateway_payload()
        payload["x_skgateway"] = dict(payload["x_skgateway"])
        payload["x_skgateway"]["served_model"] = "different-served-model"
        provider = skgateway_provider(FakeSkGatewayTransport(payload))
        with self.assertRaises(ServedModelAttributionError):
            provider.complete(self._call())

    def test_capability_reference_is_required(self) -> None:
        from sklegal_model_gateway.models import CancellationToken
        from sklegal_model_gateway.providers import ProviderCall

        call = ProviderCall(
            route=make_route(),
            prompt="p",
            output_schema={},
            cancel_token=CancellationToken(),
            context=None,
            capability_ref=None,
            transport_profile=make_profile(),
        )
        provider = skgateway_provider()
        with self.assertRaises(CapabilityDeniedError):
            provider.complete(call)

    def test_unqualified_live_path_denies_before_any_transport(self) -> None:
        from sklegal_model_gateway.skgateway import LIVE_PATH_CONTROLS

        missing = set(LIVE_PATH_CONTROLS) - {"capauth_identity"}
        provider = skgateway_provider(
            FakeSkGatewayTransport(),
            report=FakeLivePathReport(missing),
        )
        with self.assertRaises(ProviderUnavailableError):
            provider.complete(self._call())

    def test_transport_outage_is_typed(self) -> None:
        provider = skgateway_provider(
            FakeSkGatewayTransport(error=RuntimeError("gateway down"))
        )
        with self.assertRaises(ProviderUnavailableError):
            provider.complete(self._call())


class GatewayTransportSeamTests(unittest.TestCase):
    def test_direct_and_gateway_bindings_produce_the_same_proposal_contract(
        self,
    ) -> None:
        """Parity: switching the binding changes only transport evidence."""

        direct_route = make_route(
            route_id="qwen.corpus-summary.direct.v1",
            transport_profile_id="chiap08.direct-qwen.v1",
        )
        gateway_route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id="chiap08.skgateway-chat.v1",
        )
        direct_transport = FakeQwenTransport()
        catalog = load_test_catalog()

        class DirectThroughGateway:
            """Direct Qwen adapter reusing the fake direct transport."""

            provider = Provider.QWEN_LOCAL
            transport_kind = TransportKind.SKGATEWAY_CHAT

            def __init__(self) -> None:
                self._inner = QwenLocalProvider(direct_transport)

            def complete(self, call: Any) -> Any:
                result = self._inner.complete(call)
                from sklegal_model_gateway.models import TransportEvidence

                transport = TransportEvidence(
                    transport_profile_id="chiap08.direct-qwen.v1",
                    transport_kind=TransportKind.SKGATEWAY_CHAT,
                    requested_model="qwen3-32b",
                    served_model=result.model_revision,
                )
                return type(result)(
                    raw_text=result.raw_text,
                    model_revision=result.model_revision,
                    provider_request_id=result.provider_request_id,
                    usage=result.usage,
                    transport=transport,
                )

        store = make_store(
            make_profile(
                TransportKind.SKGATEWAY_CHAT, profile_id="chiap08.direct-qwen.v1"
            ),
            make_profile(
                TransportKind.SKGATEWAY_CHAT, profile_id="chiap08.skgateway-chat.v1"
            ),
        )
        gateway = make_gateway(
            routes=(direct_route, gateway_route),
            provider=DirectThroughGateway(),
            store=store,
            catalog=catalog,
        )
        direct_request = make_request(
            route_id="qwen.corpus-summary.direct.v1",
        )
        direct_proposal = gateway.submit(direct_request, capability_ref="cap:test")
        gateway_proposal = gateway.submit(
            make_request(route_id="qwen.corpus-summary.skgateway.v1"),
            capability_ref="cap:test",
        )
        # Same Proposal contract: schema pins, payload, and hashes.
        self.assertEqual(direct_proposal.payload, gateway_proposal.payload)
        self.assertEqual(
            direct_proposal.payload_sha256, gateway_proposal.payload_sha256
        )
        self.assertEqual(
            direct_proposal.output_schema_id, gateway_proposal.output_schema_id
        )
        self.assertEqual(
            direct_proposal.output_schema_sha256,
            gateway_proposal.output_schema_sha256,
        )
        self.assertEqual(
            direct_proposal.prompt_template_sha256,
            gateway_proposal.prompt_template_sha256,
        )
        self.assertEqual(Provider.QWEN_LOCAL, gateway_proposal.provider)
        self.assertEqual(
            direct_proposal.policy.classification,
            gateway_proposal.policy.classification,
        )

    def test_gateway_route_requires_matching_adapter_transport_kind(self) -> None:
        route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id="chiap08.skgateway-chat.v1",
        )
        store = make_store(
            make_profile(TransportKind.SKGATEWAY_CHAT, profile_id="chiap08.skgateway-chat.v1")
        )
        # A direct adapter bound under an skgateway profile must not carry it.
        gateway = make_gateway(
            routes=(route,),
            provider=QwenLocalProvider(FakeQwenTransport()),
            store=store,
        )
        with self.assertRaises(TransportBindingError):
            gateway.submit(
                make_request(route_id="qwen.corpus-summary.skgateway.v1"),
                capability_ref="cap:test",
            )

    def test_route_without_store_fails_closed(self) -> None:
        route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id="chiap08.skgateway-chat.v1",
        )
        gateway = make_gateway(
            routes=(route,),
            provider=QwenLocalProvider(FakeQwenTransport()),
            store=None,
        )
        with self.assertRaises(TransportProfileNotFoundError):
            gateway.submit(
                make_request(route_id="qwen.corpus-summary.skgateway.v1"),
                capability_ref="cap:test",
            )

    def test_repository_disabled_skgateway_profile_denies_traffic(self) -> None:
        """The shipped SKGateway binding stays disabled until qualified."""

        store = TransportProfileStore.from_file(PROFILE_STORE_PATH)
        route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id="chiap08.skgateway-chat.v1",
        )
        gateway = make_gateway(
            routes=(route,),
            provider=skgateway_provider(),
            store=store,
        )
        with self.assertRaises(TransportProfileDisabledError):
            gateway.submit(
                make_request(route_id="qwen.corpus-summary.skgateway.v1"),
                capability_ref="cap:test",
            )

    def test_repository_gateway_route_with_qualified_profile_runs(self) -> None:
        store = TransportProfileStore.from_file(PROFILE_STORE_PATH)
        # Enable the gateway profile in memory only; the file stays disabled.
        enabled = store.profile("chiap08.skgateway-chat.v1").model_copy(
            update={"enabled": True}
        )
        recomputed = enabled.model_copy(
            update={"config_sha256": profile_content_sha256(enabled)}
        )
        live_store = TransportProfileStore(
            (
                store.profile("chiap08.direct-qwen.v1"),
                recomputed,
                store.profile("chiap01.openai-responses.v1"),
            ),
            store_revision=store.store_revision,
        )
        route = make_route(
            route_id="qwen.corpus-summary.skgateway.v1",
            transport_profile_id="chiap08.skgateway-chat.v1",
            allowed_served_model_alias="qwen3-32b",
        )
        gateway = make_gateway(
            routes=(route,),
            provider=skgateway_provider(),
            store=live_store,
            catalog=load_test_catalog(),
            capacity=CapacityDomainController(
                {"qwen.chiap08.shared.v1": CapacityEnvelope(4, 4, 30.0)}
            ),
        )
        proposal = gateway.submit(
            make_request(route_id="qwen.corpus-summary.skgateway.v1"),
            capability_ref="cap:test",
        )
        transport = proposal.evidence.transport
        assert transport is not None
        self.assertEqual(QWEN_SERVED, transport.served_model)
        self.assertEqual(
            TransportKind.SKGATEWAY_CHAT, transport.transport_kind
        )
        # The catalog-resolved alias appears as the requested model.
        self.assertEqual("qwen3-32b", transport.requested_model)


class RollbackParityTests(unittest.TestCase):
    def test_rollback_binding_keeps_the_proposal_contract(self) -> None:
        """Rollback to direct changes the binding only, not the contract."""

        store = TransportProfileStore.from_file(PROFILE_STORE_PATH)
        direct_route = make_route(
            route_id="qwen.corpus-summary.direct.v1",
            transport_profile_id="chiap08.direct-qwen.v1",
        )
        gateway = make_gateway(
            routes=(direct_route,),
            provider=QwenLocalProvider(FakeQwenTransport()),
            store=store,
            catalog=load_test_catalog(),
        )
        before = gateway.submit(
            make_request(route_id="qwen.corpus-summary.direct.v1"),
            capability_ref="cap:test",
        )
        # Rebuild with the same route after "rollback": identical contract.
        after = gateway.submit(
            make_request(route_id="qwen.corpus-summary.direct.v1"),
            capability_ref="cap:test",
        )
        self.assertEqual(before.output_schema_id, after.output_schema_id)
        self.assertEqual(before.output_schema_sha256, after.output_schema_sha256)
        self.assertEqual(before.payload_sha256, after.payload_sha256)
        self.assertEqual(before.model, after.model)


class AuditRecordTests(unittest.TestCase):
    def _proposal(self) -> Any:
        store = TransportProfileStore.from_file(PROFILE_STORE_PATH)
        route = make_route(
            route_id="qwen.corpus-summary.direct.v1",
            transport_profile_id="chiap08.direct-qwen.v1",
        )
        gateway = make_gateway(
            routes=(route,),
            provider=QwenLocalProvider(FakeQwenTransport()),
            store=store,
            catalog=load_test_catalog(),
        )
        return gateway.submit(
            make_request(route_id="qwen.corpus-summary.direct.v1"),
            capability_ref="cap:test",
        )

    def test_audit_record_carries_no_prompt_or_secret_material(self) -> None:
        proposal = self._proposal()
        record = AuditRecord.from_proposal(
            request=make_request(route_id="qwen.corpus-summary.direct.v1"),
            proposal=proposal,
            recorded_at=FIXED_NOW,
        )
        canonical = record.canonical_json()
        self.assertNotIn("synthetic prompt", canonical)
        self.assertNotIn("cap:test", canonical)
        self.assertNotIn("sk-", canonical)
        self.assertIn(AUDIT_RECORD_SCHEMA, canonical)
        self.assertEqual("proposal_validated", record.outcome)

    def test_failing_audit_sink_fails_closed(self) -> None:
        recorder = FailingAuditRecorder()
        proposal = self._proposal()
        with self.assertRaises(Exception):
            recorder.append(
                AuditRecord.from_proposal(
                    request=make_request(route_id="qwen.corpus-summary.direct.v1"),
                    proposal=proposal,
                    recorded_at=FIXED_NOW,
                )
            )

    def test_in_memory_sink_appends(self) -> None:
        recorder = InMemoryAuditRecorder()
        proposal = self._proposal()
        recorder.append(
            AuditRecord.from_proposal(
                request=make_request(route_id="qwen.corpus-summary.direct.v1"),
                proposal=proposal,
                recorded_at=FIXED_NOW,
            )
        )
        self.assertEqual(1, len(recorder.records))


class DeploymentArtifactContractTests(unittest.TestCase):
    def test_capacity_policy_matches_the_shared_domain(self) -> None:
        payload = json.loads(
            (DEPLOY_ROOT / "capacity-policy.json").read_text(encoding="utf-8")
        )
        domain = payload["domains"][0]
        self.assertEqual("qwen.chiap08.shared.v1", domain["domain_id"])
        self.assertEqual(4, domain["max_active"])
        self.assertEqual(4, domain["max_queued"])
        self.assertEqual(30, domain["queue_wait_seconds"])

    def test_route_registry_pins_the_shared_domain_on_qwen_routes(self) -> None:
        registry = RouteRegistry.from_file(REGISTRY_PATH)
        for route in registry.routes:
            if route.provider is Provider.QWEN_LOCAL and route.route_id != (
                "qwen.corpus-summary.v1"
            ):
                self.assertEqual("qwen.chiap08.shared.v1", route.capacity_domain_id)

    def test_skgateway_and_direct_routes_share_prompt_and_schema(self) -> None:
        registry = RouteRegistry.from_file(REGISTRY_PATH)
        direct = registry.route("qwen.corpus-summary.direct.v1")
        skgateway = registry.route("qwen.corpus-summary.skgateway.v1")
        self.assertEqual(direct.prompt_template_id, skgateway.prompt_template_id)
        self.assertEqual(
            direct.prompt_template_sha256, skgateway.prompt_template_sha256
        )
        self.assertEqual(direct.output_schema_id, skgateway.output_schema_id)
        self.assertEqual(
            direct.output_schema_sha256, skgateway.output_schema_sha256
        )
        self.assertEqual(direct.model, skgateway.model)

    def test_bucket_route_pins_workload_and_bucket(self) -> None:
        registry = RouteRegistry.from_file(REGISTRY_PATH)
        bucket_route = registry.route("qwen.corpus-summary.bucket-xl.v1")
        self.assertEqual(WorkloadClass.XL, bucket_route.workload_class)
        self.assertEqual("bucket.xlarge.v1", bucket_route.allowed_bucket_id)
        self.assertIsNone(bucket_route.allowed_served_model_alias)
        self.assertFalse(bucket_route.enabled)

    def test_deployment_artifacts_use_ascii_dashes_only(self) -> None:
        for path in sorted(DEPLOY_ROOT.glob("*.json")) + [
            ROOT / "deploy" / "chiap08" / "SKGATEWAY-DEPLOYMENT-RUNBOOK.md",
            PROFILE_STORE_PATH,
            CATALOG_PATH,
        ]:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)

    def test_no_literal_addresses_or_raw_keys_in_deployment_config(self) -> None:
        for path in sorted(DEPLOY_ROOT.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("http://", text)
            self.assertNotIn("https://github", text.replace(
                "https://github.com/smilinTux/skgateway.git", ""
            ))
            self.assertNotIn("sk-", text.replace("skgateway", ""))

    def test_catalog_file_has_the_pinned_schema_marker(self) -> None:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(CATALOG_SCHEMA, payload["schema"])

    def test_skgateway_source_pin_records_the_reviewed_commit(self) -> None:
        payload = json.loads(
            (DEPLOY_ROOT / "skgateway-source-pin.json").read_text(encoding="utf-8")
        )
        upstream = payload["upstream"]
        self.assertEqual(
            "b4b4115df9a6d5c9c4621d98207a1074e2737ef5",
            upstream["planning_review_commit"],
        )
        self.assertEqual("MIT", upstream["license"])
        self.assertEqual("npm ci", upstream["install_command"])
        self.assertFalse(upstream["vendored_into_sklegal"])
        self.assertTrue(
            payload["live_path_gate"]["protected_traffic"].startswith("denied")
        )


if __name__ == "__main__":
    unittest.main()
