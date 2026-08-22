"""Validate the source-linked official drafting style profile artifact."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
HAMMERTIME = ROOT.parent / "hammerTime"
DATA = ROOT / "config/drafting_styles/official-drafting-style-profiles.json"
SCHEMA = ROOT / "config/drafting_styles/official-drafting-style-profiles.schema.json"


def validate() -> None:
    data = json.loads(DATA.read_text())
    schema = json.loads(SCHEMA.read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        raise ValueError("\n".join(f"{list(error.path)}: {error.message}" for error in errors))
    if len({rule["category"] for rule in data["rules"]}) != 17:
        raise ValueError("the matrix must cover all 17 requested profile categories")
    for rule in data["rules"]:
        if rule["source"]["source_sha256"] != next(
            source["source_sha256"] for source in data["sources"] if source["source_id"] == rule["source"]["source_id"]
        ):
            raise ValueError(f"source hash mismatch for {rule['rule_id']}")
        if rule["review_status"] != "human_review_required":
            raise ValueError(f"unexpected review status for {rule['rule_id']}")
        locator = rule["source"]["locator"]
        source_path = HAMMERTIME / locator["normalized_path"]
        if not source_path.is_file():
            raise ValueError(f"missing source locator file for {rule['rule_id']}: {source_path}")
        line_count = len(source_path.read_text(errors="replace").splitlines())
        if locator["line_end"] < locator["line_start"] or locator["line_end"] > line_count:
            raise ValueError(f"invalid source locator range for {rule['rule_id']}")


if __name__ == "__main__":
    validate()
    print(f"validated {DATA.relative_to(ROOT)}")
