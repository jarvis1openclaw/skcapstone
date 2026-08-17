"""Tests for the `skcapstone cmdb` CLI group.

The collectors themselves are tested in skcoord. What matters here is the
wiring, and specifically the two ways this CLI could quietly mislead an
operator: writing when it said it would not, and reporting a clean fleet when
it actually observed nothing at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from skcoord.cmdb import CMDBManager

from skcapstone.cli import main


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every cmdb command at a scratch skcapstone home."""
    monkeypatch.setattr("skcapstone.cli.cmdb.SHARED_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def seeded(home: Path) -> Path:
    mgr = CMDBManager(home)
    ci = mgr.create_ci(
        "skgateway",
        "service",
        description="model router",
        node="testnode",
        attributes={"port": 18991},
        tags=["discovered"],
    )
    mgr.add_relationship(ci.id, "test", "runs_on", "ci-host-testnode")
    return home


def run(*args):
    return CliRunner().invoke(main, ["cmdb", *args])


# ── list / show ───────────────────────────────────────────────────────────


def test_list_json_is_parseable(seeded: Path) -> None:
    result = run("list", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [c["name"] for c in payload] == ["skgateway"]


def test_list_filters_by_type_and_tag(seeded: Path) -> None:
    assert json.loads(run("list", "--type", "host", "--json").output) == []
    assert len(json.loads(run("list", "--tag", "discovered", "--json").output)) == 1
    assert json.loads(run("list", "--tag", "nope", "--json").output) == []


def test_list_on_an_empty_cmdb_says_so(home: Path) -> None:
    result = run("list")
    assert result.exit_code == 0
    assert "No configuration items" in result.output


def test_show_renders_attributes_and_relationships(seeded: Path) -> None:
    result = run("show", "ci-service-skgateway")
    assert result.exit_code == 0
    assert "skgateway" in result.output
    assert "runs_on" in result.output


def test_show_on_a_missing_ci_fails_cleanly(home: Path) -> None:
    result = run("show", "ci-service-nope")
    assert result.exit_code != 0
    assert "CI not found" in result.output


def test_impact_reports_dependents(seeded: Path) -> None:
    payload = json.loads(run("impact", "ci-service-skgateway", "--json").output)
    assert payload["ci"]["name"] == "skgateway"
    assert payload["dependents"] == []


# ── the two ways this could mislead ───────────────────────────────────────


def test_scan_never_writes(home: Path) -> None:
    result = run("scan", "--no-local", "--json")
    assert result.exit_code == 0
    assert CMDBManager(home).list_cis() == [], "scan is read-only"


def test_reconcile_is_dry_by_default(home: Path) -> None:
    """--apply is opt-in. A scan that writes by default cannot be run twice."""
    (home / "registry").mkdir(parents=True)
    (home / "registry" / "svc.json").write_text(json.dumps({"name": "svc"}))

    payload = json.loads(run("reconcile", "--no-local", "--json").output)

    assert payload["applied"] is False
    assert payload["counts"]["created"] == 1
    assert CMDBManager(home).list_cis() == [], "the dry run must not write"


def test_reconcile_apply_writes_and_is_idempotent(home: Path) -> None:
    (home / "registry").mkdir(parents=True)
    (home / "registry" / "svc.json").write_text(json.dumps({"name": "svc"}))

    first = json.loads(run("reconcile", "--no-local", "--apply", "--json").output)
    assert first["applied"] is True
    assert first["counts"]["created"] == 1
    assert len(CMDBManager(home).list_cis()) == 1

    second = json.loads(run("reconcile", "--no-local", "--apply", "--json").output)
    assert second["counts"]["created"] == 0
    assert second["counts"]["updated"] == 0


def test_scan_says_out_loud_when_it_observed_nothing(home: Path) -> None:
    """Reading only specs must not look like finding a clean fleet."""
    result = run("scan", "--no-local")
    assert result.exit_code == 0
    assert "No runners" in result.output


def test_drift_says_out_loud_when_it_observed_nothing(home: Path) -> None:
    result = run("drift", "--no-local")
    assert result.exit_code == 0
    assert "drift cannot be measured" in result.output


# ── dependency guard ──────────────────────────────────────────────────────


def test_an_old_skcoord_gets_a_message_naming_the_package(home: Path) -> None:
    """Without the guard this is a bare ImportError for a module the operator
    has never heard of."""
    with patch.dict("sys.modules", {"skcoord.discovery": None}):
        result = run("scan")

    assert result.exit_code != 0
    assert "skcoord" in result.output
    assert "too old" in result.output


# ── host selection ────────────────────────────────────────────────────────


def test_host_flag_accepts_a_bare_name_and_an_ssh_target() -> None:
    from skcapstone.cli.cmdb import _build_runners

    runners = _build_runners(("alpha", "beta=cbrd21@100.86.156.5"), local=False)

    assert [r.host for r in runners] == ["alpha", "beta"]
    assert runners[0].target == "alpha"
    assert runners[1].target == "cbrd21@100.86.156.5"


def test_no_local_and_no_host_means_no_runners() -> None:
    from skcapstone.cli.cmdb import _build_runners

    assert _build_runners((), local=False) == []
