from __future__ import annotations

import hashlib
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_PAGE = REPO_ROOT / "docs" / "status" / "index.html"
APPROVAL_PAGE = REPO_ROOT / "docs" / "approval" / "index.html"
APPROVAL_RECORD = REPO_ROOT / "docs" / "approval" / "ARCHITECTURE-APPROVAL.md"
RETRIEVAL_AMENDMENT = REPO_ROOT / "docs" / "approval" / "AMENDMENT-SKL-S2-10.md"
DESIGN_HASHES = REPO_ROOT / "docs" / "approval" / "DESIGN-HASHES.sha256"

EXPECTED_DESIGN_PATHS = {
    "docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md",
    "docs/architecture/LIBERTY-AUTO-PILOT-TDD.md",
    "docs/planning/EPIC-SPRINT-PLAN.md",
    "docs/tasks/SUBAGENT-TASK-TTDS.md",
    "docs/approval/index.html",
}


class _StatusPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []
        self.tags: list[str] = []
        self.html_lang: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags.append(tag)
        if tag == "html":
            self.html_lang = values.get("lang")
        if identifier := values.get("id"):
            self.ids.append(identifier)
        if tag == "a" and (href := values.get("href")):
            self.links.append(href)


class StatusPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = STATUS_PAGE.read_text(encoding="utf-8")
        cls.parser = _StatusPageParser()
        cls.parser.feed(cls.source)

    def test_page_has_accessible_static_landmarks(self) -> None:
        self.assertEqual("en", self.parser.html_lang)
        self.assertIn("main", self.parser.tags)
        self.assertIn("nav", self.parser.tags)
        self.assertIn("header", self.parser.tags)
        self.assertIn("footer", self.parser.tags)
        self.assertIn("main-content", self.parser.ids)
        self.assertIn("#main-content", self.parser.links)
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))
        self.assertNotIn("<script", self.source.lower())

    def test_status_content_preserves_current_gates_and_legal_vocabulary(self) -> None:
        required_phrases = (
            "Architecture approved",
            "Build and qualification",
            "Simulation only",
            "No production deployment",
            "No HammerTime Inbox processing",
            "No additional matter migration",
            "No outbound legal action",
            "Models produce typed proposals only",
            "Client or Matter content",
            "b04de409",
            "8137c2f5",
            "5ab203ce",
            "f8d425e1",
            "b688c5db",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)
        self.assertNotRegex(self.source, re.compile(r"\b(?:Problem|Incident)\b"))

    def test_local_evidence_links_resolve_and_no_remote_assets_are_loaded(self) -> None:
        for href in self.parser.links:
            if href.startswith("#"):
                self.assertIn(href[1:], self.parser.ids)
                continue
            with self.subTest(href=href):
                self.assertFalse(href.startswith(("http://", "https://", "//")))
                self.assertTrue((STATUS_PAGE.parent / href).resolve().is_file())

    def test_approved_review_page_still_matches_recorded_hash(self) -> None:
        digest = hashlib.sha256(APPROVAL_PAGE.read_bytes()).hexdigest()
        record = APPROVAL_RECORD.read_text(encoding="utf-8")
        self.assertIn(f"{digest}  docs/approval/index.html", record)

    def test_current_design_hash_inventory_matches_approved_amendment(self) -> None:
        lines = [
            line
            for line in DESIGN_HASHES.read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(5, len(lines))
        self.assertEqual(
            EXPECTED_DESIGN_PATHS, {line.split("  ", 1)[1] for line in lines}
        )

        amendment = RETRIEVAL_AMENDMENT.read_text(encoding="utf-8")
        record = APPROVAL_RECORD.read_text(encoding="utf-8")
        self.assertNotIn("CURRENT_HASHES_PENDING_VALIDATION", amendment)
        self.assertNotIn("CURRENT_HASHES_PENDING_VALIDATION", record)
        self.assertIn("Status: approved", amendment)
        for line in lines:
            with self.subTest(path=line.split("  ", 1)[1]):
                self.assertIn(line, amendment)
                self.assertIn(line, record)


if __name__ == "__main__":
    unittest.main()
