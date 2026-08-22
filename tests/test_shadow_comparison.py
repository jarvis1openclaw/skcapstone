"""Tests for the SKL-S3-04B shadow projection comparison harness."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from sklegal_retrieval.errors import RetrievalUnavailableError
from sklegal_retrieval.evaluation_dataset import EvalCaseClass, load_frozen_dataset
from sklegal_retrieval.postgres import VECTOR_EXACT_STATEMENT, PostgresRetrievalAdapter
from sklegal_retrieval.query_templates import bind_query_template
from sklegal_retrieval.shadow_comparison import (
    BASE_BGE_M3_ROUTE,
    CUSTOM_LEGAL_ROUTE,
    K_VALUES,
    LEAKAGE_CROSS_PARTITION,
    LEAKAGE_PRIVILEGE_ESCALATION,
    DeterministicHashEmbedder,
    ShadowComparisonError,
    ShadowGenerationSpec,
    build_fixture_candidates,
    build_route_report,
    build_shadow_generation,
    compare_shadow_routes,
    cosine_distance,
    evaluate_shadow_route,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    stale_shadow_generation,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "evals/retrieval/frozen-v1"


class StepClock:
    """Deterministic clock advancing by a fixed step on every read."""

    def __init__(self, step: float = 0.5) -> None:
        self._step = step
        self._value = 0.0

    def __call__(self) -> float:
        value = self._value
        self._value += self._step
        return value


class MetricUnitTests(unittest.TestCase):
    """Hand-computed metric values over tiny ranked lists."""

    def test_recall_at_k_counts_hits_over_the_relevant_set(self) -> None:
        self.assertEqual(recall_at_k(["a", "b", "c"], {"a", "d"}, 2), 0.5)
        self.assertEqual(recall_at_k(["a", "b", "c"], {"d"}, 3), 0.0)
        self.assertEqual(recall_at_k(["a", "b", "c"], {"a", "b", "c"}, 10), 1.0)
        self.assertEqual(recall_at_k(["a", "b", "c"], {"a", "b", "c"}, 2), 2 / 3)

    def test_recall_at_k_is_none_without_relevant_documents(self) -> None:
        self.assertIsNone(recall_at_k(["a", "b"], set(), 5))
        self.assertIsNone(recall_at_k([], set(), 5))

    def test_ndcg_at_k_uses_exponential_gain_and_log_discount(self) -> None:
        grades = {"a": 3, "b": 1}
        # dcg: b at rank one (gain 1), a at rank two (gain 7 / log2(3)).
        # idcg: a first (gain 7), b second (gain 1 / log2(3)).
        expected = (1.0 + 7.0 / math.log2(3)) / (7.0 + 1.0 / math.log2(3))
        self.assertAlmostEqual(ndcg_at_k(["b", "a"], grades, 2), expected)
        self.assertAlmostEqual(ndcg_at_k(["a", "b"], grades, 2), 1.0)
        self.assertAlmostEqual(ndcg_at_k(["a", "b"], grades, 1), 1.0)

    def test_ndcg_at_k_is_zero_without_graded_documents(self) -> None:
        self.assertEqual(ndcg_at_k(["a", "b"], {}, 3), 0.0)
        self.assertEqual(ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, 3), 0.0)

    def test_ndcg_at_k_truncates_at_k(self) -> None:
        self.assertAlmostEqual(ndcg_at_k(["c", "a", "b"], {"a": 3, "b": 2}, 1), 0.0)

    def test_reciprocal_rank_of_the_first_relevant_hit(self) -> None:
        self.assertEqual(reciprocal_rank(["x", "y", "z"], {"z"}), 1 / 3)
        self.assertEqual(reciprocal_rank(["a"], {"a"}), 1.0)
        self.assertEqual(reciprocal_rank(["x", "y"], {"a"}), 0.0)

    def test_cosine_distance_over_known_vectors(self) -> None:
        self.assertAlmostEqual(cosine_distance((1.0, 0.0), (1.0, 0.0)), 0.0)
        self.assertAlmostEqual(cosine_distance((1.0, 0.0), (0.0, 1.0)), 1.0)
        self.assertAlmostEqual(cosine_distance((1.0, 0.0), (-1.0, 0.0)), 2.0)
        with self.assertRaises(ValueError):
            cosine_distance((1.0,), (1.0, 0.0))
        with self.assertRaises(ValueError):
            cosine_distance((0.0, 0.0), (1.0, 0.0))


class FrozenDatasetTestCase(unittest.TestCase):
    """Shared fixture loading for the full-harness tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_frozen_dataset(DATASET_ROOT)


