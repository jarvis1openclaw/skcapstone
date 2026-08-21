"""Synthetic HammerTime fixture tree builder for adapter tests.

Builds a minimal but contract-faithful HammerTime root inside a temporary
directory. No real matter content, real legacy identifiers, or HammerTime
paths are used; every value is synthetic. The tree mirrors the documented
HammerTime release contract:

- json/releases/corpus-release-<id>.json release manifests
- json/state/runtime-aliases.json runtime alias state
- json/state/decomposed-state.json sealed decomposition snapshot
- json/decomposed/<doc>.json decomposition artifacts
- incidents/_incident-registry.md legacy id to path index
- incidents/problems/<slug>/PROBLEM.md legacy matter containers
- incidents/problems/<slug>/incidents/<dir>/INCIDENT.md legacy activities
  with validation reports, packet facts, and owner directions
- Inbox/ exists to prove the adapter refuses it
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROBLEM_ID = "PRB-2099-900"
INCIDENT_ID = "INC-900"
PROBLEM_SLUG = "fixture-matter"
INCIDENT_DIR = "INC-900-fixture-activity"
RELEASE_ID = "dev-20260101-fixture-release"
PREVIOUS_RELEASE_ID = "dev-20251231-earlier-fixture"
DECOMPOSITION_ID = "0001-fixture-doc-abcdef1234"
ALIAS_TARGET = "dev"

PROBLEM_RELATIVE = f"incidents/problems/{PROBLEM_SLUG}/PROBLEM.md"
INCIDENT_RELATIVE = (
    f"incidents/problems/{PROBLEM_SLUG}/incidents/{INCIDENT_DIR}/INCIDENT.md"
)
INCIDENT_DIR_RELATIVE = f"incidents/problems/{PROBLEM_SLUG}/incidents/{INCIDENT_DIR}"
REFERENCE_RELATIVE = "reference/legal/fixture-reference.md"

PROBLEM_MARKDOWN = f"""---
problem_id: {PROBLEM_ID}
title: "Fixture matter for adapter tests"
slug: {PROBLEM_SLUG}
status: open
priority: low
category: business
created_date: 2099-01-01
updated_date: 2099-01-02
incidents:
  - {INCIDENT_DIR}
tags: [fixture]
---

# {PROBLEM_ID}: Fixture Matter

Synthetic body text for adapter tests.
"""

INCIDENT_MARKDOWN = f"""---
incident_id: {INCIDENT_ID}
problem_id: {PROBLEM_ID}
title: "Fixture activity for adapter tests"
slug: fixture-activity
status: in_progress
priority: low
created_date: 2099-01-01
updated_date: 2099-01-02
tags: [fixture]
---

# {INCIDENT_ID}: Fixture Activity

Synthetic incident body text.
"""

REGISTRY_MARKDOWN = f"""# Incident and Problem Registry (fixture)

## Open Problems

| ID | Title | Category | Priority | Incidents | Created | Status |
|----|-------|----------|----------|-----------|---------|--------|
| {PROBLEM_ID} | [Fixture matter](problems/{PROBLEM_SLUG}/PROBLEM.md) | business | low | 1 | 2099-01-01 | open |

## Open Incidents

| ID | Problem | Title | Status |
|----|---------|-------|--------|
| {INCIDENT_ID} | {PROBLEM_ID} | [Fixture activity](problems/{PROBLEM_SLUG}/incidents/{INCIDENT_DIR}/INCIDENT.md) | open |
"""

VALIDATION_REPORT = {
    "validated_date": "2099-01-02",
    "incident_id": INCIDENT_ID,
    "valid": True,
    "source_preservation": {"hashed": 2, "copied": 2, "verified": 2},
    "errors": [],
    "notices": ["synthetic fixture notice"],
}

PACKET_V1_FACTS = {
    "incident_id": INCIDENT_ID,
    "problem_id": PROBLEM_ID,
    "packet_version": 1,
    "packet_slug": "fixture-packet",
}

PACKET_V2_FACTS = {
    "incident_id": INCIDENT_ID,
    "problem_id": PROBLEM_ID,
    "packet_version": 2,
    "packet_slug": "fixture-packet",
}

OWNER_DIRECTIONS = """# Owner Directions (fixture)

Synthetic owner direction record for adapter tests.
"""

REVIEW_NOTE = """# Phase 0 Wave 1 v2 Review (fixture)

Synthetic review note for adapter tests.
"""

REFERENCE_MARKDOWN = """---
title: "Fixture reference"
category: legal
checksum: sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
---

# Fixture Reference

