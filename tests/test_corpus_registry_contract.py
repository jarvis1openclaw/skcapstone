from __future__ import annotations

import json
import unittest
from pathlib import Path

from sklegal_retrieval.corpus_reconciliation import CorpusDiscrepancyKind
from sklegal_retrieval.corpus_registry import CorpusCountKind

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/retrieval/corpus-registry-contract.json"
DOC_PATH = ROOT / "docs/development/CORPUS-REGISTRY.md"
TASK_TDDS = ROOT / "docs/tasks/SUBAGENT-TASK-TTDS.md"

MATERIALIZED_COUNTS = {
    "source",
    "normalized",
    "decomposition",
    "vector",
    "graph",
    "reject",
    "orphan",
    "release",
}


class CorpusRegistryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_identity_pins_card_and_task(self) -> None:
        self.assertEqual("sklegal-corpus-registry/v1", self.contract["schema"])
        self.assertEqual("f5ed9d24", self.contract["card"])
        self.assertEqual("SKL-S2-05", self.contract["task"])
        self.assertEqual(
            "docs/development/CORPUS-REGISTRY.md", self.contract["paired_document"]
        )

    def test_health_is_bounded_and_reads_only_materialized_state(self) -> None:
        health = self.contract["health"]
        self.assertEqual("materialized_registry_snapshot_only", health["read_source"])
        self.assertEqual(100, health["fixed_budget_ms"])
        self.assertEqual("unavailable", health["budget_exceeded_status"])
        self.assertEqual("unavailable", health["store_unavailable_status"])
        self.assertEqual("degraded", health["never_reconciled_status"])
        self.assertEqual(1000, health["degraded_lag_events_threshold"])
        for flag in (
            "full_tree_scan_on_health_request",
            "projection_backend_scan_on_health_request",
            "per_tenant_breakdown_on_unauthenticated_health",
            "failed_read_reports_zero_counts",
        ):
            with self.subTest(flag=flag):
                self.assertEqual("forbidden", health[flag])

    def test_materialized_counts_match_the_registry_model(self) -> None:
        self.assertEqual(MATERIALIZED_COUNTS, set(self.contract["materialized_counts"]))
        self.assertEqual(
            MATERIALIZED_COUNTS,
            {kind.value for kind in CorpusCountKind},
        )

    def test_registry_entry_fields_are_pinned(self) -> None:
        self.assertEqual(
            {
                "tenant_id",
                "release_id",
                "counts",
                "core_watermark",
                "core_event_sha256",
                "required_replay_lsn",
                "lag",
                "last_reconciled_at",
            },
            set(self.contract["registry_entry_fields"]),
        )

    def test_incremental_updates_follow_the_outbox_rules(self) -> None:
        updates = self.contract["incremental_updates"]
        self.assertEqual("transactional_outbox", updates["transport"])
        for flag in (
            "idempotent_delivery",
            "absolute_counts_not_deltas",
            "strict_event_sequence",
            "populates_required_replay_lsn_from_core_outbox",
            "watermark_compare_and_set",
        ):
            with self.subTest(flag=flag):
                self.assertTrue(updates[flag])

    def test_reconciliation_is_a_scheduled_deep_job_separate_from_health(self) -> None:
        reconciliation = self.contract["reconciliation"]
        self.assertEqual("scheduled_batch_job", reconciliation["trigger"])
        self.assertFalse(reconciliation["user_facing_health_path_permitted"])
        self.assertTrue(reconciliation["deep_scan_permitted"])
        self.assertTrue(reconciliation["writes_last_reconciled_state"])
        self.assertTrue(reconciliation["reports_complete_coverage_separately"])
        self.assertFalse(reconciliation["partial_or_timeout_writes_state"])
        self.assertTrue(reconciliation["write_conflict_aborts_run"])

    def test_reconciliation_detects_the_full_discrepancy_vocabulary(self) -> None:
        detects = set(self.contract["reconciliation"]["detects"])
        self.assertEqual(
            {kind.value for kind in CorpusDiscrepancyKind},
            detects,
        )
        self.assertEqual(
            {
                "missing_decomposition",
                "orphan_vector",
                "stale_graph",
                "changed_source",
                "missing_source",
                "count_drift",
            },
            detects,
        )

    def test_approved_documents_and_contract_use_ascii_dashes(self) -> None:
        for path in (CONTRACT_PATH, DOC_PATH, TASK_TDDS):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)

    def test_document_links_the_contract_and_the_implementation(self) -> None:
        doc = DOC_PATH.read_text(encoding="utf-8")
        normalized_doc = " ".join(doc.split())
        for required in (
            "config/retrieval/corpus-registry-contract.json",
            "f5ed9d24",
            "SKL-S2-05",
            "BoundedCorpusHealthReader",
            "DeepCorpusReconciler",
            "CorpusRegistryProjector",
            "CorpusRegistryStore",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized_doc)


if __name__ == "__main__":
    unittest.main()
