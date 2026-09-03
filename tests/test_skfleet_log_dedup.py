"""Hourly deduplication for repeated fleet blocker diagnostics."""

import ast
import datetime
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "fleet" / "skfleet-rotate.py"


def _helper():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_log_once_per_hour"
    )
    emitted = []
    namespace = {
        "datetime": datetime,
        "json": json,
        "os": __import__("os"),
        "re": __import__("re"),
        "HOME": "/unused",
        "log": lambda directory, message: emitted.append((directory, message)),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), "exec"), namespace)
    return namespace["_log_once_per_hour"], emitted


def test_repeated_card_blocker_is_emitted_once_per_utc_hour(tmp_path: Path) -> None:
    emit_once, emitted = _helper()
    first_hour = datetime.datetime(2026, 9, 3, 18, 5, tzinfo=datetime.timezone.utc)
    next_hour = first_hour + datetime.timedelta(hours=1)

    assert emit_once(
        tmp_path, "OPEN_REVIEW_EVIDENCE_BLOCKED", "abc12345", "blocked", tmp_path, first_hour
    )
    assert not emit_once(
        tmp_path, "OPEN_REVIEW_EVIDENCE_BLOCKED", "abc12345", "blocked", tmp_path, first_hour
    )
    assert emit_once(
        tmp_path, "OPEN_REVIEW_EVIDENCE_BLOCKED", "abc12345", "blocked", tmp_path, next_hour
    )
    assert [message for _, message in emitted] == ["blocked", "blocked"]

    markers = sorted(tmp_path.glob("*.json"))
    assert len(markers) == 2
    assert json.loads(markers[0].read_text(encoding="utf-8")) == {
        "card_id": "abc12345",
        "event": "OPEN_REVIEW_EVIDENCE_BLOCKED",
        "hour_utc": "20260903T18",
    }


def test_each_card_keeps_its_first_occurrence(tmp_path: Path) -> None:
    emit_once, emitted = _helper()
    now = datetime.datetime(2026, 9, 3, 18, 59, tzinfo=datetime.timezone.utc)

    assert emit_once(tmp_path, "OPEN_REVIEW_EVIDENCE_BLOCKED", "card-one", "one", tmp_path, now)
    assert emit_once(tmp_path, "OPEN_REVIEW_EVIDENCE_BLOCKED", "card-two", "two", tmp_path, now)
    assert [message for _, message in emitted] == ["one", "two"]
