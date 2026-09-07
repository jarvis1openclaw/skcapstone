"""Validate bounded review work carried by an incomplete Link lineage manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_RECOMMENDATIONS = 50


def load_review_work(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "skfleet.link-lineage/v1":
        raise ValueError("lineage schema invalid")
    evidence = str(data.get("evidence_hash") or "")
    unsigned = dict(data)
    unsigned.pop("evidence_hash", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if not _DIGEST.fullmatch(evidence) or hashlib.sha256(encoded.encode()).hexdigest() != evidence:
        raise ValueError("lineage evidence invalid")
    source_revision = str(data.get("source_revision") or "")
    if not _DIGEST.fullmatch(source_revision):
        raise ValueError("lineage source revision invalid")
    raw = data.get("review_work_recommendations")
    if not isinstance(raw, list) or len(raw) > MAX_RECOMMENDATIONS:
        raise ValueError("review work bound invalid")
    recommendations: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") != "review-work":
            raise ValueError("review work malformed")
        if item.get("reason") not in {"missing_terminal_review", "review_not_bound_to_head"}:
            raise ValueError("review work reason invalid")
        if not _SHA.fullmatch(str(item.get("head_revision") or "")):
            raise ValueError("review work head invalid")
        if not _SHA.fullmatch(str(item.get("base_revision") or "")):
            raise ValueError("review work base invalid")
        if not _DIGEST.fullmatch(str(item.get("card_generation") or "")):
            raise ValueError("review work card generation invalid")
        reviewers = item.get("reviewer_candidates")
        owner = str(item.get("source_owner") or "")
        if not isinstance(reviewers, list) or not reviewers:
            raise ValueError("review work reviewer missing")
        if any(
            not isinstance(reviewer, dict)
            or reviewer.get("seat") != "seraph"
            or reviewer.get("eligible") is not True
            or reviewer.get("identity") == owner
            or reviewer.get("name") == owner
            for reviewer in reviewers
        ):
            raise ValueError("review work reviewer not distinct")
        recommendations.append(dict(item))
    return source_revision, evidence, recommendations
