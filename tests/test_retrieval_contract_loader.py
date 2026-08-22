from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from sklegal_retrieval.contract import (
    DEFAULT_CONTRACT_PATH,
    EXPECTED_CONTRACT_SHA256,
    EXPECTED_QUERY_TEMPLATE_IDS,
    ContractValidationError,
    load_retrieval_contract,
)
from sklegal_retrieval.query_templates import (
    QUERY_TEMPLATES,
    QueryTemplateError,
    bind_query_template,
    get_query_template,
)


class RetrievalContractLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8"))

    def _write_contract(self, root: Path, document: object) -> Path:
        path = root / "tenant-partition-contract.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_default_contract_loads_as_a_typed_security_slice(self) -> None:
        contract = load_retrieval_contract()

        self.assertEqual("sklegal-retrieval-partition/v2", contract.schema)
        self.assertEqual(EXPECTED_QUERY_TEMPLATE_IDS, contract.query_template_ids)
        self.assertEqual(100, contract.parameter_limits.max_results)
        self.assertEqual(100, contract.parameter_limits.max_entity_ids)
        self.assertEqual(3, contract.parameter_limits.max_graph_depth)
        self.assertEqual(16_384, contract.parameter_limits.max_query_bytes)
        self.assertTrue(contract.graph_is_optional)
        self.assertEqual(
            EXPECTED_CONTRACT_SHA256,
            contract.file_sha256,
        )
        self.assertEqual(
            EXPECTED_CONTRACT_SHA256,
            hashlib.sha256(DEFAULT_CONTRACT_PATH.read_bytes()).hexdigest(),
        )
        self.assertFalse(hasattr(contract, "raw_document"))

    def test_loader_rejects_drift_outside_the_typed_security_slice(self) -> None:
        document = json.loads(json.dumps(self.document))
        document["supply_chain"]["sbom_required"] = False

        with tempfile.TemporaryDirectory() as directory:
            path = self._write_contract(Path(directory), document)
            with self.assertRaisesRegex(
                ContractValidationError,
                "digest is not approved",
            ):
                load_retrieval_contract(path)

    def test_loader_rejects_malformed_duplicate_and_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema":"first","schema":"second"}',
                encoding="utf-8",
            )
            non_object = root / "non-object.json"
            non_object.write_text("[]", encoding="utf-8")

            for path in (malformed, duplicate, non_object):
                with self.subTest(path=path.name):
                    with self.assertRaises(ContractValidationError):
                        load_retrieval_contract(path)

    def test_loader_rejects_missing_or_relaxed_security_gates(self) -> None:
        mutations = (
            lambda document: document.pop("schema"),
            lambda document: document["topology"].__setitem__(
                "live_skmem_pg_reuse", "allowed"
            ),
            lambda document: document["registry"].__setitem__(
                "caller_may_supply_physical_name", True
            ),
            lambda document: document["lexical"].__setitem__(
                "caller_may_supply_raw_sql", True
            ),
            lambda document: document["vector"].__setitem__(
                "caller_may_supply_raw_filter", True
            ),
            lambda document: document["graph"].__setitem__(
                "caller_may_supply_raw_cypher", True
            ),
            lambda document: document["graph"].__setitem__(
                "caller_may_supply_graph_name", True
            ),
            lambda document: document["query_scope"].__setitem__(
                "authorization_before_registry_read", False
            ),
            lambda document: document["legacy_backends"].__setitem__(
                "legacy_alias_is_routing_eligible", True
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, mutate in enumerate(mutations):
                document = json.loads(json.dumps(self.document))
                mutate(document)
                path = self._write_contract(root, document)
                with self.subTest(index=index):
                    with self.assertRaises(ContractValidationError):
                        load_retrieval_contract(path)

    def test_loader_rejects_template_or_limit_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_drift = json.loads(json.dumps(self.document))
            template_drift["query_scope"]["query_templates"].append("raw.sql.v1")
            with self.assertRaises(ContractValidationError):
                load_retrieval_contract(self._write_contract(root, template_drift))

            limit_drift = json.loads(json.dumps(self.document))
            limit_drift["query_scope"]["parameter_limits"]["max_results"] = 101
            with self.assertRaises(ContractValidationError):
                load_retrieval_contract(self._write_contract(root, limit_drift))

    def test_loader_rejects_symlink_and_non_path_input(self) -> None:
        with self.assertRaises(ContractValidationError):
            load_retrieval_contract(str(DEFAULT_CONTRACT_PATH))  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self._write_contract(root, self.document)
            link = root / "contract-link.json"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(ContractValidationError):
                load_retrieval_contract(link)


class ClosedQueryTemplateRegistryTests(unittest.TestCase):
    def test_registry_contains_only_contract_templates_with_exact_hashes(self) -> None:
        self.assertEqual(EXPECTED_QUERY_TEMPLATE_IDS, tuple(QUERY_TEMPLATES))
        self.assertEqual(12, len(QUERY_TEMPLATES))

        for template_id, template in QUERY_TEMPLATES.items():
            with self.subTest(template_id=template_id):
                self.assertIs(template, get_query_template(template_id))
                self.assertEqual("1", template.version)
                self.assertEqual(template.template_id, template.query_template_id)
                self.assertEqual(template.version, template.query_template_version)
                self.assertEqual(
                    template.definition_sha256,
                    template.query_template_sha256,
                )
                self.assertEqual(
                    hashlib.sha256(template.canonical_bytes()).hexdigest(),
                    template.definition_sha256,
                )
                self.assertRegex(template.definition_sha256, r"^[0-9a-f]{64}$")
                self.assertEqual(
                    template_id.startswith("graph."),
                    template.graph_optional,
                )

    def test_lexical_vector_hybrid_and_graph_parameters_bind(self) -> None:
        lexical = bind_query_template(
            "lexical.search.v1",
            {"query_text": "signed engagement", "max_results": 10},
        )
        self.assertEqual("signed engagement", lexical.parameters["query_text"])
        self.assertEqual(10, lexical.parameters["max_results"])

        vector = bind_query_template(
            "vector.exact.v1",
            {"query_embedding": [0, 0.5, -0.25], "max_results": 20},
        )
        self.assertEqual((0.0, 0.5, -0.25), vector.parameters["query_embedding"])

        hybrid = bind_query_template(
            "hybrid.rrf.v1",
            {
                "query_text": "authority citation",
                "query_embedding": [0.25],
                "max_results": 5,
            },
        )
        self.assertEqual("hybrid.rrf.v1", hybrid.template.template_id)

        graph = bind_query_template(
            "graph.paths_bounded.v1",
            {
                "source_entity_ids": ["claim-1"],
                "target_entity_ids": ["authority-1"],
                "graph_depth": 3,
                "max_results": 100,
            },
        )
        self.assertEqual(("claim-1",), graph.parameters["source_entity_ids"])
        self.assertTrue(graph.template.graph_optional)

        count = bind_query_template("graph.scope_count.v1", {})
        self.assertEqual({}, dict(count.parameters))
        with self.assertRaises(TypeError):
            count.parameters["max_results"] = 1  # type: ignore[index]

    def test_raw_query_controls_and_unknown_parameters_are_rejected(self) -> None:
        forbidden_names = (
            "sql",
            "raw_sql",
            "tsquery",
            "raw_tsquery",
            "cypher",
            "raw_cypher",
            "graph_name",
            "config",
            "text_search_configuration",
            "filter",
            "filters",
            "order",
            "order_by",
            "order_expression",
        )
        for name in forbidden_names:
            with self.subTest(name=name):
                with self.assertRaises(QueryTemplateError):
                    bind_query_template(
                        "lexical.search.v1",
                        {"query_text": "matter", "max_results": 10, name: "unsafe"},
                    )

        with self.assertRaises(QueryTemplateError):
            bind_query_template(
                "lexical.search.v1",
                {"query_text": "matter", "max_results": 10, "offset": 1},
            )

    def test_parameter_types_and_bounds_fail_closed(self) -> None:
        invalid_calls = (
            ("lexical.search.v1", {"query_text": "x" * 16_385, "max_results": 1}),
            ("lexical.search.v1", {"query_text": "x", "max_results": 0}),
            ("lexical.search.v1", {"query_text": "x", "max_results": 101}),
            ("lexical.search.v1", {"query_text": "x", "max_results": True}),
            ("vector.exact.v1", {"query_embedding": [], "max_results": 1}),
            (
                "vector.exact.v1",
                {"query_embedding": [math.nan], "max_results": 1},
            ),
            (
                "vector.exact.v1",
                {"query_embedding": [math.inf], "max_results": 1},
            ),
            (
                "graph.entity.v1",
                {
                    "entity_ids": [f"entity-{index}" for index in range(101)],
                    "max_results": 1,
                },
            ),
            (
                "graph.neighbors.v1",
                {
                    "entity_ids": ["entity-1"],
                    "graph_depth": 4,
                    "max_results": 1,
                },
            ),
            (
                "graph.entity.v1",
                {"entity_ids": ["entity-1", "entity-1"], "max_results": 1},
            ),
            (
                "graph.entity.v1",
                {"entity_ids": ["unsafe identifier"], "max_results": 1},
            ),
            (
                "graph.paths_bounded.v1",
                {
                    "source_entity_ids": [f"source-{index}" for index in range(100)],
                    "target_entity_ids": ["target-1"],
                    "graph_depth": 1,
                    "max_results": 1,
                },
            ),
        )
        for template_id, parameters in invalid_calls:
            with self.subTest(template_id=template_id, parameters=parameters):
                with self.assertRaises(QueryTemplateError):
                    bind_query_template(template_id, parameters)

    def test_unknown_template_missing_parameter_and_non_mapping_are_rejected(
        self,
    ) -> None:
        with self.assertRaises(QueryTemplateError):
            get_query_template("lexical.raw.v1")
        with self.assertRaises(QueryTemplateError):
            bind_query_template("lexical.search.v1", {"query_text": "matter"})
        with self.assertRaises(QueryTemplateError):
            bind_query_template("lexical.search.v1", [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