Synthetic reference body.
"""


def _release_manifest(release_id: str, target: str) -> dict[str, object]:
    return {
        "release_id": release_id,
        "release_target": target,
        "schema_version": 1,
        "mode": "rebuild",
        "generated_at": "2099-01-02T00:00:00+00:00",
        "source_commit": "",
        "vector_collection": f"hammertime-v3-{target}",
        "graph_name": f"hammertime-v4-{target}",
        "documents": {
            "new": [REFERENCE_RELATIVE],
            "changed": [],
            "deleted": [],
            "unchanged": [],
        },
        "document_counts": {"new": 1, "changed": 0, "deleted": 0, "unchanged": 0},
        "verification": {"qdrant_collection_ok": True},
        "decomposed_snapshot": {"snapshot_hash": "fixture", "file_count": 1},
    }


def _runtime_aliases(current: str, previous: str | None) -> dict[str, object]:
    def binding(release_id: str) -> dict[str, object]:
        return {
            "release_id": release_id,
            "manifest_path": f"json/releases/corpus-release-{release_id}.json",
            "promoted_at": "2099-01-02T00:00:00+00:00",
            "vector_collection": "hammertime-v3-dev",
            "graph_name": "hammertime-v4-dev",
        }

    alias: dict[str, object] = {"current": binding(current)}
    if previous is not None:
        alias["previous"] = binding(previous)
    return {
        "aliases": {ALIAS_TARGET: alias},
        "schema_version": 2,
        "updated_at": "2099-01-02T00:00:00+00:00",
    }


def _decomposition() -> dict[str, object]:
    return {
        "source_file": REFERENCE_RELATIVE,
        "decomposed_at": "2099-01-02T00:00:00+00:00",
        "frontmatter": {"title": "Fixture reference", "category": "legal"},
        "stats": {
            "chunks": 1,
            "claims": 1,
            "citations": 0,
            "entities": 1,
            "relationships": 0,
        },
        "chunks": [
            {
                "chunk_id": "chk_fixture0001",
                "parent_doc": REFERENCE_RELATIVE,
                "chunk_index": 0,
                "section_title": "Fixture Reference",
                "text": "# Fixture Reference\n\nSynthetic reference body.",
                "total_chunks": 1,
            }
        ],
        "claims": [
            {
                "claim_id": "clm_fixture0001",
                "text": "Synthetic fixture claim.",
                "source_file": REFERENCE_RELATIVE,
                "line": 5,
                "category": "legal",
                "confidence": "low",
            }
        ],
        "citations": [],
        "entities": [
            {
                "entity_id": "ent_fixture0001",
                "name": "Fixture Entity",
                "type": "Organization",
                "source_file": REFERENCE_RELATIVE,
                "context": "Synthetic fixture context.",
            }
        ],
        "relationships": [],
    }


def _decomposed_state() -> dict[str, object]:
    return {
        "release_id": "dev-live",
        "release_target": "dev",
        "schema_version": "1",
        "sealed_at": "2099-01-02T00:00:00+00:00",
        "snapshot": {"files": {f"{DECOMPOSITION_ID}.json": "fixture-hash"}},
    }


def _write_text(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _write_json(root: Path, relative: str, payload: dict[str, object]) -> None:
    _write_text(root, relative, json.dumps(payload, indent=2, sort_keys=True))


def build_hammertime_fixture(root: Path) -> Path:
    """Create a synthetic contract-faithful HammerTime tree under ``root``."""
    _write_json(
        root,
        f"json/releases/corpus-release-{RELEASE_ID}.json",
        _release_manifest(RELEASE_ID, "dev"),
    )
    _write_json(
        root,
        f"json/releases/corpus-release-{PREVIOUS_RELEASE_ID}.json",
        _release_manifest(PREVIOUS_RELEASE_ID, "dev"),
    )
    _write_json(
        root,
        "json/state/runtime-aliases.json",
        _runtime_aliases(RELEASE_ID, PREVIOUS_RELEASE_ID),
    )
    _write_json(root, "json/state/decomposed-state.json", _decomposed_state())
    _write_json(root, f"json/decomposed/{DECOMPOSITION_ID}.json", _decomposition())
    _write_text(root, REFERENCE_RELATIVE, REFERENCE_MARKDOWN)
    _write_text(root, "incidents/_incident-registry.md", REGISTRY_MARKDOWN)
    _write_text(root, PROBLEM_RELATIVE, PROBLEM_MARKDOWN)
    _write_text(root, INCIDENT_RELATIVE, INCIDENT_MARKDOWN)
    _write_json(
        root,
        f"{INCIDENT_DIR_RELATIVE}/validation-report.json",
        VALIDATION_REPORT,
    )
    _write_json(
        root,
        f"{INCIDENT_DIR_RELATIVE}/packet-v1-facts.json",
        PACKET_V1_FACTS,
    )
    _write_json(
        root,
        f"{INCIDENT_DIR_RELATIVE}/packet-v2-facts.json",
        PACKET_V2_FACTS,
    )
    _write_text(
        root,
        f"{INCIDENT_DIR_RELATIVE}/phase-0-wave-1-v2-review.md",
        REVIEW_NOTE,
    )
    _write_text(
        root,
        f"{INCIDENT_DIR_RELATIVE}/correspondence/OWNER-DIRECTIONS.md",
        OWNER_DIRECTIONS,
    )
    _write_text(root, "Inbox/do-not-read.md", "intake workspace sentinel\n")
    return root


def fixture_sha256(root: Path, relative: str) -> str:
    """Hash one fixture file so tests can pin expectations."""
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()
