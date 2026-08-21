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
SCALING_DOC = ROOT / "docs/architecture/POSTGRES-PRINCIPAL-SCALING.md"
SCALING_AMENDMENT = ROOT / "docs/approval/AMENDMENT-SKL-S3-08.md"
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
        for path in (
            PERSISTENCE_DOC,
            CAPAUTH_DOC,
            APPROVAL_DOC,
            SCALING_DOC,
            SCALING_AMENDMENT,
        ):
            with self.subTest(doc=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)  # em dash
                self.assertNotIn("\u2013", text)  # en dash

    def test_principal_scaling_decision_fails_closed_and_is_human_gated(self) -> None:
        decision = SCALING_DOC.read_text(encoding="utf-8")
        amendment = SCALING_AMENDMENT.read_text(encoding="utf-8")
        normalized_decision = " ".join(decision.split())
        for required in (
            "database-owned authorization-context lease",
            "caller-set PostgreSQL variable",
            "atomically consumes the lease",
            "transaction ID",
            "NOBYPASSRLS",
            "cross-Tenant and cross-Matter denial",
            "existing per-principal `session_user` contract remains authoritative",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized_decision)
        self.assertIn("Status: proposed, pending human approval", amendment)
        self.assertIn("No hash-pinned approved document was modified", amendment)


if __name__ == "__main__":
    unittest.main()
