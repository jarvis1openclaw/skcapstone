"""Tests for the SKL-S3-04C qualification verdict and alias rollback."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sklegal_retrieval.embedding_alias import (
    DEFAULT_ALIAS_NAME,
    AliasAction,
    AliasStatus,
    EmbeddingAliasError,
    EmbeddingAliasRegistry,
)
from sklegal_retrieval.embedding_qualification import (
    THRESHOLDS_DEFAULT_PATH,
    CheckOutcome,
    EmbeddingQualificationError,
    EmbeddingQualificationVerdict,
    MetricCheck,
    QualificationDecision,
    QualificationThresholds,
    apply_qualification_thresholds,
    load_qualification_thresholds,
)
from sklegal_retrieval.evaluation_dataset import load_frozen_dataset
from sklegal_retrieval.shadow_comparison import (
    CUSTOM_LEGAL_ROUTE,
    ShadowComparisonReport,
    ShadowRouteLabel,
    build_fixture_candidates,
    compare_shadow_routes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "evals/retrieval/frozen-v1"

ALTERNATE_FREEZE = "b" * 64

#: Every check citation the fixture routes must produce under a
#: StepClock(0.01) run over the pinned freeze. The values are the S3-04B
#: fixture measurements; they never qualify a real embedder.
CUSTOM_CITATIONS = (
    ("recall@1", "passed", "0.700000", ">=", "0.600000"),
    ("recall@5", "passed", "0.950000", ">=", "0.900000"),
    ("recall@10", "passed", "1.000000", ">=", "0.950000"),
    ("ndcg@1", "passed", "0.686813", ">=", "0.600000"),
    ("ndcg@5", "passed", "0.728068", ">=", "0.650000"),
    ("ndcg@10", "passed", "0.741575", ">=", "0.700000"),
    ("mrr", "passed", "0.704518", ">=", "0.650000"),
    ("citation_accuracy", "waived", "0.000000", ">=", "0.900000"),
    ("macro_latency_mean_seconds", "passed", "0.010000", "<=", "0.050000"),
    ("max_latency_seconds", "passed", "0.010000", "<=", "0.250000"),
    ("leakage_count", "passed", "0", "==", "0"),
)

BASE_CITATIONS = (
    ("recall@1", "passed", "0.700000", ">=", "0.600000"),
    ("recall@5", "passed", "0.950000", ">=", "0.900000"),
    ("recall@10", "passed", "1.000000", ">=", "0.950000"),
    ("ndcg@1", "passed", "0.683150", ">=", "0.600000"),
    ("ndcg@5", "passed", "0.726395", ">=", "0.650000"),
    ("ndcg@10", "passed", "0.740503", ">=", "0.700000"),
    ("mrr", "passed", "0.704060", ">=", "0.650000"),
    ("citation_accuracy", "waived", "0.000000", ">=", "0.900000"),
    ("macro_latency_mean_seconds", "passed", "0.010000", "<=", "0.050000"),
    ("max_latency_seconds", "passed", "0.010000", "<=", "0.250000"),
    ("leakage_count", "passed", "0", "==", "0"),
)


class StepClock:
    """Deterministic clock advancing by a fixed step on every read."""

    def __init__(self, step: float = 0.5) -> None:
        self._step = step
        self._value = 0.0

    def __call__(self) -> float:
        value = self._value
        self._value += self._step
        return value


class FrozenDatasetTestCase(unittest.TestCase):
    """Shared fixture loading for the full-harness tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_frozen_dataset(DATASET_ROOT)


def _threshold_payload() -> dict[str, Any]:
    return json.loads(THRESHOLDS_DEFAULT_PATH.read_text())


