"""Validate the source-linked official drafting style profile artifact."""

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "config/drafting_styles/official-drafting-style-profiles.json"
SCHEMA = ROOT / "config/drafting_styles/official-drafting-style-profiles.schema.json"
LOCATOR_EVIDENCE = (
    ROOT / "config/drafting_styles/official-drafting-style-locator-evidence.json"
)
LOCATOR_EVIDENCE_SHA256 = (
    "824f8db82d94c0c310546f07f95dd35e09ec3917746ff15318ec88d42fb7b904"
)
PROFILE_SHA256 = "9651ce6bdabf18cc27a39ff2bed87309b179b72fe37053c852f43cd288627959"
PROVENANCE_SHA256 = "fb7101a3c91cec2a56155fcd9feb7d666438e2fcfce2a30f09189972454c5b0d"


def validate() -> None:
    data = json.loads(DATA.read_text())
    schema = json.loads(SCHEMA.read_text())
    locator_evidence_bytes = LOCATOR_EVIDENCE.read_bytes()
    if hashlib.sha256(locator_evidence_bytes).hexdigest() != LOCATOR_EVIDENCE_SHA256:
        raise ValueError("source locator evidence hash mismatch")
    locator_evidence = json.loads(locator_evidence_bytes)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path)
    )
    if errors:
        raise ValueError(
            "\n".join(f"{list(error.path)}: {error.message}" for error in errors)
        )
    if len({rule["category"] for rule in data["rules"]}) != 17:
        raise ValueError("the matrix must cover all 17 requested profile categories")
    profile_sha256 = hashlib.sha256(DATA.read_bytes()).hexdigest()
    if not (locator_evidence["profile_sha256"] == profile_sha256 == PROFILE_SHA256):
        raise ValueError("style profile hash mismatch")
    if locator_evidence["provenance_sha256"] != PROVENANCE_SHA256:
        raise ValueError("semantic provenance evidence mismatch")
    for rule in data["rules"]:
        source = next(
            source
            for source in data["sources"]
            if source["source_id"] == rule["source"]["source_id"]
        )
        if rule["source"]["source_sha256"] != source["source_sha256"]:
            raise ValueError(f"source hash mismatch for {rule['rule_id']}")
        if rule["review_status"] != "human_review_required":
            raise ValueError(f"unexpected review status for {rule['rule_id']}")
        locator = rule["source"]["locator"]
        if locator["line_end"] < locator["line_start"]:
            raise ValueError(f"invalid source locator range for {rule['rule_id']}")


if __name__ == "__main__":
    validate()
    print(f"validated {DATA.relative_to(ROOT)}")
