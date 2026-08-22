"""Vendored CapAuth supply-chain contract tests.

Proves that the vendored copy in ``vendor/capauth`` matches its recorded
provenance manifest, that the workspace resolves ``capauth`` from the
vendored path instead of the personal upstream remote, and that tampering
with the vendored tree or its manifest is detected.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_vendor_capauth.py"
SPEC = importlib.util.spec_from_file_location("check_vendor_capauth", MODULE_PATH)
assert SPEC and SPEC.loader
check_vendor_capauth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_vendor_capauth)

MANIFEST_PATH = ROOT / "vendor" / "capauth" / "VENDOR-MANIFEST.json"


class VendorCapAuthTests(unittest.TestCase):
    def test_vendored_tree_matches_manifest(self) -> None:
        self.assertEqual([], check_vendor_capauth.verify_tree())

    def test_dependency_wiring_uses_vendored_copy(self) -> None:
        self.assertEqual([], check_vendor_capauth.verify_dependency_wiring())

    def test_manifest_records_approved_pin(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            check_vendor_capauth.PINNED_COMMIT, manifest["upstream_commit"]
        )
        self.assertEqual(check_vendor_capauth.PINNED_VERSION, manifest["version"])
        self.assertEqual(check_vendor_capauth.UPSTREAM_URL, manifest["upstream_url"])
        self.assertEqual("sha256", manifest["hash_algorithm"])
        self.assertIn("GPL-3.0", manifest["license"])

    def test_personal_remote_absent_from_build_inputs(self) -> None:
        for relative in (
            "pyproject.toml",
            "uv.lock",
            "packages/capauth/pyproject.toml",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(
                check_vendor_capauth.FORBIDDEN_REMOTE,
                text,
                f"personal upstream remote still referenced in {relative}",
            )

    def test_installed_capauth_comes_from_vendored_tree(self) -> None:
        import capauth

        self.assertEqual(
            check_vendor_capauth.PINNED_VERSION,
            importlib.metadata.version("capauth"),
        )
        self.assertTrue(
            str(Path(capauth.__file__).resolve()).startswith(
                str((ROOT / "vendor" / "capauth").resolve())
            ),
            "installed capauth does not resolve inside vendor/capauth",
        )

    def test_manifest_tamper_detected(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            manifest_path = root / check_vendor_capauth.MANIFEST_NAME
            check_vendor_capauth.write_manifest(root, manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["module.py"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            problems = check_vendor_capauth.verify_tree(root, manifest_path)
            self.assertTrue(
                any("hash mismatch" in problem for problem in problems),
                f"tampered hash not detected: {problems}",
            )

    def test_unrecorded_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            manifest_path = root / check_vendor_capauth.MANIFEST_NAME
            check_vendor_capauth.write_manifest(root, manifest_path)
            (root / "smuggled.py").write_text("VALUE = 2\n", encoding="utf-8")
            problems = check_vendor_capauth.verify_tree(root, manifest_path)
            self.assertTrue(
                any("unrecorded file" in problem for problem in problems),
                f"unrecorded file not detected: {problems}",
            )

    def test_missing_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            target = root / "module.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            manifest_path = root / check_vendor_capauth.MANIFEST_NAME
            check_vendor_capauth.write_manifest(root, manifest_path)
            target.unlink()
            problems = check_vendor_capauth.verify_tree(root, manifest_path)
            self.assertTrue(
                any("missing from vendored tree" in problem for problem in problems),
                f"missing file not detected: {problems}",
            )

    def test_wrong_commit_detected(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            manifest_path = root / check_vendor_capauth.MANIFEST_NAME
            check_vendor_capauth.write_manifest(root, manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["upstream_commit"] = "0" * 40
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            problems = check_vendor_capauth.verify_tree(root, manifest_path)
            self.assertTrue(
                any("upstream_commit" in problem for problem in problems),
                f"wrong upstream commit not detected: {problems}",
            )


if __name__ == "__main__":
    unittest.main()
