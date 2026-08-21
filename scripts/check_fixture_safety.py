#!/usr/bin/env python3
"""Reject secret-like or protected matter content from test fixtures."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "tests" / "fixtures"
DENIED = {
    "legacy matter identifier": re.compile(r"\b(?:PRB|INC)-[0-9]{3,}\b", re.IGNORECASE),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "provider token": re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{16,}\b"),
    "social security number": re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b"),
}


def validate(root: Path = DEFAULT_ROOT) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"fixture root does not exist: {root}")
    checked: list[Path] = []
    violations: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            violations.append(f"symlink fixture denied: {path}")
            continue
        if path.stat().st_size > 1024 * 1024:
            violations.append(f"fixture exceeds 1 MiB: {path}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            violations.append(f"binary fixture denied: {path}")
            continue
        checked.append(path)
        for label, pattern in DENIED.items():
            if pattern.search(text):
                violations.append(f"{label} in {path}")
    if violations:
        raise ValueError("; ".join(violations))
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    checked = validate(args.root)
    print(f"fixture safety valid: {len(checked)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