class ShadowGenerationTests(FrozenDatasetTestCase):
    """Shadow generation builds are deterministic and route-bound."""

    def test_rebuilding_one_route_reproduces_identical_rows(self) -> None:
        first = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        second = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        self.assertEqual(first, second)
        self.assertEqual(first.rows_digest(), second.rows_digest())
        self.assertEqual(
            first.projection_set_id,
            second.projection_set_id,
        )

    def test_every_document_is_embedded_with_the_route_pins(self) -> None:
        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        embedder = DeterministicHashEmbedder(CUSTOM_LEGAL_ROUTE)
        staged = sum(len(partition.rows) for partition in generation.partitions)
        self.assertEqual(staged, len(self.dataset.documents))
        from sklegal_retrieval.shadow_comparison import document_text

        first = generation.partitions[0].rows[0]
        document = next(
            item
            for item in self.dataset.documents
            if item.document_id == first.source.document_id
        )
        self.assertEqual(first.embedding, embedder.embed(document_text(document)))
        self.assertEqual(
            len(first.embedding),
            CUSTOM_LEGAL_ROUTE.vector.embedding_dimension,
        )
        norm = math.sqrt(sum(value * value for value in first.embedding or ()))
        self.assertAlmostEqual(norm, 1.0)

    def test_routes_share_corpus_rows_but_differ_in_embeddings(self) -> None:
        custom = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        base = build_shadow_generation(self.dataset, BASE_BGE_M3_ROUTE)
        # The rows digest covers source and content pins only, so both routes
        # project an identical corpus; only the vector columns may differ.
        self.assertEqual(custom.rows_digest(), base.rows_digest())
        self.assertNotEqual(custom.projection_set_id, base.projection_set_id)
        custom_row = custom.partitions[0].rows[0]
        base_row = base.partitions[0].rows[0]
        self.assertEqual(custom_row.source, base_row.source)
        self.assertNotEqual(custom_row.embedding, base_row.embedding)

    def test_an_embedder_from_another_route_is_rejected(self) -> None:
        with self.assertRaises(ShadowComparisonError):
            build_shadow_generation(
                self.dataset,
                CUSTOM_LEGAL_ROUTE,
                embedder=DeterministicHashEmbedder(BASE_BGE_M3_ROUTE),
            )

    def test_partitions_are_grouped_by_tenant_and_matter(self) -> None:
        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        keys = [(item.tenant_id, item.matter_id) for item in generation.partitions]
        expected = sorted(
            {(item.tenant_id, item.matter_id) for item in self.dataset.documents}
        )
        self.assertEqual(keys, expected)
        for partition in generation.partitions:
            self.assertTrue(partition.rows)
            self.assertEqual(len(partition.documents), len(partition.rows))
            for row in partition.rows:
                self.assertEqual(row.projection, partition.projection)

    def test_partition_for_unknown_scope_fails_closed(self) -> None:
        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        known = {(item.tenant_id, item.matter_id) for item in self.dataset.documents}
        unknown_matter = UUID("99999999-9999-4999-8999-999999999999")
        self.assertNotIn((self.dataset.documents[0].tenant_id, unknown_matter), known)
        with self.assertRaises(ShadowComparisonError):
            generation.partition_for(
                self.dataset.documents[0].tenant_id, unknown_matter
            )


