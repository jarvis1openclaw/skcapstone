import hashlib
import json

import pytest

from skcapstone.link_review_work import load_review_work


def _manifest(item):
    data = {
        "schema": "skfleet.link-lineage/v1",
        "source_revision": "1" * 64,
        "coverage": {"unresolved": 1},
        "records": {},
        "reviewer_candidates": [],
        "diagnostics": [],
        "unresolved_prs": [7],
        "review_work_recommendations": [item],
    }
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    data["evidence_hash"] = hashlib.sha256(encoded.encode()).hexdigest()
    return data


def _item():
    return {
        "kind": "review-work",
        "reason": "missing_terminal_review",
        "repository": "org/repo",
        "pr": 7,
        "head_revision": "a" * 40,
        "base_revision": "b" * 40,
        "source_card": "source01",
        "card_generation": "2" * 64,
        "source_owner": "builder",
        "reviewer_candidates": [
            {"name": "Seraph", "seat": "seraph", "identity": "seraph", "eligible": True}
        ],
    }


def test_loads_exact_review_work_from_signed_manifest(tmp_path):
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(_manifest(_item())))
    revision, evidence, work = load_review_work(path)
    assert revision == "1" * 64
    assert len(evidence) == 64
    assert work == [_item()]


def test_rejects_non_distinct_reviewer_and_tampering(tmp_path):
    item = _item()
    item["source_owner"] = "seraph"
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(_manifest(item)))
    with pytest.raises(ValueError, match="not distinct"):
        load_review_work(path)
    data = _manifest(_item())
    data["source_revision"] = "3" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="evidence invalid"):
        load_review_work(path)