class ThresholdFileTests(unittest.TestCase):
    """The approved threshold file loads and pins its exact bytes."""

    def test_the_approved_file_loads_and_pins_its_bytes(self) -> None:
        loaded = load_qualification_thresholds(THRESHOLDS_DEFAULT_PATH)
        raw = THRESHOLDS_DEFAULT_PATH.read_bytes()
        self.assertEqual(loaded.file_sha256, hashlib.sha256(raw).hexdigest())
        again = load_qualification_thresholds(THRESHOLDS_DEFAULT_PATH)
        self.assertEqual(loaded, again)
        pinned = loaded.thresholds
        self.assertEqual(
            pinned.thresholds_schema, "sklegal-embedding-qualification-thresholds/v1"
        )
        self.assertEqual(pinned.thresholds_version, "1.0.0")
        self.assertEqual(pinned.harness_version, "1.0.0")
        self.assertEqual(pinned.k_values, (1, 5, 10))
        self.assertEqual(
            pinned.dataset_freeze_sha256,
            "c4f829781badfc0139dda420c539ee30d4d43ac7c189aa55114af385ed02d189",
        )
        self.assertEqual(pinned.maximum_leakage_count, 0)

    def test_a_missing_threshold_file_fails_closed(self) -> None:
        with self.assertRaises(EmbeddingQualificationError):
            load_qualification_thresholds(REPO_ROOT / "config/retrieval/absent.json")


class ThresholdValidatorTests(unittest.TestCase):
    """Every bound-family validator rejects an unapprovable threshold set."""

    def _validate(self, payload: dict[str, Any]) -> QualificationThresholds:
        return TypeAdapter(QualificationThresholds).validate_json(json.dumps(payload))

    def test_metric_vectors_must_align_with_the_k_values(self) -> None:
        payload = _threshold_payload()
        payload["recall_at_k_minimum"] = [0.6, 0.9]
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_the_leakage_bound_is_exactly_zero(self) -> None:
        payload = _threshold_payload()
        payload["maximum_leakage_count"] = 1
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_k_values_must_be_unique_positive_integers(self) -> None:
        payload = _threshold_payload()
        payload["k_values"] = [1, 5, 5, 10]
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_the_latency_ceiling_cannot_undercut_the_mean_budget(self) -> None:
        payload = _threshold_payload()
        payload["max_latency_maximum_seconds"] = 0.01
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_a_waiver_may_only_cover_the_waivable_metric(self) -> None:
        payload = _threshold_payload()
        payload["fixture_embedder_waivers"][0]["metric"] = "mrr"
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_a_waiver_may_only_cover_the_fixture_embedder_kind(self) -> None:
        payload = _threshold_payload()
        payload["fixture_embedder_waivers"][0]["applies_to_embedder_kind"] = (
            "real_model"
        )
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_the_enforced_real_embedder_floor_must_match_the_bound(self) -> None:
        payload = _threshold_payload()
        payload["fixture_embedder_waivers"][0]["enforced_value_for_real_embedders"] = (
            0.8
        )
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_each_metric_carries_at_most_one_waiver(self) -> None:
        payload = _threshold_payload()
        waiver = dict(payload["fixture_embedder_waivers"][0])
        payload["fixture_embedder_waivers"].append(waiver)
        with self.assertRaises(ValueError):
            self._validate(payload)

    def test_a_narrower_k_threshold_set_still_validates(self) -> None:
        payload = _threshold_payload()
        payload["k_values"] = [1, 5]
        payload["recall_at_k_minimum"] = [0.6, 0.9]
        payload["ndcg_at_k_minimum"] = [0.6, 0.65]
        thresholds = self._validate(payload)
        self.assertEqual(thresholds.k_values, (1, 5))


class MetricCheckTests(unittest.TestCase):
    """A check records its waiver reason exactly when it is waived."""

    def _check(self, **overrides: Any) -> MetricCheck:
        fields: dict[str, Any] = {
            "metric": "mrr",
            "operator": ">=",
            "measured": "0.100000",
            "bound": "0.650000",
            "outcome": CheckOutcome.FAILED,
        }
        fields.update(overrides)
        return MetricCheck(**fields)

    def test_a_waived_check_carries_its_reason(self) -> None:
        check = self._check(
            outcome=CheckOutcome.WAIVED,
            waiver_reason="the fixture embedder cannot rank citations",
        )
        self.assertIs(check.outcome, CheckOutcome.WAIVED)
        self.assertIn("fixture", check.waiver_reason or "")

    def test_a_non_waived_check_carries_no_reason(self) -> None:
        with self.assertRaises(ValueError):
            self._check(
                outcome=CheckOutcome.PASSED,
                waiver_reason="a passing check is never waived",
            )
        with self.assertRaises(ValueError):
            self._check(outcome=CheckOutcome.WAIVED)


