from __future__ import annotations

from pathlib import Path

from skcapstone.skill_roots import (
    installed_skskills,
    link_installed_skills,
    link_skill,
    skill_roots,
)


def _installed(tmp_path: Path) -> Path:
    skill = tmp_path / "skskills" / "installed" / "i-have-adhd"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: i-have-adhd\n---\n", encoding="utf-8")
    return tmp_path / "skskills"


def test_skill_roots_use_current_and_legacy_codex_paths(tmp_path: Path):
    roots = skill_roots(["codex"], tmp_path)
    assert roots == [tmp_path / ".agents/skills", tmp_path / ".codex/skills"]


def test_installed_skills_are_found(tmp_path: Path):
    home = _installed(tmp_path)
    assert installed_skskills(home) == [("i-have-adhd", home / "installed/i-have-adhd/SKILL.md")]


def test_link_installed_skills_is_idempotent_and_cross_framework(tmp_path: Path):
    home = _installed(tmp_path)
    environments = ["codex", "claude-code", "opencode", "pi"]

    first = link_installed_skills(environments, skskills_home=home, home=tmp_path)
    assert len(first) == 6
    assert {entry["action"] for entry in first} == {"created"}

    second = link_installed_skills(environments, skskills_home=home, home=tmp_path)
    assert {entry["action"] for entry in second} == {"exists"}


def test_real_skill_file_is_preserved(tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    target = tmp_path / ".agents/skills/example/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("human-owned", encoding="utf-8")

    result = link_skill("example", source, tmp_path / ".agents/skills")
    assert result["action"] == "exists"
    assert target.read_text(encoding="utf-8") == "human-owned"


def test_dry_run_does_not_create_roots(tmp_path: Path):
    home = _installed(tmp_path)
    result = link_installed_skills(["codex"], skskills_home=home, home=tmp_path, dry_run=True)
    assert {entry["action"] for entry in result} == {"dry-run"}
    assert not (tmp_path / ".agents").exists()
