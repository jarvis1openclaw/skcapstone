#!/usr/bin/env python3
"""Validate the ordered SKLegal migration manifest without a live database."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "migrations"
MIGRATION_NAME = re.compile(r"^(?P<sequence>[0-9]{4})_[a-z0-9_]+\.sql$")
UP_MARKER = "-- sklegal:up"
DOWN_MARKER = "-- sklegal:down"


class MigrationError(ValueError):
    """Raised when a migration manifest fails closed."""


def split_migration(sql: str, *, name: str) -> tuple[str, str]:
    """Return nonempty up and down sections from a reversible migration."""

    if sql.count(UP_MARKER) != 1 or sql.count(DOWN_MARKER) != 1:
        raise MigrationError(f"migration must have one up and down marker: {name}")
    before, after_up = sql.split(UP_MARKER, maxsplit=1)
    up, down = after_up.split(DOWN_MARKER, maxsplit=1)
    if before.strip():
        raise MigrationError(f"migration content precedes up marker: {name}")
    if not up.strip() or not down.strip():
        raise MigrationError(f"migration up and down sections must be nonempty: {name}")
    return up.strip(), down.strip()


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read migration manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MigrationError("migration manifest schema_version must be 1")
    return payload


def validate(root: Path = DEFAULT_ROOT) -> list[str]:
    payload = _load_manifest(root)
    entries = payload.get("migrations")
    if not isinstance(entries, list):
        raise MigrationError("migrations must be a list")

    names: list[str] = []
    sequences: list[int] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise MigrationError(f"migration entry {index} must be an object")
        name = entry.get("file")
        digest = entry.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise MigrationError(f"migration entry {index} is incomplete")
        match = MIGRATION_NAME.fullmatch(name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {name}")
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise MigrationError(f"migration is missing or unsafe: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise MigrationError(f"migration digest mismatch: {name}")
        split_migration(path.read_text(encoding="utf-8"), name=name)
        names.append(name)
        sequences.append(int(match.group("sequence")))

    if len(names) != len(set(names)) or len(sequences) != len(set(sequences)):
        raise MigrationError("migration filenames and sequences must be unique")
    if sequences != sorted(sequences):
        raise MigrationError("migration manifest must be ordered")
    if sequences and sequences[0] != 1:
        raise MigrationError("migration sequence must start at 0001")
    if sequences and sequences != list(
        range(sequences[0], sequences[0] + len(sequences))
    ):
        raise MigrationError("migration sequences must be contiguous")

    declared = set(names)
    present = {path.name for path in root.glob("*.sql") if path.is_file()}
    if present != declared:
        raise MigrationError("migration files and manifest entries differ")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    names = validate(args.root)
    print(f"migration manifest valid: {len(names)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