class ComparisonHarnessTests(FrozenDatasetTestCase):
    """The full harness measures both routes over the same freeze."""

    def test_fixture_comparison_reports_both_routes(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock(0.5))
        self.assertEqual(
            report.dataset_freeze_sha256,
            self.dataset.manifest.freeze_sha256,
        )
        self.assertEqual(report.k_values, K_VALUES)
        self.assertEqual(
            [item.route.label for item in report.routes],
            ["custom_legal", "base_bge_m3"],
        )
        for route in report.routes:
            with self.subTest(route=route.route.label):
                self.assertEqual(route.leakage_count, 0)
                self.assertEqual(
                    route.projection_generation, route.projection_generation
                )
                partition_queries = sum(
                    item.queries_evaluated for item in route.partitions
                )
                self.assertEqual(partition_queries, len(self.dataset.queries))
                for metric in route.partitions:
                    self.assertFalse(metric.leakage_codes)
                    for value in (*metric.recall_at_k, *metric.ndcg_at_k):
                        self.assertGreaterEqual(value, 0.0)
                        self.assertLessEqual(value, 1.0)
                    self.assertGreaterEqual(metric.mrr, 0.0)
                    self.assertLessEqual(metric.mrr, 1.0)
                    # The step clock makes every measured latency exactly 0.5.
                    self.assertEqual(metric.latency_mean_seconds, 0.5)
                    self.assertEqual(metric.latency_max_seconds, 0.5)

    def test_comparison_is_deterministic_across_runs(self) -> None:
        first = compare_shadow_routes(
            self.dataset, build_fixture_candidates(self.dataset), clock=StepClock()
        )
        second = compare_shadow_routes(
            self.dataset, build_fixture_candidates(self.dataset), clock=StepClock()
        )
        self.assertEqual(first, second)

    def test_routes_cover_identical_partitions_with_shared_freeze(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock())
        keys = [
            (item.tenant_id, item.matter_id) for item in report.routes[0].partitions
        ]
        self.assertEqual(keys, sorted(keys))
        for route in report.routes[1:]:
            self.assertEqual(
                [(item.tenant_id, item.matter_id) for item in route.partitions],
                keys,
            )
            self.assertEqual(
                route.dataset_freeze_sha256,
                report.routes[0].dataset_freeze_sha256,
            )

    def test_citation_eligibility_counts_exact_citation_queries(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock())
        exact = {
            (item.tenant_id, item.matter_id)
            for item in self.dataset.queries
            if item.case_class is EvalCaseClass.EXACT_CITATION
        }
        route = report.routes[0]
        counted = sum(item.citation_eligible_queries for item in route.partitions)
        expected = sum(
            1
            for item in self.dataset.queries
            if item.case_class is EvalCaseClass.EXACT_CITATION
        )
        self.assertEqual(counted, expected)
        for metric in route.partitions:
            if (metric.tenant_id, metric.matter_id) in exact:
                self.assertGreater(metric.citation_eligible_queries, 0)
            else:
                self.assertEqual(metric.citation_eligible_queries, 0)

    def test_recall_eligibility_requires_relevant_judgments(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock())
        for route in report.routes:
            for metric in route.partitions:
                self.assertLessEqual(
                    metric.recall_eligible_queries, metric.queries_evaluated
                )
                if metric.queries_evaluated:
                    self.assertGreater(metric.recall_eligible_queries, 0)

    def test_outcomes_must_cover_exactly_the_frozen_queries(self) -> None:
        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        embedder = DeterministicHashEmbedder(CUSTOM_LEGAL_ROUTE)
        from sklegal_retrieval.shadow_comparison import ShadowEvaluationPlane

        plane = ShadowEvaluationPlane(generation)
        outcomes = [
            plane.execute_query(query, embedder, clock=StepClock())
            for query in self.dataset.queries[:-1]
        ]
        with self.assertRaises(ShadowComparisonError):
            build_route_report(self.dataset, generation, outcomes)


class LeakageDetectionTests(FrozenDatasetTestCase):
    """The harness flags a plane that serves rows across its guards."""

    def test_cross_partition_leakage_is_coded_and_counted(self) -> None:
        candidates = build_fixture_candidates(self.dataset, leak_partitions=True)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock())
        for route in report.routes:
            codes = [
                code for metric in route.partitions for code in metric.leakage_codes
            ]
            with self.subTest(route=route.route.label):
                self.assertGreater(route.leakage_count, 0)
                self.assertEqual(route.leakage_count, len(codes))
                self.assertTrue(
                    all(
                        code.startswith(f"{LEAKAGE_CROSS_PARTITION}:") for code in codes
                    )
                )
                for metric in route.partitions:
                    self.assertEqual(
                        route.leakage_count,
                        sum(len(item.leakage_codes) for item in route.partitions),
                    )

    def test_privilege_escalation_leakage_is_coded_and_counted(self) -> None:
        candidates = build_fixture_candidates(self.dataset, leak_privilege=True)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock())
        for route in report.routes:
            codes = [
                code for metric in route.partitions for code in metric.leakage_codes
            ]
            with self.subTest(route=route.route.label):
                self.assertGreater(route.leakage_count, 0)
                self.assertTrue(
                    all(
                        code.startswith(f"{LEAKAGE_PRIVILEGE_ESCALATION}:")
                        for code in codes
                    )
                )

    def test_a_clean_plane_reports_no_leakage(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock())
        for route in report.routes:
            self.assertEqual(route.leakage_count, 0)


