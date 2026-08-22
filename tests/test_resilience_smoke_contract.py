from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/development/RESILIENCE-SMOKE.md"


class ResilienceSmokeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_recovery_targets_are_numeric_and_bounded(self) -> None:
        self.assertRegex(self.text, r"Recovery point objective \| 5 minutes or less")
        self.assertRegex(self.text, r"Recovery time objective \| 15 minutes or less")

    def test_scope_preserves_scratch_only_and_hammertime_boundary(self) -> None:
        for required in (
            "disposable data",
            "HammerTime `Inbox/`",
            "production credential",
            "production\ndatabase",
            "No production deployment",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.text)

    def test_procedure_requires_all_three_smokes_and_evidence(self) -> None:
        for required in (
            "worker-kill replay",
            "Restart the Temporal development service",
            "Dump the scratch PostgreSQL database",
            "attach command output and timestamps",
            "qualification failure, not a pass by omission",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.text)

    def test_current_evidence_does_not_overclaim_unexecuted_smokes(self) -> None:
        self.assertIn("Worker kill and replay | PASS", self.text)
        self.assertIn("Temporal restart | PENDING", self.text)
        self.assertIn("PostgreSQL backup and restore | PENDING", self.text)
        self.assertNotRegex(
            self.text,
            re.compile(
                r"Temporal restart \| PASS|PostgreSQL backup and restore \| PASS"
            ),
        )

    def test_documents_use_ascii_dashes_only(self) -> None:
        self.assertNotIn("\u2014", self.text)
        self.assertNotIn("\u2013", self.text)


if __name__ == "__main__":
    unittest.main()
