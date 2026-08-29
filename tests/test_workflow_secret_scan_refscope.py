"""Guard the secret-scan workflow's checked-out ref boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "secret-scan.yml"


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _git(cwd: Path, *args: str) -> str:
    return _run("git", *args, cwd=cwd).stdout.strip()


def _scan_step() -> tuple[dict, str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["gitleaks"]["steps"]
    install = next(step for step in steps if step.get("name") == "Install gitleaks")
    scan = next(step for step in steps if step.get("name", "").startswith("Scan ("))
    return install, scan["run"]


def _make_ref_fixture(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.name", "Secret Scan Test")
    _git(repo, "config", "user.email", "secret-scan-test@example.invalid")
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "clean main")
    clean_commit = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-q", "-c", "portfolio-canary")
    synthetic_value = "".join(("A1b2C3d4", "E5f6G7h8", "I9j0K1l2"))
    (repo / "fixture.json").write_text(
        json.dumps({"idempotency_key": synthetic_value}) + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "fixture.json")
    _git(repo, "commit", "-q", "-m", "add synthetic canary finding")
    finding_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/portfolio-canary", finding_commit)
    _git(repo, "switch", "-q", "main")
    return repo, clean_commit, finding_commit


def _scan(
    gitleaks: str,
    repo: Path,
    report: Path,
) -> subprocess.CompletedProcess[str]:
    return _run(
        gitleaks,
        "detect",
        "--source",
        str(repo),
        "--log-opts",
        "HEAD",
        "--redact",
        "--no-banner",
        "--exit-code",
        "1",
        "--report-format",
        "json",
        "--report-path",
        str(report),
        cwd=repo,
        check=False,
    )


def test_workflow_preserves_secret_scan_contract() -> None:
    install, scan = _scan_step()

    assert install["env"]["GITLEAKS_VERSION"] == "8.28.0"
    assert "--config .gitleaks.toml" in scan
    assert "--baseline-path .gitleaks-baseline.json" in scan
    assert "--log-opts HEAD" in scan
    assert "--redact" in scan
    assert "--exit-code 1" in scan
    assert "--all" not in scan


def test_head_scope_excludes_unrelated_fetched_ref(tmp_path: Path) -> None:
    repo, clean_commit, finding_commit = _make_ref_fixture(tmp_path)

    all_commits = set(_git(repo, "log", "--format=%H", "--all").splitlines())
    head_commits = set(_git(repo, "log", "--format=%H", "HEAD").splitlines())

    assert finding_commit in all_commits
    assert finding_commit not in head_commits
    assert clean_commit in head_commits

    _git(repo, "switch", "-q", "portfolio-canary")
    finding_head_commits = set(_git(repo, "log", "--format=%H", "HEAD").splitlines())
    assert finding_commit in finding_head_commits


@pytest.mark.skipif(
    not os.environ.get("SKCAPSTONE_GITLEAKS_BIN"),
    reason="set SKCAPSTONE_GITLEAKS_BIN to run the exact gitleaks contract",
)
def test_gitleaks_fails_only_on_finding_ancestry(tmp_path: Path) -> None:
    gitleaks = os.environ["SKCAPSTONE_GITLEAKS_BIN"]
    repo, _, finding_commit = _make_ref_fixture(tmp_path)

    clean_report = tmp_path / "clean.json"
    clean_result = _scan(gitleaks, repo, clean_report)
    assert clean_result.returncode == 0
    assert json.loads(clean_report.read_text(encoding="utf-8")) == []

    _git(repo, "switch", "-q", "portfolio-canary")
    finding_report = tmp_path / "finding.json"
    finding_result = _scan(gitleaks, repo, finding_report)
    findings = json.loads(finding_report.read_text(encoding="utf-8"))

    assert finding_result.returncode == 1
    assert len(findings) == 1
    assert findings[0]["RuleID"] == "generic-api-key"
    assert findings[0]["Commit"] == finding_commit
    assert findings[0]["Secret"] == "REDACTED"
