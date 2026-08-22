"""Contract tests for versioned agent specifications (SKL-S3-03 slice A).

Covers schema validation, version immutability, hash pinning, and
rejection of unbounded authority at definition time. No tool gateway and
no live model calls are exercised in this slice.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from sklegal_agents import (
    KNOWN_TOOL_IDS,
    MAX_RETRIES,
    MAX_TOOL_CALLS_PER_RUN,
    MAX_WALL_CLOCK_SECONDS,
    SPEC_SCHEMA,
    AgentSpecRegistry,
    SpecIntegrityError,
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionImmutableError,
    UnboundedAuthorityError,
    UnknownModelRouteError,
    UnknownToolError,
)
from sklegal_model_gateway import RouteRegistry

ROOT = Path(__file__).resolve().parents[1]
ROUTE_REGISTRY_PATH = ROOT / "config" / "model_gateway" / "route-registry.json"
SPECS_DIR = ROOT / "agents" / "specs"
SCHEMAS_DIR = ROOT / "agents" / "schemas"
EXAMPLE_SPEC_PATH = SPECS_DIR / "corpus-analyst.v1.json"
SPEC_SCHEMA_PATH = SCHEMAS_DIR / "agent-spec.v1.schema.json"
INPUT_SCHEMA_PATH = SCHEMAS_DIR / "corpus-summary-input.v1.schema.json"


def spec_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


class SpecRegistryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.route_registry = RouteRegistry.from_file(ROUTE_REGISTRY_PATH)
        self.example_payload = json.loads(EXAMPLE_SPEC_PATH.read_text(encoding="utf-8"))

    def write_spec(self, directory: Path, name: str, payload: dict) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def load_payload(self, payload: dict, name: str = "candidate.v1.json"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = self.write_spec(Path(tmp.name), name, payload)
        return AgentSpecRegistry.from_files([path], route_registry=self.route_registry)


class ExampleSpecContractTests(SpecRegistryFixture):
    def test_example_spec_loads_and_is_hash_pinned(self) -> None:
        registry = AgentSpecRegistry.from_directory(
            SPECS_DIR, route_registry=self.route_registry
        )
        record = registry.spec("corpus-analyst")
        self.assertEqual(1, record.spec.version)
        self.assertEqual(spec_file_sha256(EXAMPLE_SPEC_PATH), record.spec_sha256)
        self.assertEqual(("qwen.corpus-summary.v1",), record.spec.model_routes)

    def test_example_spec_pins_real_schema_artifacts(self) -> None:
        registry = AgentSpecRegistry.from_directory(
            SPECS_DIR, route_registry=self.route_registry
        )
        record = registry.spec("corpus-analyst")
        self.assertEqual(
            spec_file_sha256(INPUT_SCHEMA_PATH), record.spec.input_schema.sha256
        )
        route = self.route_registry.route("qwen.corpus-summary.v1")
        self.assertEqual(route.output_schema_id, record.spec.output_schema.schema_id)
        self.assertEqual(route.output_schema_sha256, record.spec.output_schema.sha256)

    def test_expected_hash_manifest_is_enforced(self) -> None:
        manifest = {EXAMPLE_SPEC_PATH.name: spec_file_sha256(EXAMPLE_SPEC_PATH)}
        registry = AgentSpecRegistry.from_directory(
            SPECS_DIR,
            route_registry=self.route_registry,
            expected_hashes=manifest,
        )
        self.assertEqual(1, len(registry.records))

    def test_manifest_hash_mismatch_fails_closed(self) -> None:
        manifest = {EXAMPLE_SPEC_PATH.name: "0" * 64}
        with self.assertRaises(SpecIntegrityError):
            AgentSpecRegistry.from_directory(
                SPECS_DIR,
                route_registry=self.route_registry,
                expected_hashes=manifest,
            )

    def test_manifest_must_cover_every_spec_file(self) -> None:
        with self.assertRaises(SpecIntegrityError):
            AgentSpecRegistry.from_directory(
                SPECS_DIR,
                route_registry=self.route_registry,
                expected_hashes={},
            )


class SchemaValidationTests(SpecRegistryFixture):
    def test_missing_schema_marker_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        del payload["schema"]
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)

    def test_unknown_schema_marker_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["schema"] = "sklegal-agent-spec/v99"
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)

    def test_missing_required_field_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        del payload["budgets"]
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)

    def test_extra_field_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["shell_access"] = True
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)

    def test_malformed_hash_pin_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["output_schema"]["sha256"] = "not-a-sha256"
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)

    def test_budget_outside_bounds_is_rejected(self) -> None:
        for field, value in (
            ("max_tool_calls", 0),
            ("max_tool_calls", MAX_TOOL_CALLS_PER_RUN + 1),
            ("max_wall_clock_seconds", MAX_WALL_CLOCK_SECONDS + 1),
            ("max_retries", MAX_RETRIES + 1),
        ):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.example_payload)
                payload["budgets"][field] = value
                with self.assertRaises(SpecValidationError):
                    self.load_payload(payload)

    def test_unknown_retry_class_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["retry_class"] = "unbounded"
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)

    def test_retry_class_must_match_pinned_routes(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["retry_class"] = "batch"
        with self.assertRaises(SpecValidationError):
            self.load_payload(payload)


class VersionImmutabilityTests(SpecRegistryFixture):
    def test_conflicting_document_cannot_claim_existing_version(self) -> None:
        registry = AgentSpecRegistry.from_files(
            [EXAMPLE_SPEC_PATH], route_registry=self.route_registry
        )
        changed = copy.deepcopy(self.example_payload)
        changed["task_purpose"] = "A changed purpose for the same version."
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_spec(Path(tmp), "corpus-analyst.v1.json", changed)
            with self.assertRaises(SpecVersionImmutableError):
                registry.with_spec_file(path, route_registry=self.route_registry)

    def test_identical_re_registration_is_idempotent(self) -> None:
        registry = AgentSpecRegistry.from_files(
            [EXAMPLE_SPEC_PATH], route_registry=self.route_registry
        )
        again = registry.with_spec_file(
            EXAMPLE_SPEC_PATH, route_registry=self.route_registry
        )
        self.assertIs(registry, again)

    def test_new_version_extends_without_replacing(self) -> None:
        registry = AgentSpecRegistry.from_files(
            [EXAMPLE_SPEC_PATH], route_registry=self.route_registry
        )
        v2 = copy.deepcopy(self.example_payload)
        v2["version"] = 2
        v2["task_purpose"] = "Revised purpose recorded as a new version."
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_spec(Path(tmp), "corpus-analyst.v2.json", v2)
            extended = registry.with_spec_file(path, route_registry=self.route_registry)
        self.assertEqual((1, 2), extended.versions("corpus-analyst"))
        original = extended.spec("corpus-analyst", version=1)
        self.assertEqual(spec_file_sha256(EXAMPLE_SPEC_PATH), original.spec_sha256)
        self.assertEqual(2, extended.spec("corpus-analyst").spec.version)

    def test_duplicate_conflicting_versions_in_one_load_are_rejected(self) -> None:
        changed = copy.deepcopy(self.example_payload)
        changed["task_purpose"] = "Conflicting same-version document."
        with tempfile.TemporaryDirectory() as tmp:
            first = self.write_spec(
                Path(tmp), "a.v1.json", copy.deepcopy(self.example_payload)
            )
            second = self.write_spec(Path(tmp), "b.v1.json", changed)
            with self.assertRaises(SpecVersionImmutableError):
                AgentSpecRegistry.from_files(
                    [first, second], route_registry=self.route_registry
                )

    def test_registry_records_are_immutable(self) -> None:
        registry = AgentSpecRegistry.from_files(
            [EXAMPLE_SPEC_PATH], route_registry=self.route_registry
        )
        record = registry.spec("corpus-analyst")
        with self.assertRaises(Exception):
            record.spec.task_purpose = "mutate"  # type: ignore[misc]
        with self.assertRaises(SpecNotFoundError):
            registry.spec("corpus-analyst", version=99)


class UnboundedAuthorityTests(SpecRegistryFixture):
    def test_unknown_tool_is_rejected_at_load(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["tool_allowlist"] = ["matter.read", "shell.exec"]
        with self.assertRaises(UnknownToolError):
            self.load_payload(payload)

    def test_wildcard_tool_authority_is_rejected(self) -> None:
        for wildcard in ("*", "evidence.*", "all?"):
            with self.subTest(wildcard=wildcard):
                payload = copy.deepcopy(self.example_payload)
                payload["tool_allowlist"] = [wildcard]
                with self.assertRaises(UnboundedAuthorityError):
                    self.load_payload(payload)

    def test_known_catalog_tools_are_accepted(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["tool_allowlist"] = sorted(KNOWN_TOOL_IDS)
        registry = self.load_payload(payload)
        record = registry.spec("corpus-analyst")
        self.assertEqual(tuple(sorted(KNOWN_TOOL_IDS)), record.spec.tool_allowlist)

    def test_tenant_wide_context_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["allowed_context"]["matter_scoped"] = False
        with self.assertRaises(UnboundedAuthorityError):
            self.load_payload(payload)

    def test_non_human_escalation_is_rejected(self) -> None:
        for target in ("agent.supervisor", "corpus-analyst"):
            with self.subTest(target=target):
                payload = copy.deepcopy(self.example_payload)
                payload["escalation_target"] = target
                with self.assertRaises(UnboundedAuthorityError):
                    self.load_payload(payload)

    def test_context_ceiling_cannot_exceed_route_egress_ceiling(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["model_routes"] = ["openai.corpus-summary.v1"]
        payload["allowed_context"]["classification_ceiling"] = "highly_restricted"
        with self.assertRaises(UnboundedAuthorityError):
            self.load_payload(payload)

    def test_unknown_model_route_is_rejected(self) -> None:
        payload = copy.deepcopy(self.example_payload)
        payload["model_routes"] = ["qwen.nonexistent.v1"]
        with self.assertRaises(UnknownModelRouteError):
            self.load_payload(payload)


class SchemaArtifactContractTests(SpecRegistryFixture):
    def setUp(self) -> None:
        super().setUp()
        self.schema = json.loads(SPEC_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_schema_artifact_mirrors_example_spec_shape(self) -> None:
        properties = set(self.schema["properties"])
        self.assertEqual(set(self.example_payload), properties)
        self.assertEqual(set(self.schema["required"]), properties)
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(SPEC_SCHEMA, self.schema["properties"]["schema"]["const"])

    def test_schema_artifact_structurally_rejects_unbounded_authority(self) -> None:
        budgets = self.schema["properties"]["budgets"]["properties"]
        self.assertEqual(MAX_TOOL_CALLS_PER_RUN, budgets["max_tool_calls"]["maximum"])
        self.assertEqual(
            MAX_WALL_CLOCK_SECONDS,
            budgets["max_wall_clock_seconds"]["maximum"],
        )
        self.assertEqual(MAX_RETRIES, budgets["max_retries"]["maximum"])
        context = self.schema["properties"]["allowed_context"]["properties"]
        self.assertIs(True, context["matter_scoped"]["const"])
        pattern = self.schema["properties"]["escalation_target"]["pattern"]
        self.assertTrue(pattern.startswith("^human\\."))


if __name__ == "__main__":
    unittest.main()
