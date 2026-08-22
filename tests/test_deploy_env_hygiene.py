"""Deployment environment-file safety contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "deploy" / "chiap01" / "postgres.env.example"
COMPOSE = ROOT / "deploy" / "chiap01" / "compose.sklegal.yml"
README = ROOT / "deploy" / "chiap01" / "README.md"


def _assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        assert separator, f"missing assignment in {path}: {line}"
        assignments[key] = raw_value.split(" #", 1)[0].strip()
    return assignments


def _check_ignored(relative_path: str) -> int:
    return subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative_path],
        cwd=ROOT,
        check=False,
    ).returncode


def test_postgres_env_example_is_complete_nonsecret_and_committable() -> None:
    assignments = _assignments(EXAMPLE)
    assert assignments == {
        "POSTGRES_DB": "sklegal",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "replace-before-use",  # pragma: allowlist secret
    }

    compose = COMPOSE.read_text(encoding="utf-8")
    assert "./postgres.env" in compose
    assert "$$POSTGRES_USER" in compose
    assert "$$POSTGRES_DB" in compose

    readme = README.read_text(encoding="utf-8")
    assert "cp deploy/chiap01/postgres.env.example" in readme
    assert "config --quiet" in readme
    assert "up -d postgres" in readme

    assert _check_ignored("deploy/chiap01/postgres.env") == 0
    assert _check_ignored("deploy/chiap01/postgres.env.example") == 1
    assert _check_ignored("synthetic/service.env") == 0
    assert _check_ignored("synthetic/service.env.example") == 1
