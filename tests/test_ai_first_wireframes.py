from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIREFRAME_DIR = ROOT / "docs" / "planning" / "wireframes"
V1 = WIREFRAME_DIR / "index.html"
V2 = WIREFRAME_DIR / "index-v2.html"
COMPONENT_MAP = WIREFRAME_DIR / "COMPONENT-API-MAP-V2.md"
RESEARCH = (
    ROOT / "docs" / "research" / "HOWTOWININCOURT-ARCHITECTURE-REVIEW-2026-08-22.md"
)
SKGATEWAY_RESEARCH = (
    ROOT / "docs" / "research" / "SKGATEWAY-INTEGRATION-REVIEW-2026-08-22.md"
)
TASK_TDD = ROOT / "docs" / "tasks" / "SKL-UI-03-TDD.md"
SKGATEWAY_UI_TDD = ROOT / "docs" / "tasks" / "SKL-UI-04-TDD.md"
SKGATEWAY_IMPLEMENTATION_TDD = ROOT / "docs" / "tasks" / "SKL-S3-10-TDD.md"


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if values.get("href"):
            self.hrefs.append(values["href"] or "")
        if values.get("src"):
            self.sources.append(values["src"] or "")


class AiFirstWireframeTests(unittest.TestCase):
    def _parse(self, path: Path) -> tuple[str, _DocumentParser]:
        source = path.read_text(encoding="utf-8")
        parser = _DocumentParser()
        parser.feed(source)
        parser.close()
        return source, parser

    def test_version_links_and_all_local_targets_resolve(self) -> None:
        v1_source, _ = self._parse(V1)
        _, parser = self._parse(V2)
        self.assertIn('href="index-v2.html"', v1_source)
        self.assertIn("index.html", parser.hrefs)

        for href in parser.hrefs:
            if href.startswith("#"):
                self.assertIn(href[1:], parser.ids)
                continue
            self.assertFalse(href.startswith(("http://", "https://", "//")))
            self.assertTrue((V2.parent / href).resolve().exists(), href)

    def test_v2_has_unique_required_sections(self) -> None:
        _, parser = self._parse(V2)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        required = {
            "cover",
            "operating-model",
            "corpus-map",
            "cockpit",
            "intake",
            "artifacts",
            "elements",
            "recommendation",
            "strategy",
            "challenge",
            "model-routing",
            "workproduct",
            "deadlines",
            "evidence",
            "case-log",
            "failures",
            "delivery",
        }
        self.assertTrue(required.issubset(parser.ids), required - set(parser.ids))

    def test_v2_is_self_contained_and_uses_synthetic_content(self) -> None:
        source, parser = self._parse(V2)
        self.assertEqual(parser.sources, [])
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)
        self.assertIn("synthetic content only", source)
        self.assertIn("Synthetic Vehicle Contract Matter", source)

    def test_ai_first_and_model_boundaries_are_explicit(self) -> None:
        source, _ = self._parse(V2)
        for phrase in (
            "The AI drives intake",
            "routine analysis should not wait for a human click",
            "Qwen analysis",
            "frontier route",
            "typed proposal",
            "The model does not own the workflow",
            "Approval still binds one exact version",
        ):
            self.assertIn(phrase, source)

        for private_runtime_value in (
            "10.0.0.139",
            "qwen3.8-27b-huihui-abliterated-q4_k_m",
            "chiap08",
        ):
            self.assertNotIn(private_runtime_value, source)

    def test_source_classes_and_external_action_order_are_visible(self) -> None:
        source, _ = self._parse(V2)
        for phrase in (
            "Course instruction",
            "Current Authority",
            "Matter facts and evidence",
            "Model inference",
            "Human decision",
            "advocate statement",
            "Current federal Rule 26",
        ):
            self.assertIn(phrase, source)

        positions = [
            source.index(f'<div class="node">{state}</div>')
            for state in (
                "draft",
                "validated",
                "approved",
                "queued",
                "dispatched",
                "receipt_verified",
            )
        ]
        self.assertEqual(positions, sorted(positions))

    def test_artifact_intake_and_full_case_log_are_explicit(self) -> None:
        source, _ = self._parse(V2)
        for phrase in (
            "Add to this Matter",
            "original stays immutable",
            "parent and child hashes",
            "Build missing-artifact list",
            "Matter activity log and full provenance record",
            "Every retrieval, model run, tool call",
            "Export case chronology",
            "Build provenance bundle",
            "Artifact manifest",
            "Analysis dossier",
            "Action and receipt log",
        ):
            self.assertIn(phrase, source)

    def test_model_routing_seam_is_abstract_and_fail_closed(self) -> None:
        source, _ = self._parse(V2)
        for phrase in (
            "Provider-neutral model routing seam",
            "Logical SKLegal route ID",
            "Initial and rollback binding - direct local Qwen",
            "Preferred future binding - qualified SKGateway",
            "Chat Completions surface, not the OpenAI Responses API",
            "protected use blocked",
            "exact served model",
            "Workload class",
            "Model size metadata",
            "Free is a price attribute only",
            "Dependency audit",
            "one high-severity YAML denial-of-service advisory set",
            "bbf206c3",
        ):
            self.assertIn(phrase, source)

    def test_contract_docs_cover_new_provenance_requirements(self) -> None:
        task = TASK_TDD.read_text(encoding="utf-8")
        component_map = COMPONENT_MAP.read_text(encoding="utf-8")
        research = RESEARCH.read_text(encoding="utf-8")
        gateway_research = SKGATEWAY_RESEARCH.read_text(encoding="utf-8")
        gateway_ui_tdd = SKGATEWAY_UI_TDD.read_text(encoding="utf-8")
        gateway_implementation_tdd = SKGATEWAY_IMPLEMENTATION_TDD.read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        self.assertIn("ecf6d536", task)
        self.assertIn("complete Matter activity log", task)
        self.assertIn("Matter artifact intake contract owed to the UI", component_map)
        self.assertIn("Matter activity and provenance contract", component_map)
        self.assertIn("Provenance and artifact handling decision", research)
        self.assertIn("Critical live-path gap", gateway_research)
        self.assertIn("API compatibility gap", gateway_research)
        self.assertIn("js-yaml@4.1.1", gateway_research)
        self.assertIn("31194edb", gateway_ui_tdd)
        self.assertIn("bbf206c3", gateway_implementation_tdd)
        self.assertIn("Required SKGateway live-path gate", gateway_implementation_tdd)
        self.assertIn("Matter provenance and activity", agents)
        self.assertIn("Matter provenance and artifacts", claude)

    def test_changed_artifacts_use_ascii_dashes(self) -> None:
        paths = (
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            TASK_TDD,
            V2,
            COMPONENT_MAP,
            RESEARCH,
            SKGATEWAY_RESEARCH,
            SKGATEWAY_UI_TDD,
            SKGATEWAY_IMPLEMENTATION_TDD,
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("\N{EM DASH}", source, str(path))
            self.assertNotIn("\N{EN DASH}", source, str(path))


if __name__ == "__main__":
    unittest.main()
