#!/usr/bin/env python3
"""Compute the reproducible SKLegal Codex Security worktree identity."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


IDENTITY_SEED = "local-workspace:/mnt/cloud/onedrive/projects/DAVE-AI/sklegal"
TARGET_PREFIX = "codex-security-target/v1:sha256:"
SNAPSHOT_PREFIX = "codex-security-snapshot/v1:sha256:"

EXCLUDED_PARTS = {".git", ".pytest_cache", "__pycache__"}
EXCLUDED_FILES = {
    Path("docs/security/THREAT-MODEL.md"),
}


def target_id() -> str:
    digest = hashlib.sha256(IDENTITY_SEED.encode("utf-8")).hexdigest()
    return f"{TARGET_PREFIX}{digest}"


def reviewed_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(repo_root):
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_PARTS)
        root_path = Path(root)
        for name in sorted(names):
            path = root_path / name
            relative = path.relative_to(repo_root)
            if relative in EXCLUDED_FILES or any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.is_file() and not path.is_symlink():
                files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_digest(repo_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in reviewed_files(repo_root):
        path = repo_root / relative
        record = (
            relative.as_posix().encode("utf-8")
            + b"\0"
            + file_digest(path).encode("ascii")
            + b"\0"
            + str(path.stat().st_size).encode("ascii")
            + b"\n"
        )
        digest.update(record)
    return f"{SNAPSHOT_PREFIX}{digest.hexdigest()}"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    print(f"Repository: {target_id()}")
    print(f"Version: {snapshot_digest(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
