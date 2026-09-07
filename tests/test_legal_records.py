import json
from pathlib import Path
import pytest
from skcapstone.legal_records import append_evidence, append_record, read, supersede


def test_structural_and_evidence_are_separate(tmp_path: Path):
    root = tmp_path / "legal"
    append_record(root, "d1", {"type": "deadline", "timezone": "America/New_York", "rule_version": "r2"})
    ev = append_evidence(root, "d1", {"kind": "late_upload", "received_at": "2026-09-07T10:00:00Z", "bytes_sha256": "abc"})
    assert len(read(root / "records.jsonl")) == 1
    assert len(read(root / "evidence.jsonl")) == 1
    assert ev["schema"] != read(root / "records.jsonl")[0]["schema"]


def test_tracking_number_does_not_infer_outcome(tmp_path):
    with pytest.raises(ValueError):
        append_evidence(tmp_path, "c1", {"tracking_number": "x", "outcome": "delivered"})


def test_corrupt_store_fails_closed(tmp_path):
    p = tmp_path / "records.jsonl"
    p.write_text('{"ok":true}\nnot-json\n')
    with pytest.raises(ValueError):
        append_record(tmp_path, "x", {"type": "communication"})


def test_correction_is_append_only(tmp_path):
    first = append_record(tmp_path, "c1", {"type": "communication", "response": "partial"})
    supersede(tmp_path, "c1", {"response": "returned_mail"}, supersedes=first["event_id"])
    lines = read(tmp_path / "records.jsonl")
    assert len(lines) == 2
    assert lines[1]["record"]["supersedes"] == first["event_id"]
