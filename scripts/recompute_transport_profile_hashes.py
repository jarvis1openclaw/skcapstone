"""Recompute transport profile configuration hashes (SKL-S3-10).

The transport profile store pins a content hash per profile. After any edit
to ``config/model_gateway/deployment/transport-profiles.json``, run this
script to rewrite each ``config_sha256`` to the canonical content hash of
its profile. The store then fails closed if a profile is later modified
without recomputation.
"""

from __future__ import annotations

import json
from pathlib import Path

from sklegal_model_gateway.models import TransportProfile
from sklegal_model_gateway.transport_profiles import profile_content_sha256

ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "config" / "model_gateway" / "deployment" / (
    "transport-profiles.json"
)


def main() -> int:
    payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    for raw in payload["profiles"]:
        # Hash the content fields only; the stored hash itself never
        # contributes to its own value.
        content = {
            key: value for key, value in raw.items() if key != "config_sha256"
        }
        profile = TransportProfile.model_validate(
            {**content, "config_sha256": "0" * 64}
        )
        digest = profile_content_sha256(profile)
        raw["config_sha256"] = digest
    text = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    STORE_PATH.write_text(text, encoding="utf-8")
    print(f"rewrote profile hashes: {STORE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
