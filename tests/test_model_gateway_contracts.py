"""Machine-readable contract tests for the model gateway (SKL-S3-02).

Keeps the route registry, prompt pins, schema pins, onboarding checklist,
and the paired development and security documents internally consistent.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from sklegal_domain import DataClassification
from sklegal_model_gateway import (
    CORPUS_SUMMARY_SCHEMA_ID,
    REGISTRY_SCHEMA,
    CorpusSummaryProposalPayload,
    Provider,
    RouteRegistry,
    schema_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "model_gateway" / "route-registry.json"
PROMPT_ROOT = ROOT / "config" / "model_gateway" / "prompts"
CHECKLIST_PATH = ROOT / "config" / "model_gateway" / "openai-onboarding-checklist.json"
POLICY_PATH = ROOT / "config" / "security" / "policy.json"
GATEWAY_DOC = ROOT / "docs" / "development" / "MODEL-GATEWAY.md"
ONBOARDING_DOC = ROOT / "docs" / "security" / "OPENAI-PLATFORM-ONBOARDING.md"

REQUIRED_CHECKLIST_ITEMS = {
    "organization_project_selection",
    "api_billing",
    "api_key_creation",
    "retention_settings",
    "spend_limits",
    "secret_storage",
    "key_rotation",
}


class RouteRegistryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RouteRegistry.from_file(REGISTRY_PATH)
        self.payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_registry_loads_with_pinned_schema_marker(self) -> None:
        self.assertEqual(REGISTRY_SCHEMA, self.payload["schema"])
        self.assertEqual(2, len(self.registry.routes))
        revision = hashlib.sha256(
            REGISTRY_PATH.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        self.assertEqual(revision, self.registry.registry_revision)

    def test_admission_contract_is_fail_fast_without_queueing(self) -> None:
        admission = self.payload["admission"]
        self.assertEqual("fail_fast_typed_saturation_error", admission["behavior"])
        self.assertEqual("none", admission["queueing"])
        for route in self.registry.routes:
            with self.subTest(route=route.route_id):
                self.assertEqual(4, route.max_concurrent)

    def test_every_route_pins_prompt_schema_budget_timeout_and_retry(self) -> None:
        for route in self.registry.routes:
            with self.subTest(route=route.route_id):
                self.assertTrue(route.enabled)
                self.assertGreater(route.context_token_budget, 0)
                self.assertGreater(route.max_output_tokens, 0)
                self.assertGreater(route.timeout_seconds, 0)
                self.assertEqual("model", route.retry_class)
                template_path = PROMPT_ROOT / f"{route.prompt_template_id}.prompt.txt"
                template = template_path.read_text(encoding="utf-8")
                digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
                self.assertEqual(digest, route.prompt_template_sha256)
                self.assertEqual(CORPUS_SUMMARY_SCHEMA_ID, route.output_schema_id)
                self.assertEqual(
                    schema_sha256(CorpusSummaryProposalPayload),
                    route.output_schema_sha256,
                )

    def test_pinned_routes_share_prompt_and_schema_for_parity(self) -> None:
        qwen = self.registry.route("qwen.corpus-summary.v1")
        openai = self.registry.route("openai.corpus-summary.v1")
        self.assertEqual(Provider.QWEN_LOCAL, qwen.provider)
        self.assertEqual(Provider.OPENAI, openai.provider)
        self.assertEqual(qwen.prompt_template_id, openai.prompt_template_id)
        self.assertEqual(qwen.prompt_template_sha256, openai.prompt_template_sha256)
        self.assertEqual(qwen.output_schema_id, openai.output_schema_id)
        self.assertEqual(qwen.output_schema_sha256, openai.output_schema_sha256)

    def test_egress_ceilings_match_the_security_policy_matrix(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        matrix = policy["external_model_egress"]
        self.assertEqual("deny", matrix["privileged_work_product"])
        self.assertEqual("deny", matrix["highly_restricted"])
        self.assertEqual("conditional_human_approval", matrix["confidential"])
        qwen = self.registry.route("qwen.corpus-summary.v1")
        openai = self.registry.route("openai.corpus-summary.v1")
        self.assertEqual(
            DataClassification.HIGHLY_RESTRICTED,
            qwen.egress_classification_ceiling,
        )
        self.assertEqual(
            DataClassification.CONFIDENTIAL,
            openai.egress_classification_ceiling,
        )

    def test_openai_route_uses_secret_reference_never_raw_key(self) -> None:
        openai = self.registry.route("openai.corpus-summary.v1")
        reference = openai.secret_reference
        assert reference is not None
        self.assertTrue(reference.startswith("vault:"))
        self.assertFalse(reference.startswith("sk-"))
        qwen = self.registry.route("qwen.corpus-summary.v1")
        self.assertIsNone(qwen.secret_reference)
        self.assertNotIn("sk-", REGISTRY_PATH.read_text(encoding="utf-8"))


class OnboardingChecklistContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checklist = json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))

    def test_checklist_covers_every_required_human_item(self) -> None:
        self.assertEqual(
            "sklegal-openai-onboarding-checklist/v1", self.checklist["schema"]
        )
        items = self.checklist["required_items"]
        self.assertEqual(REQUIRED_CHECKLIST_ITEMS, {item["item_id"] for item in items})
        for item in items:
            with self.subTest(item=item["item_id"]):
                self.assertEqual("human", item["owner"])
                self.assertTrue(item["description"])

    def test_consumer_chatgpt_is_never_an_application_credential(self) -> None:
        boundary = self.checklist["credential_boundary"]
        self.assertFalse(boundary["consumer_chatgpt_session_is_application_credential"])
        self.assertFalse(
            boundary["consumer_chatgpt_subscription_is_application_credential"]
        )
        self.assertIn("ChatGPT", boundary["statement"])
        self.assertIn("never an application credential", boundary["statement"])

    def test_checklist_and_onboarding_doc_are_aligned(self) -> None:
        doc = ONBOARDING_DOC.read_text(encoding="utf-8")
        normalized = " ".join(doc.split())
        for item in REQUIRED_CHECKLIST_ITEMS:
            with self.subTest(item=item):
                self.assertIn(item, json.dumps(self.checklist))
        for required in (
            "openai-onboarding-checklist.json",
            "never an application credential",
            "store=false",
            "Organization and project selection",
            "API billing",
            "API key creation",
            "Retention settings",
            "Spend limits",
            "Secret storage",
            "Key rotation",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)


class GatewayDocumentationContractTests(unittest.TestCase):
    def test_gateway_doc_links_registry_checklist_and_boundary(self) -> None:
        doc = GATEWAY_DOC.read_text(encoding="utf-8")
        normalized = " ".join(doc.split())
        for required in (
            "route-registry.json",
            "openai-onboarding-checklist.json",
            "OPENAI-PLATFORM-ONBOARDING.md",
            "ProviderSaturationError",
            "fail closed",
            "proposals",
            "never an application credential",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)

    def test_new_gateway_artifacts_use_ascii_dashes_only(self) -> None:
        paths = [
            GATEWAY_DOC,
            ONBOARDING_DOC,
            REGISTRY_PATH,
            CHECKLIST_PATH,
            PROMPT_ROOT / "corpus-summary.v1.prompt.txt",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)  # em dash
                self.assertNotIn("\u2013", text)  # en dash


if __name__ == "__main__":
    unittest.main()