class StaleGenerationTests(FrozenDatasetTestCase):
    """The evaluation gates reject rows from another projection generation."""

    def test_serving_another_generation_is_rejected(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        custom, embedder = candidates[0].generation, candidates[0].embedder
        stale = stale_shadow_generation(custom, custom.spec.projection_generation + 1)
        with self.assertRaises(ShadowComparisonError):
            evaluate_shadow_route(self.dataset, stale, embedder, clock=StepClock())

    def test_the_served_generation_defaults_to_the_spec_generation(self) -> None:
        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        self.assertEqual(
            generation.serve_generation, generation.spec.projection_generation
        )

    def test_a_freeze_mismatch_is_rejected(self) -> None:
        candidates = build_fixture_candidates(self.dataset)
        custom, embedder = candidates[0].generation, candidates[0].embedder
        wrong_freeze = replace(
            custom,
            spec=ShadowGenerationSpec(
                route=custom.spec.route,
                dataset_freeze_sha256="0" * 64,
                projection_generation=custom.spec.projection_generation,
            ),
        )
        with self.assertRaises(ShadowComparisonError):
            evaluate_shadow_route(
                self.dataset, wrong_freeze, embedder, clock=StepClock()
            )


class PlaneGuardTests(FrozenDatasetTestCase):
    """The query runner fails closed without an active query context."""

    def test_fetch_without_a_query_context_is_unavailable(self) -> None:
        from sklegal_retrieval.shadow_comparison import ShadowQueryRunner

        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        runner = ShadowQueryRunner(generation)
        adapter = PostgresRetrievalAdapter(runner)
        embedder = DeterministicHashEmbedder(CUSTOM_LEGAL_ROUTE)
        partition = generation.partitions[0]
        bound = bind_query_template(
            "vector.exact.v1",
            {
                "query_embedding": embedder.embed(partition.documents[0].title),
                "max_results": 5,
            },
        )
        with self.assertRaises(RetrievalUnavailableError):
            adapter.execute(bound, (partition.projection,))

    def test_the_runner_executes_only_the_pinned_statement(self) -> None:
        from sklegal_retrieval.shadow_comparison import ShadowQueryRunner

        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        runner = ShadowQueryRunner(generation)
        runner.begin_query(self.dataset.queries[0])
        with self.assertRaises(ShadowComparisonError):
            runner.fetch_all(
                component_access_refs=(
                    generation.partitions[0].projection.component_access_ref,
                ),
                statement="SELECT * FROM sklegal_retrieval.lexical_search_v1(%s, %s)",
                parameters=({"x": 1.0}, 5),
            )
        runner.end_query(self.dataset.queries[0])

    def test_an_embedder_from_another_route_is_rejected_by_the_plane(self) -> None:
        from sklegal_retrieval.shadow_comparison import ShadowEvaluationPlane

        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        plane = ShadowEvaluationPlane(generation)
        with self.assertRaises(ShadowComparisonError):
            plane.execute_query(
                self.dataset.queries[0],
                DeterministicHashEmbedder(BASE_BGE_M3_ROUTE),
                clock=StepClock(),
            )

    def test_served_rows_carry_the_pinned_statement_shape(self) -> None:
        from sklegal_retrieval.shadow_comparison import ShadowQueryRunner

        generation = build_shadow_generation(self.dataset, CUSTOM_LEGAL_ROUTE)
        runner = ShadowQueryRunner(generation)
        embedder = DeterministicHashEmbedder(CUSTOM_LEGAL_ROUTE)
        query = next(
            item
            for item in self.dataset.queries
            if (item.tenant_id, item.matter_id)
            == (generation.partitions[0].tenant_id, generation.partitions[0].matter_id)
        )
        partition = generation.partition_for(query.tenant_id, query.matter_id)
        runner.begin_query(query)
        try:
            rows = runner.fetch_all(
                component_access_refs=(partition.projection.component_access_ref,),
                statement=VECTOR_EXACT_STATEMENT,
                parameters=(embedder.embed(query.query_text), 3),
            )
        finally:
            runner.end_query(query)
        self.assertLessEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(
                row["projection"].projection_generation, generation.serve_generation
            )


if __name__ == "__main__":
    unittest.main()
