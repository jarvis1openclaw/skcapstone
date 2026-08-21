#!/usr/bin/env python3
"""Fail when detect-secrets reports findings outside the reviewed baseline."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / ".secrets.baseline"
EXCLUDE_FILES = (
    r"(^|/)(\.git|\.venv|\.tools|\.cleanroom-[^/]+|node_modules|dist|build|coverage|"
    r"\.mypy_cache|\.ruff_cache|\.pytest_cache|__pycache__)(/|$)|"
    r"(^|/)docs/evidence/licensing/(DOC-HAUS-RIGHTS-INVENTORY\.json|"
    r"DOC-HAUS-SBOM\.cdx\.json)$|"
    r"(^|/)(\.secrets\.baseline|uv\.lock|package-lock\.json)$"
)


def _findings(payload: dict[str, Any]) -> set[tuple[str, str, str]]:
    normalized: set[tuple[str, str, str]] = set()
    results = payload.get("results", {})
    if not isinstance(results, dict):
        raise ValueError("secret scan results must be an object")
    for filename, entries in results.items():
        if not isinstance(filename, str) or not isinstance(entries, list):
            raise ValueError("secret scan result entry is malformed")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("secret scan finding is malformed")
            normalized.add(
                (
                    filename,
                    str(entry.get("type", "")),
                    str(entry.get("hashed_secret", "")),
                )
            )
    return normalized


def scan(
    baseline_path: Path = DEFAULT_BASELINE,
    *,
    repo_root: Path = REPO_ROOT,
    exclude_files: str = EXCLUDE_FILES,
) -> int:
    executable = shutil.which("detect-secrets")
    if executable is None:
        raise RuntimeError("detect-secrets is not installed in the active environment")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    process = subprocess.run(
        [
            executable,
            "scan",
            "--all-files",
            "--exclude-files",
            exclude_files,
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    current = json.loads(process.stdout)
    additions = sorted(_findings(current) - _findings(baseline))
    if additions:
        for filename, finding_type, _ in additions:
            print(f"unreviewed secret finding: {filename}: {finding_type}")
        return 1
    print("secret scan valid: no findings outside reviewed baseline")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()
    return scan(args.baseline)


if __name__ == "__main__":
    raise SystemExit(main())