class QualificationVerdictTests(FrozenDatasetTestCase):
    """The verdict cites every measured value and bound on both routes."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.thresholds = load_qualification_thresholds(THRESHOLDS_DEFAULT_PATH)
        cls.clean_report = compare_shadow_routes(
            cls.dataset,
            build_fixture_candidates(cls.dataset),
            clock=StepClock(0.01),
        )
        cls.clean_verdict = apply_qualification_thresholds(
            cls.clean_report, cls.thresholds
        )

    def _citations(self, verdict: Any) -> tuple[tuple[str, str, str, str, str], ...]:
        return tuple(
            (
                check.metric,
                check.outcome.value,
                check.measured,
                check.operator,
                check.bound,
            )
            for check in verdict.checks
        )

    def test_the_clean_fixture_verdict_cites_every_custom_check(self) -> None:
        custom = self.clean_verdict.route_verdict(ShadowRouteLabel.CUSTOM_LEGAL)
        self.assertTrue(custom.qualified)
        self.assertEqual(self._citations(custom), CUSTOM_CITATIONS)
        self.assertEqual(
            custom.embedding_model_id, CUSTOM_LEGAL_ROUTE.vector.embedding_model_id
        )
        self.assertEqual(custom.embedder_kind, "deterministic_hash_fixture")
        self.assertIsInstance(custom.projection_set_id, UUID)

    def test_the_clean_fixture_verdict_cites_every_base_check(self) -> None:
        base = self.clean_verdict.route_verdict(ShadowRouteLabel.BASE_BGE_M3)
        self.assertTrue(base.qualified)
        self.assertEqual(self._citations(base), BASE_CITATIONS)

    def test_the_waiver_is_recorded_with_its_reason_on_both_routes(self) -> None:
        for label in (ShadowRouteLabel.CUSTOM_LEGAL, ShadowRouteLabel.BASE_BGE_M3):
            route = self.clean_verdict.route_verdict(label)
            waived = route.waived_checks
            self.assertEqual(len(waived), 1)
            self.assertEqual(waived[0].metric, "citation_accuracy")
            self.assertIn("fixture", waived[0].waiver_reason or "")
            self.assertIn("0.9000", waived[0].waiver_reason or "")

    def test_the_clean_verdict_qualifies_the_custom_route(self) -> None:
        self.assertIs(
            self.clean_verdict.decision, QualificationDecision.CUSTOM_QUALIFIED
        )
        self.assertEqual(
            self.clean_verdict.thresholds_sha256, self.thresholds.file_sha256
        )
        self.assertEqual(
            self.clean_verdict.dataset_freeze_sha256,
            self.dataset.manifest.freeze_sha256,
        )
        self.assertEqual(self.clean_verdict.k_values, (1, 5, 10))

    def test_a_failed_custom_route_rolls_back_to_base(self) -> None:
        routes = []
        for route in self.clean_report.routes:
            data = route.model_dump(mode="python")
            if route.route.label is ShadowRouteLabel.CUSTOM_LEGAL:
                data["macro_mrr"] = 0.1
            routes.append(type(route).model_validate(data))
        failing_report = ShadowComparisonReport(
            dataset_freeze_sha256=self.clean_report.dataset_freeze_sha256,
            k_values=self.clean_report.k_values,
            routes=tuple(routes),
        )
        verdict = apply_qualification_thresholds(failing_report, self.thresholds)
        self.assertIs(verdict.decision, QualificationDecision.ROLLBACK_TO_BASE)
        custom = verdict.route_verdict(ShadowRouteLabel.CUSTOM_LEGAL)
        self.assertFalse(custom.qualified)
        self.assertEqual(len(custom.failed_checks), 1)
        failed = custom.failed_checks[0]
        self.assertEqual(failed.metric, "mrr")
        self.assertEqual(failed.measured, "0.100000")
        self.assertEqual(failed.bound, "0.650000")
        self.assertTrue(verdict.route_verdict(ShadowRouteLabel.BASE_BGE_M3).qualified)

    def test_leaking_routes_fail_closed_with_no_waiver_available(self) -> None:
        candidates = build_fixture_candidates(self.dataset, leak_partitions=True)
        report = compare_shadow_routes(self.dataset, candidates, clock=StepClock(0.01))
        verdict = apply_qualification_thresholds(report, self.thresholds)
        self.assertIs(verdict.decision, QualificationDecision.FAIL_CLOSED)
        for label in (ShadowRouteLabel.CUSTOM_LEGAL, ShadowRouteLabel.BASE_BGE_M3):
            route = verdict.route_verdict(label)
            self.assertFalse(route.qualified)
            leakage = [
                check
                for check in route.failed_checks
                if check.metric == "leakage_count"
            ]
            self.assertEqual(len(leakage), 1)
            self.assertEqual(leakage[0].bound, "0")
            self.assertGreater(int(leakage[0].measured), 0)

    def test_slow_routes_fail_the_latency_bounds(self) -> None:
        report = compare_shadow_routes(
            self.dataset,
            build_fixture_candidates(self.dataset),
            clock=StepClock(0.5),
        )
        verdict = apply_qualification_thresholds(report, self.thresholds)
        self.assertIs(verdict.decision, QualificationDecision.FAIL_CLOSED)
        for label in (ShadowRouteLabel.CUSTOM_LEGAL, ShadowRouteLabel.BASE_BGE_M3):
            route = verdict.route_verdict(label)
            failed = {check.metric: check for check in route.failed_checks}
            self.assertIn("macro_latency_mean_seconds", failed)
            self.assertEqual(failed["macro_latency_mean_seconds"].measured, "0.500000")
            self.assertEqual(failed["macro_latency_mean_seconds"].bound, "0.050000")

    def test_the_verdict_rejects_a_foreign_dataset_freeze(self) -> None:
        data = self.clean_report.model_dump(mode="python")
        data["dataset_freeze_sha256"] = ALTERNATE_FREEZE
        for route in data["routes"]:
            route["dataset_freeze_sha256"] = ALTERNATE_FREEZE
        foreign = ShadowComparisonReport.model_validate(data)
        with self.assertRaises(EmbeddingQualificationError):
            apply_qualification_thresholds(foreign, self.thresholds)

    def test_the_verdict_rejects_a_foreign_harness_version(self) -> None:
        data = self.clean_report.model_dump(mode="python")
        data["harness_version"] = "1.0.1"
        foreign = ShadowComparisonReport.model_validate(data)
        with self.assertRaises(EmbeddingQualificationError):
            apply_qualification_thresholds(foreign, self.thresholds)

    def test_the_verdict_rejects_foreign_k_values(self) -> None:
        payload = _threshold_payload()
        payload["k_values"] = [1, 5]
        payload["recall_at_k_minimum"] = [0.6, 0.9]
        payload["ndcg_at_k_minimum"] = [0.6, 0.65]
        narrower = TypeAdapter(QualificationThresholds).validate_json(
            json.dumps(payload)
        )
        with self.assertRaises(EmbeddingQualificationError):
            apply_qualification_thresholds(
                self.clean_report,
                type(self.thresholds)(
                    thresholds=narrower,
                    file_sha256=self.thresholds.file_sha256,
                ),
            )

    def test_the_decision_cannot_disagree_with_the_route_verdicts(self) -> None:
        data = self.clean_verdict.model_dump(mode="python")
        data["decision"] = QualificationDecision.FAIL_CLOSED.value
        with self.assertRaises(ValueError):
            EmbeddingQualificationVerdict.model_validate(data)

    def test_a_route_cannot_claim_qualification_with_a_failed_check(self) -> None:
        data = self.clean_verdict.model_dump(mode="python")
        data["routes"][0]["qualified"] = False
        with self.assertRaises(ValueError):
            EmbeddingQualificationVerdict.model_validate(data)


class AliasRollbackTests(FrozenDatasetTestCase):
    """The alias binds one qualified route and exercises its rollback."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.thresholds = load_qualification_thresholds(THRESHOLDS_DEFAULT_PATH)
        cls.custom, cls.base = build_fixture_candidates(cls.dataset)
        cls.clean_report = compare_shadow_routes(
            cls.dataset, (cls.custom, cls.base), clock=StepClock(0.01)
        )
        cls.clean_verdict = apply_qualification_thresholds(
            cls.clean_report, cls.thresholds
        )
        routes = []
        for route in cls.clean_report.routes:
            data = route.model_dump(mode="python")
            if route.route.label is ShadowRouteLabel.CUSTOM_LEGAL:
                data["macro_mrr"] = 0.1
            routes.append(type(route).model_validate(data))
        cls.failing_verdict = apply_qualification_thresholds(
            ShadowComparisonReport(
                dataset_freeze_sha256=cls.clean_report.dataset_freeze_sha256,
                k_values=cls.clean_report.k_values,
                routes=tuple(routes),
            ),
            cls.thresholds,
        )

    def _registry(self) -> EmbeddingAliasRegistry:
        registry = EmbeddingAliasRegistry(self.thresholds)
        registry.register_candidate(self.custom)
        registry.register_candidate(self.base)
        return registry

    def test_a_fresh_alias_fails_closed(self) -> None:
        registry = EmbeddingAliasRegistry(self.thresholds)
        self.assertIs(registry.state.status, AliasStatus.UNBOUND)
        self.assertEqual(registry.state.alias_name, DEFAULT_ALIAS_NAME)
        with self.assertRaises(EmbeddingAliasError):
            registry.resolve()
        with self.assertRaises(EmbeddingAliasError):
            registry.serve_query(self.dataset.queries[0])

    def test_registering_one_route_twice_is_rejected(self) -> None:
        registry = EmbeddingAliasRegistry(self.thresholds)
        registry.register_candidate(self.custom)
        with self.assertRaises(EmbeddingAliasError):
            registry.register_candidate(self.custom)

    def test_the_alias_serves_through_the_bound_custom_route(self) -> None:
        registry = self._registry()
        revision = registry.bind_candidate(self.custom, self.clean_verdict)
        self.assertIs(revision.action, AliasAction.BIND)
        binding = registry.resolve()
        self.assertIs(binding.route_label, ShadowRouteLabel.CUSTOM_LEGAL)
        self.assertEqual(binding.embedding_model_id, "embedding.custom-legal.shadow")
        served = registry.serve_query(self.dataset.queries[0])
        self.assertIs(served.route_label, ShadowRouteLabel.CUSTOM_LEGAL)
        self.assertEqual(served.embedding_model_id, binding.embedding_model_id)
        self.assertEqual(served.revision_sequence, 1)
        self.assertTrue(served.ranked_document_ids)
        self.assertGreaterEqual(served.latency_seconds, 0.0)

    def test_the_alias_binds_only_a_qualified_route(self) -> None:
        registry = self._registry()
        with self.assertRaises(EmbeddingAliasError):
            registry.bind_candidate(self.custom, self.failing_verdict)

    def test_rollback_serves_the_base_route_and_cites_the_failed_check(self) -> None:
        registry = self._registry()
        registry.bind_candidate(self.custom, self.clean_verdict)
        custom_served = registry.serve_query(self.dataset.queries[0])
        self.assertIs(custom_served.route_label, ShadowRouteLabel.CUSTOM_LEGAL)

        revision = registry.rollback(self.base, self.failing_verdict)
        self.assertIs(revision.action, AliasAction.ROLLBACK)
        self.assertIn("mrr measured 0.100000 bound >= 0.650000", revision.reason)
        self.assertIn("rollback to base_bge_m3", revision.reason)

        binding = registry.resolve()
        self.assertIs(binding.route_label, ShadowRouteLabel.BASE_BGE_M3)
        self.assertEqual(binding.embedding_model_id, "embedding.bge-m3-base.shadow")
        self.assertEqual(binding.embedding_model_revision, "fixture-v1")

        served = registry.serve_query(self.dataset.queries[0])
        self.assertIs(served.route_label, ShadowRouteLabel.BASE_BGE_M3)
        self.assertEqual(served.embedding_model_id, binding.embedding_model_id)
        self.assertEqual(served.revision_sequence, 2)
        self.assertTrue(served.ranked_document_ids)

        history = registry.state.history
        self.assertEqual(
            [(item.sequence, item.action) for item in history],
            [(1, AliasAction.BIND), (2, AliasAction.ROLLBACK)],
        )
        self.assertEqual(registry.state.current_binding, history[-1].binding)

    def test_rollback_requires_a_verdict_that_failed_the_custom_route(self) -> None:
        registry = self._registry()
        registry.bind_candidate(self.custom, self.clean_verdict)
        with self.assertRaises(EmbeddingAliasError):
            registry.rollback(self.base, self.clean_verdict)

    def test_rollback_requires_an_existing_binding(self) -> None:
        registry = self._registry()
        with self.assertRaises(EmbeddingAliasError):
            registry.rollback(self.base, self.failing_verdict)

    def test_rollback_requires_the_current_binding_to_be_the_failed_route(self) -> None:
        registry = self._registry()
        registry.bind_candidate(self.base, self.clean_verdict)
        with self.assertRaises(EmbeddingAliasError):
            registry.rollback(self.base, self.failing_verdict)

    def test_the_alias_rejects_a_verdict_pinned_to_other_thresholds(self) -> None:
        registry = self._registry()
        data = self.clean_verdict.model_dump(mode="python")
        data["thresholds_sha256"] = "a" * 64
        foreign = EmbeddingQualificationVerdict.model_validate(data)
        with self.assertRaises(EmbeddingAliasError):
            registry.bind_candidate(self.custom, foreign)

    def test_only_a_registered_candidate_can_be_bound(self) -> None:
        registry = EmbeddingAliasRegistry(self.thresholds)
        registry.register_candidate(self.base)
        with self.assertRaises(EmbeddingAliasError):
            registry.bind_candidate(self.custom, self.clean_verdict)


