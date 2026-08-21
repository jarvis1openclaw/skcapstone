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

CREATE_FUNCTION = re.compile(
    r"create\s+(?P<replace>or\s+replace\s+)?function\s+"
    r"(?P<schema>[a-z_][a-z0-9_]*)\.(?P<name>[a-z_][a-z0-9_]*)\s*"
    r"\((?P<args>[^)]*)\)",
    re.IGNORECASE,
)
DROP_FUNCTION = re.compile(
    r"drop\s+function\s+"
    r"(?P<schema>[a-z_][a-z0-9_]*)\.(?P<name>[a-z_][a-z0-9_]*)\s*"
    r"\((?P<args>[^)]*)\)",
    re.IGNORECASE,
)
REVOKE_PUBLIC_FUNCTION = re.compile(
    r"revoke\s+.+?\s+on\s+function\s+"
    r"(?P<schema>[a-z_][a-z0-9_]*)\.(?P<name>[a-z_][a-z0-9_]*)\s*"
    r"\((?P<args>[^)]*)\)\s+from\s+public\b",
    re.IGNORECASE,
)
GRANT_FUNCTION = re.compile(
    r"grant\s+.+?\s+on\s+function\s+"
    r"(?P<schema>[a-z_][a-z0-9_]*)\.(?P<name>[a-z_][a-z0-9_]*)\s*"
    r"\((?P<args>[^)]*)\)\s+to\s+(?P<roles>[^;]+);",
    re.IGNORECASE,
)
BLANKET_REVOKE = re.compile(
    r"revoke\s+all\s+on\s+all\s+functions\s+in\s+schema\s+"
    r"(?P<schemas>[^;]+?)\s+from\s+public\b",
    re.IGNORECASE | re.DOTALL,
)
ARG_TYPE_PREFIXES = ("timestamp", "time", "double", "character", "bit")


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


def _arg_types(args: str) -> str:
    types = []
    for raw in args.split(","):
        arg = re.sub(r"(?i)\s+default\s+.*$", "", raw.strip())
        if not arg:
            continue
        words = arg.split()
        if len(words) > 1 and words[0].lower() in ("in", "out", "inout", "variadic"):
            words = words[1:]
        if len(words) > 1 and words[0].lower() not in ARG_TYPE_PREFIXES:
            words = words[1:]
        types.append(" ".join(words))
    return ", ".join(types)


def _signature(match: re.Match[str]) -> str:
    collapsed = _arg_types(re.sub(r"\s+", " ", match["args"])).lower()
    return f"{match['schema'].lower()}.{match['name'].lower()}({collapsed})"


def validate_function_privileges(root: Path, names: list[str]) -> None:
    """Require privilege hardening for every sklegal_* migration function.

    PostgreSQL grants EXECUTE on new functions to PUBLIC by default, and the
    CapAuth definer functions make a missed revoke a real privilege leak. A
    creation is compliant when its own file revokes PUBLIC explicitly or a
    blanket schema revoke appears in the same or a later migration. A DROP
    plus CREATE re-creation loses every privilege, so earlier grants must be
    re-applied in the same or a later migration; CREATE OR REPLACE preserves
    privileges and needs neither.
    """

    creations: list[list[tuple[str, bool]]] = []
    drops: list[set[str]] = []
    revokes: list[set[str]] = []
    grants: list[dict[str, set[str]]] = []
    blankets: list[set[str]] = []
    for name in names:
        up, _down = split_migration(
            (root / name).read_text(encoding="utf-8"), name=name
        )
        file_creates: list[tuple[int, str, bool]] = []
        for match in CREATE_FUNCTION.finditer(up):
            if match["schema"].lower().startswith("sklegal_"):
                file_creates.append(
                    (match.start(), _signature(match), bool(match["replace"]))
                )
        creations.append(
            [(sig, replaced) for _pos, sig, replaced in sorted(file_creates)]
        )
        drops.append({_signature(match) for match in DROP_FUNCTION.finditer(up)})
        revokes.append(
            {_signature(match) for match in REVOKE_PUBLIC_FUNCTION.finditer(up)}
        )
        file_grants: dict[str, set[str]] = {}
        for match in GRANT_FUNCTION.finditer(up):
            roles = {
                role.strip().lower()
                for role in match["roles"].split(",")
                if role.strip()
            }
            file_grants.setdefault(_signature(match), set()).update(roles)
        grants.append(file_grants)
        file_blankets: set[str] = set()
        for match in BLANKET_REVOKE.finditer(up):
            file_blankets.update(
                re.sub(r"\s+", "", match["schemas"]).lower().split(",")
            )
        blankets.append(file_blankets)

    blanket_cover: list[set[str]] = [set() for _ in names]
    covered: set[str] = set()
    for index in range(len(names) - 1, -1, -1):
        covered = covered | blankets[index]
        blanket_cover[index] = covered

    problems: list[str] = []
    alive: dict[str, int] = {}
    accrued: dict[str, set[str]] = {}
    historical: dict[str, set[str]] = {}
    for index, name in enumerate(names):
        for sig in sorted(drops[index]):
            if sig in alive:
                del alive[sig]
                historical[sig] = accrued.pop(sig, set())
        for sig, replaced in creations[index]:
            required_grants: set[str] = set()
            if sig in alive:
                if not replaced:
                    required_grants = accrued.get(sig, set())
                else:
                    accrued.setdefault(sig, set())
                    continue
            else:
                required_grants = historical.get(sig, set())
                alive[sig] = index
                accrued[sig] = set()
            schema = sig.split(".", maxsplit=1)[0]
            if sig not in revokes[index] and schema not in blanket_cover[index]:
                problems.append(f"{name}: missing REVOKE FROM PUBLIC for {sig}")
            if required_grants:
                reapplied: set[str] = set()
                for later in range(index, len(names)):
                    reapplied.update(grants[later].get(sig, set()))
                missing = sorted(required_grants - reapplied)
                if missing:
                    problems.append(
                        f"{name}: re-created {sig} lost grants to {missing}"
                    )
        for sig, roles in grants[index].items():
            if sig in alive:
                accrued.setdefault(sig, set()).update(roles)
    if problems:
        raise MigrationError(
            "function privilege hardening failed: " + "; ".join(problems)
        )


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
    validate_function_privileges(root, names)
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
