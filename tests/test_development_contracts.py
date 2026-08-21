from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

NUMBER_WORDS = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Eleven",
    12: "Twelve",
    13: "Thirteen",
    14: "Fourteen",
    15: "Fifteen",
    16: "Sixteen",
}

PERSISTENCE_DOC = ROOT / "docs/development/PERSISTENCE.md"
CAPAUTH_DOC = ROOT / "docs/development/CAPAUTH.md"
APPROVAL_DOC = ROOT / "docs/approval/ARCHITECTURE-APPROVAL.md"
MANIFEST = ROOT / "migrations/manifest.json"


def _manifest_prefixes() -> list[str]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [entry["file"].split("_", 1)[0] for entry in payload["migrations"]]


class DevelopmentContractTests(unittest.TestCase):
    """Keep the feeding development contracts aligned with migration reality."""

    def test_persistence_documents_every_manifest_migration(self) -> None:
        doc = PERSISTENCE_DOC.read_text(encoding="utf-8")
        prefixes = _manifest_prefixes()
        self.assertGreaterEqual(len(prefixes), 12)
        for prefix in prefixes:
            with self.subTest(migration=prefix):
                self.assertIn(prefix, doc)

    def test_persistence_migration_count_matches_manifest(self) -> None:
        doc = PERSISTENCE_DOC.read_text(encoding="utf-8")
        count = len(_manifest_prefixes())
        word = NUMBER_WORDS[count]
        self.assertIn(f"{word} digest-pinned migrations", doc)

    def test_capauth_rollback_matches_migration_reality(self) -> None:
        doc = CAPAUTH_DOC.read_text(encoding="utf-8")
        self.assertNotIn("adds no migration", doc)
        rollback_match = re.search(r"## Rollback\n(.*?)\Z", doc, flags=re.DOTALL)
        self.assertIsNotNone(rollback_match)
        assert rollback_match is not None
        rollback = rollback_match.group(1)
        for prefix in ("0008", "0009", "0010", "0011", "0012"):
            with self.subTest(migration=prefix):
                self.assertIn(prefix, rollback)
        for token in (
            "capability_revocations",
            "capability_replay_reservations",
            "capability_principal_snapshot",
            "sklegal_runtime",
            "backup and restore",
        ):
            with self.subTest(token=token):
                self.assertIn(token, rollback)

    def test_repaired_docs_use_ascii_dashes_only(self) -> None:
        for path in (PERSISTENCE_DOC, CAPAUTH_DOC, APPROVAL_DOC):
            with self.subTest(doc=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)  # em dash
                self.assertNotIn("\u2013", text)  # en dash


if __name__ == "__main__":
    unittest.main()
