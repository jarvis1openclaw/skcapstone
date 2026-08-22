from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PILOT_TDD = REPO_ROOT / "docs" / "architecture" / "LIBERTY-AUTO-PILOT-TDD.md"
AMENDMENT = REPO_ROOT / "docs" / "approval" / "AMENDMENT-SKL-S2-09.md"
DESIGN_HASHES = REPO_ROOT / "docs" / "approval" / "DESIGN-HASHES.sha256"

PILOT_TDD_REL = "docs/architecture/LIBERTY-AUTO-PILOT-TDD.md"
LEGACY_SOURCE_PATH = "/mnt/cloud/onedrive/projects/DAVE AI/hammerTime"
CANONICAL_SOURCE_PATH = "/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime"


def _pinned_hash(path: str) -> str:
    for line in DESIGN_HASHES.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if name == path:
            return digest
    raise AssertionError(f"{path} not pinned in DESIGN-HASHES.sha256")


class AmendmentSklS209ProposalTests(unittest.TestCase):
    """Guards the pending SKL-S2-09 provenance amendment.

    While the amendment awaits human approval, the pinned pilot TDD must
    keep its recorded hash and the proposal record must stay internally
    consistent. When the owner approves and re-pins, this module is updated
    to the approved state per the amendment record.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.amendment = AMENDMENT.read_text(encoding="utf-8")

    def test_amendment_record_is_proposed_and_pending(self) -> None:
        self.assertIn("Amendment ID: `AMENDMENT-SKL-S2-09`", self.amendment)
        self.assertIn("Card: `a4fcdd8e`", self.amendment)
        self.assertIn("Status: proposed (pending human approval)", self.amendment)

    def test_amendment_uses_no_em_or_en_dashes(self) -> None:
        self.assertNotRegex(self.amendment, re.compile("[\u2013\u2014]"))

    def test_amendment_records_legacy_alias_and_canonical_path(self) -> None:
        self.assertIn(LEGACY_SOURCE_PATH, self.amendment)
        self.assertIn(CANONICAL_SOURCE_PATH, self.amendment)

    def test_pilot_tdd_is_not_silently_edited(self) -> None:
        digest = hashlib.sha256(PILOT_TDD.read_bytes()).hexdigest()
        self.assertEqual(_pinned_hash(PILOT_TDD_REL), digest)

    def test_proposed_post_amendment_hash_is_reproducible(self) -> None:
        source = PILOT_TDD.read_text(encoding="utf-8")
        self.assertEqual(1, source.count(LEGACY_SOURCE_PATH))
        amended = source.replace(LEGACY_SOURCE_PATH, CANONICAL_SOURCE_PATH)
        digest = hashlib.sha256(amended.encode("utf-8")).hexdigest()
        self.assertIn(
            f"{digest}  {PILOT_TDD_REL}",
            self.amendment,
            "proposed post-amendment hash must be recorded in the amendment",
        )


if __name__ == "__main__":
    unittest.main()
