from __future__ import annotations

import base64
import contextlib
import io
import json
import random
import string
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_secrets  # noqa: E402


class SecretBaselineTests(unittest.TestCase):
    def test_official_baseline_is_fully_reviewed_nonsecret(self) -> None:
        payload = json.loads(
            (REPO_ROOT / ".secrets.baseline").read_text(encoding="utf-8")
        )
        check_secrets._validate_reviewed_baseline(payload)
        findings = [entry for entries in payload["results"].values() for entry in entries]
        self.assertEqual(370, len(findings))
        self.assertTrue(all(entry["is_secret"] is False for entry in findings))

    def test_unreviewed_baseline_entry_fails_closed(self) -> None:
        payload = {
            "results": {
                "fixture.txt": [
                    {
                        "type": "Secret Keyword",
                        "hashed_" + "secret": "0" * 40,
                        "line_number": 1,
                    }
                ]
            }
        }
        with self.assertRaisesRegex(ValueError, "unreviewed baseline finding"):
            check_secrets._validate_reviewed_baseline(payload)

    def test_inventory_classifies_every_initial_addition(self) -> None:
        inventory = json.loads(
            (
                REPO_ROOT
                / "docs/evidence/mvp/"
                "SKL-MVP-BASE-01F3-SECRET-FINDING-INVENTORY-2026-08-23.json"
            ).read_text(encoding="utf-8")
        )
        findings = inventory["findings"]
        self.assertEqual(248, len(findings))
        self.assertEqual(248, len({entry["finding_id"] for entry in findings}))
        self.assertEqual(0, inventory["real_secret_count"])
        self.assertEqual(0, inventory["unresolved_count"])
        self.assertEqual(
            {
                "immutable_hash_or_synthetic_hex_identifier": 236,
                "nonsecret_vault_reference": 9,
                "synthetic_negative_test_marker": 3,
            },
            inventory["classification_counts"],
        )
        self.assertEqual(
            {"candidate_introduced": 16, "unchanged_historical_debt": 232},
            inventory["boundary_counts"],
        )

    def test_planted_secret_classes_fail_closed(self) -> None:
        generator = random.Random(622)

        def characters(alphabet: str, count: int) -> str:
            return "".join(generator.choice(alphabet) for _ in range(count))

        def jwt_encode(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).decode().rstrip("=")
        jwt = ".".join(
            (
                jwt_encode(b'{"alg":"HS256","typ":"JWT"}'),
                jwt_encode(b'{"sub":"synthetic-user","exp":1999999999}'),
                characters(string.ascii_letters + string.digits + "-_", 43),
            )
        )
        controls = {
            "aws": "aws_access_key_id = \""
            + "AKIA"
            + characters(string.ascii_uppercase + string.digits, 16)
            + "\"\n",
            "github": "github_token = \""
            + "ghp_"
            + characters(string.ascii_letters + string.digits, 36)
            + "\"\n",
            "bearer": "authorization = \"Bearer " + jwt + "\"\n",
            "private_key": "-----BEGIN "
            + "PRIVATE KEY-----\n"
            + base64.b64encode(
                characters(string.ascii_letters + string.digits, 96).encode()
            ).decode()
            + "\n-----END "
            + "PRIVATE KEY-----\n",
            ("pass" + "word"): "pass" + "word = \""
            + characters(string.ascii_letters + string.digits + "-_", 48)
            + "\"\n",
            "high_entropy": "artifact_digest = \""
            + characters("0123456789abcdef", 64)
            + "\"\n",
        }
        for label, planted in controls.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "planted.txt").write_text(planted, encoding="utf-8")
                baseline = root / "baseline.json"
                baseline.write_text('{"results": {}}\n', encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    result = check_secrets.scan(baseline, repo_root=root)
                self.assertEqual(1, result)


if __name__ == "__main__":
    unittest.main()
