"""Idempotent links from SKSkills into agent-framework skill roots."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

FRAMEWORK_ROOTS: dict[str, tuple[str, ...]] = {
    "claude-code": (".claude/skills",),
    "codex": (".agents/skills", ".codex/skills"),
    "cursor": (".cursor/skills",),
    "opencode": (".config/opencode/skills", ".opencode/skills"),
    "pi": (".pi/agent/skills",),
}


def skill_roots(environments: Iterable[str], home: Path | None = None) -> list[Path]:
    """Return unique framework roots in stable order."""
    base = home or Path.home()
    roots: list[Path] = []
    for environment in environments:
        for relative in FRAMEWORK_ROOTS.get(environment, ()):
            root = base / relative
            if root not in roots:
                roots.append(root)
    return roots


def installed_skskills(
    home: Path | None = None, agent: str | None = None
) -> list[tuple[str, Path]]:
    """Find installed SKSkills with a root-level SKILL.md."""
    base = home or Path(os.environ.get("SKSKILLS_HOME", "~/.skskills")).expanduser()
    candidates: list[tuple[str, Path]] = []
    installed = base / "installed"
    if installed.is_dir():
        candidates.extend(
            (entry.name, entry / "SKILL.md")
            for entry in sorted(installed.iterdir())
            if entry.is_dir() and (entry / "SKILL.md").is_file()
        )

    if agent:
        scoped = base / "agents" / agent
        if scoped.is_dir():
            candidates.extend(
                (entry.name, entry / "SKILL.md")
                for entry in sorted(scoped.iterdir())
                if entry.is_dir() and (entry / "SKILL.md").is_file()
            )

    result: dict[str, Path] = {}
    for name, path in candidates:
        result.setdefault(name, path)
    return sorted(result.items())


def link_skill(skill_name: str, source: Path, root: Path, dry_run: bool = False) -> dict[str, str]:
    """Link one skill into one framework root without overwriting real files."""
    target = root / skill_name / "SKILL.md"
    result = {"name": skill_name, "action": "skip", "path": str(target)}
    if not source.is_file():
        result.update(action="error", error=f"Source SKILL.md not found: {source}")
        return result
    if dry_run:
        result["action"] = "dry-run"
        return result

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        if target.resolve() == source.resolve():
            result["action"] = "exists"
            return result
        target.unlink()
    if target.exists():
        result["action"] = "exists"
        return result

    target.symlink_to(os.path.relpath(source, target.parent))
    result["action"] = "created"
    return result


def link_installed_skills(
    environments: Iterable[str],
    skskills_home: Path | None = None,
    home: Path | None = None,
    agent: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Link every installed SKSkill into every selected framework root."""
    results: list[dict[str, str]] = []
    for name, source in installed_skskills(skskills_home, agent):
        for root in skill_roots(environments, home):
            result = link_skill(name, source, root, dry_run=dry_run)
            result["root"] = str(root)
            results.append(result)
    return results
