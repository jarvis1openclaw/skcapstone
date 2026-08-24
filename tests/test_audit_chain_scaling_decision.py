"""Contract tests for the SKL-S3-09-FU audit chain-head scaling decision."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISION_DOC = REPO_ROOT / "docs" / "architecture" / "AUDIT-CHAIN-SCALING.md"
AMENDMENT = REPO_ROOT / "docs" / "approval" / "AMENDMENT-SKL-S3-09-FU.md"
EVIDENCE = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "audit"
    / "SKL-S3-09-CHAIN-HEAD-SERIALIZATION-2026-08-21.md"
)
MIGRATIONS = REPO_ROOT / "migrations"


class AuditChainScalingDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.decision = DECISION_DOC.read_text(encoding="utf-8")
        cls.amendment = AMENDMENT.read_text(encoding="utf-8")

    def test_decision_doc_records_measurement_basis(self) -> None:
        normalized = " ".join(self.decision.split())
        self.assertIn("1b0a7b29", self.decision)
        self.assertIn("SKL-S3-09-CHAIN-HEAD-SERIALIZATION-2026-08-21.md", self.decision)
        self.assertIn("80 to 96 appends per second", normalized)
        self.assertTrue(EVIDENCE.is_file())

    def test_decision_doc_records_decision_and_trigger_gate(self) -> None:
        normalized = " ".join(self.decision.split())
        for required in (
            "No schema change before or during the Sprint 5 pilot",
            "Per-Matter chain heads are the approved design direction",
            "rejected as the primary mitigation",
            "50 appends per second",
            "100 ms",
            "S5-04B",
            "evidence-bearing rollback",
            "vector watermark",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)

    def test_no_per_matter_chain_migration_exists_yet(self) -> None:
        """The decision defers the schema change; a chain_heads migration
        requires the trigger gate and an approved amendment first."""
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name == "0007_append_only_audit_outbox.sql":
                continue
            with self.subTest(migration=path.name):
                self.assertIsNone(
                    re.search(
                        r"(?im)^\s*(?:create|alter|drop)\s+"
                        r"(?:table|index|trigger|policy)\b[^;\n]*\bchain_heads\b",
                        path.read_text(encoding="utf-8"),
                    ),
                    f"{path.name} changes chain_heads outside the approved 0007 design",
                )

    def test_amendment_record_is_approved(self) -> None:
        self.assertIn("Amendment ID: `AMENDMENT-SKL-S3-09-FU`", self.amendment)
        self.assertIn("Card: `2a41d17d`", self.amendment)
        self.assertIn("Status: approved", self.amendment)
        self.assertIn("## Human decision", self.amendment)
        self.assertIn("No hash-pinned approved document was modified", self.amendment)

    def test_decision_and_amendment_use_no_em_or_en_dashes(self) -> None:
        for name, text in (("decision", self.decision), ("amendment", self.amendment)):
            with self.subTest(doc=name):
                self.assertNotRegex(text, re.compile("[\u2013\u2014]"))


if __name__ == "__main__":
    unittest.main()
