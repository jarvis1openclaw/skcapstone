#!/usr/bin/env python3
"""Verify the vendored CapAuth tree against its provenance manifest.

The vendored copy in ``vendor/capauth`` replaces the former git-plus-commit
pin to the personal upstream remote. This script enforces the supply-chain
contract:

- every vendored file matches the SHA256 recorded in ``VENDOR-MANIFEST.json``
- no vendored file is missing, added, or replaced by a symlink
- the manifest still names the approved upstream commit and package version
- the workspace manifests and lockfile resolve ``capauth`` from the vendored
  path and never from the personal upstream remote

Run with ``--write-manifest`` to regenerate the manifest after a deliberate
vendored update. The pinned commit constant in this script is the approved
pin and must be updated in the same reviewed change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "vendor" / "capauth"
MANIFEST_NAME = "VENDOR-MANIFEST.json"
MANIFEST_PATH = VENDOR_ROOT / MANIFEST_NAME
PACKAGE_MANIFEST = REPO_ROOT / "packages" / "capauth" / "pyproject.toml"
WORKSPACE_MANIFEST = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "uv.lock"

PACKAGE = "capauth"
PINNED_VERSION = "0.3.1"
PINNED_COMMIT = "183c04a7c623e8abcf37bd705bf8bca1deb4a364"  # pragma: allowlist secret
UPSTREAM_URL = "https://github.com/smilinTux/capauth.git"
FORBIDDEN_REMOTE = "github.com/smilinTux/capauth"
HASH_ALGORITHM = "sha256"

BUILD_ARTIFACT_SUFFIXES = (".pyc", ".pyo")

INCLUDED_UPSTREAM_PATHS = [
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "pyproject.toml",
    "src/",
]

EXCLUDED_UPSTREAM_PATHS = [
    "AGENTS.md",
    "AI-ADVOCATE.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "Dockerfile.authentik-capauth",
    "MISSION.md",
    "SECURITY.md",
    "SKILL.md",
    "SOP.md",
    "authentik-custom/",
    "bin/",
    "browser-extension/",
    "deploy/",
    "docs/",
    "index.d.ts",
    "index.js",
    "integrations/",
    "openclaw-plugin.archived-2026-04-23/",
    "package.json",
    "phone-signer/",
    "scripts/",
    "skill.yaml",
    "stages/",
    "tests/",
    "tools/",
    "urllib.error",
    "web/",
]

LOCAL_MODIFICATIONS = [
    "pyproject.toml: static version 0.3.1 replaces setuptools_scm dynamic "
    "versioning; setuptools_scm removed from build requirements and the "
    "[tool.setuptools_scm] section removed, because the vendored tree carries "
    "no git metadata for setuptools_scm to inspect",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_build_artifact(path: Path) -> bool:
    if path.suffix in BUILD_ARTIFACT_SUFFIXES:
        return True
    return any(
        part == "__pycache__" or part.endswith(".egg-info") for part in path.parts
    )


def _vendor_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"vendored package root does not exist: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink in vendored tree denied: {path}")
        if path.is_dir() or path.name == MANIFEST_NAME:
            continue
        if _is_build_artifact(path):
            continue
        files.append(path)
    return files


def write_manifest(
    root: Path = VENDOR_ROOT, manifest_path: Path = MANIFEST_PATH
) -> Path:
    """Regenerate the provenance manifest for the current vendored tree."""
    files = {
        path.relative_to(root).as_posix(): _sha256(path) for path in _vendor_files(root)
    }
    manifest = {
        "package": PACKAGE,
        "version": PINNED_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "upstream_url": UPSTREAM_URL,
        "upstream_commit": PINNED_COMMIT,
        "vendored_at": "2026-08-21",
        "vendored_by": "SKL-S1-13",
        "license": "GPL-3.0-or-later",
        "included_upstream_paths": INCLUDED_UPSTREAM_PATHS,
        "excluded_upstream_paths": EXCLUDED_UPSTREAM_PATHS,
        "local_modifications": LOCAL_MODIFICATIONS,
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _verify_manifest_fields(manifest: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    expected = {
        "package": PACKAGE,
        "version": PINNED_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "upstream_url": UPSTREAM_URL,
        "upstream_commit": PINNED_COMMIT,
    }
    for key, wanted in expected.items():
        if manifest.get(key) != wanted:
            problems.append(
                f"manifest field {key} is {manifest.get(key)!r}, expected {wanted!r}"
            )
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        problems.append("manifest files map is missing or empty")
    return problems


def verify_tree(
    root: Path = VENDOR_ROOT, manifest_path: Path = MANIFEST_PATH
) -> list[str]:
    """Return a list of provenance violations for the vendored tree."""
    problems: list[str] = []
    if not manifest_path.is_file():
        return [f"vendored provenance manifest is missing: {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems.extend(_verify_manifest_fields(manifest))
    recorded = manifest.get("files", {})
    if not isinstance(recorded, dict):
        recorded = {}
    observed = {
        path.relative_to(root).as_posix(): _sha256(path) for path in _vendor_files(root)
    }
    for name in sorted(set(recorded) - set(observed)):
        problems.append(f"manifest file missing from vendored tree: {name}")
    for name in sorted(set(observed) - set(recorded)):
        problems.append(f"unrecorded file in vendored tree: {name}")
    for name in sorted(set(recorded) & set(observed)):
        if recorded[name] != observed[name]:
            problems.append(f"hash mismatch in vendored tree: {name}")
    return problems


def verify_dependency_wiring() -> list[str]:
    """Return violations if capauth still resolves outside the vendored copy."""
    problems: list[str] = []
    package_text = PACKAGE_MANIFEST.read_text(encoding="utf-8")
    workspace_text = WORKSPACE_MANIFEST.read_text(encoding="utf-8")
    lock_text = LOCKFILE.read_text(encoding="utf-8")
    if FORBIDDEN_REMOTE in package_text:
        problems.append(f"{PACKAGE_MANIFEST} still references the personal remote")
    if FORBIDDEN_REMOTE in workspace_text:
        problems.append(f"{WORKSPACE_MANIFEST} still references the personal remote")
    if FORBIDDEN_REMOTE in lock_text:
        problems.append(f"{LOCKFILE} still references the personal remote")
    if f'"{PACKAGE}=={PINNED_VERSION}"' not in package_text:
        problems.append(f"{PACKAGE_MANIFEST} does not pin {PACKAGE}=={PINNED_VERSION}")
    if 'capauth = { path = "vendor/capauth"' not in workspace_text:
        problems.append(
            f"{WORKSPACE_MANIFEST} does not source capauth from vendor/capauth"
        )
    if 'editable = "vendor/capauth"' not in lock_text:
        problems.append(f"{LOCKFILE} does not lock capauth to vendor/capauth")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="regenerate VENDOR-MANIFEST.json from the current vendored tree",
    )
    args = parser.parse_args()
    if args.write_manifest:
        path = write_manifest()
        print(f"vendored provenance manifest written: {path}")
        return 0
    problems = verify_tree() + verify_dependency_wiring()
    if problems:
        for problem in problems:
            print(f"vendored capauth violation: {problem}", file=sys.stderr)
        return 1
    file_count = len(_vendor_files(VENDOR_ROOT))
    print(
        "vendored capauth verified: "
        f"{file_count} files, version {PINNED_VERSION}, "
        f"upstream commit {PINNED_COMMIT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