class AliasFailCloseTests(FrozenDatasetTestCase):
    """A verdict where no route qualifies fails the alias closed."""

    def test_leaking_candidates_fail_the_alias_closed_and_serving_refuses(self) -> None:
        thresholds = load_qualification_thresholds(THRESHOLDS_DEFAULT_PATH)
        custom, base = build_fixture_candidates(self.dataset, leak_partitions=True)
        report = compare_shadow_routes(
            self.dataset, (custom, base), clock=StepClock(0.01)
        )
        verdict = apply_qualification_thresholds(report, thresholds)
        self.assertIs(verdict.decision, QualificationDecision.FAIL_CLOSED)

        registry = EmbeddingAliasRegistry(thresholds)
        registry.register_candidate(custom)
        registry.register_candidate(base)
        revision = registry.fail_close(verdict)
        self.assertIs(revision.action, AliasAction.FAIL_CLOSED)
        self.assertIsNone(revision.binding)
        self.assertIn("leakage_count", revision.reason)
        self.assertIs(registry.state.status, AliasStatus.FAIL_CLOSED)
        with self.assertRaises(EmbeddingAliasError):
            registry.resolve()
        with self.assertRaises(EmbeddingAliasError):
            registry.serve_query(self.dataset.queries[0])

    def test_fail_close_requires_a_verdict_where_nothing_qualified(self) -> None:
        thresholds = load_qualification_thresholds(THRESHOLDS_DEFAULT_PATH)
        custom, base = build_fixture_candidates(self.dataset)
        report = compare_shadow_routes(
            self.dataset, (custom, base), clock=StepClock(0.01)
        )
        verdict = apply_qualification_thresholds(report, thresholds)
        registry = EmbeddingAliasRegistry(thresholds)
        registry.register_candidate(custom)
        registry.register_candidate(base)
        with self.assertRaises(EmbeddingAliasError):
            registry.fail_close(verdict)


if __name__ == "__main__":
    unittest.main()
