"""Guard: the documented external-action state graph matches the domain graph.

The architecture amendment ``docs/architecture/EXTERNAL-ACTION-STATE-MACHINE.md``
records the full Communication and Execution transition graphs. This test
parses the documented edge blocks and asserts they equal the domain
``TRANSITIONS`` maps exactly, so docs and code cannot drift apart silently.
"""

from __future__ import annotations

import re
import unittest
from enum import StrEnum
from pathlib import Path

from sklegal_domain import (
    Communication,
    CommunicationStatus,
    Execution,
    ExecutionStatus,
)

DOC_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "architecture"
    / "EXTERNAL-ACTION-STATE-MACHINE.md"
)

_EDGE_PATTERN = re.compile(r"^([a-z_]+) -> ([a-z_]+)$")


def _documented_edges(heading: str) -> dict[str, frozenset[str]]:
    text = DOC_PATH.read_text(encoding="utf-8")
    marker = f"{heading}:"
    start = text.index(marker) + len(marker)
    end = text.index("```", start)
    edges: dict[str, set[str]] = {}
    for line in text[start:end].splitlines():
        match = _EDGE_PATTERN.match(line.strip())
        if match is None:
            continue
        source, target = match.groups()
        edges.setdefault(source, set())
        if target != "none":
            edges[source].add(target)
    return {source: frozenset(targets) for source, targets in edges.items()}


def _domain_edges(
    transitions: dict[StrEnum, frozenset[StrEnum]],
) -> dict[str, frozenset[str]]:
    return {
        source.value: frozenset(target.value for target in targets)
        for source, targets in transitions.items()
    }


class ExternalActionStateDocTests(unittest.TestCase):
    def test_document_exists(self) -> None:
        self.assertTrue(DOC_PATH.is_file(), f"missing document: {DOC_PATH}")

    def test_communication_edges_match_domain(self) -> None:
        documented = _documented_edges("Communication transition edges")
        expected = _domain_edges(Communication.TRANSITIONS)
        self.assertEqual(expected, documented)
        self.assertEqual(
            {status.value for status in CommunicationStatus},
            set(documented),
            "documented edges must cover every CommunicationStatus value",
        )

    def test_execution_edges_match_domain(self) -> None:
        documented = _documented_edges("Execution transition edges")
        expected = _domain_edges(Execution.TRANSITIONS)
        self.assertEqual(expected, documented)
        self.assertEqual(
            {status.value for status in ExecutionStatus},
            set(documented),
            "documented edges must cover every ExecutionStatus value",
        )

    def test_terminal_states_have_no_outbound_edges(self) -> None:
        for transitions in (Communication.TRANSITIONS, Execution.TRANSITIONS):
            for status in transitions:
                if status.value in {"receipt_verified", "cancelled"}:
                    self.assertEqual(frozenset(), transitions[status])

    def test_retry_edge_is_failed_to_queued_only(self) -> None:
        for transitions in (Communication.TRANSITIONS, Execution.TRANSITIONS):
            for source, targets in transitions.items():
                if source.value == "failed":
                    self.assertEqual(frozenset({"queued"}), targets)
                elif source.value not in {"queued", "dispatched"}:
                    self.assertNotIn("failed", {t.value for t in targets})

    def test_dispatched_cannot_be_cancelled(self) -> None:
        for transitions in (Communication.TRANSITIONS, Execution.TRANSITIONS):
            for source, targets in transitions.items():
                if source.value == "dispatched":
                    self.assertEqual(
                        {"failed", "receipt_verified"},
                        {target.value for target in targets},
                    )


if __name__ == "__main__":
    unittest.main()
