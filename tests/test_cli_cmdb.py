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

try:  # the collectors ship in skcoord, which CI installs from the release
    import skcoord.discovery  # noqa: F401

    HAS_DISCOVERY = True
except ImportError:  # pragma: no cover - depends on the installed skcoord
    HAS_DISCOVERY = False

# scan/reconcile/drift need skcoord.discovery. Gate them rather than let the
# suite go red against a released skcoord that predates it, but gate them
# LOUDLY: the reason names the exact upgrade, and `pytest -rs` lists them, so
# this cannot quietly stay skipped once skcoord ships the module.
needs_discovery = pytest.mark.skipif(
    not HAS_DISCOVERY,
    reason="needs skcoord.discovery (skcoord#14); upgrade skcoord to activate these",
)


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


@needs_discovery
def test_scan_never_writes(home: Path) -> None:
    result = run("scan", "--no-local", "--json")
    assert result.exit_code == 0
    assert CMDBManager(home).list_cis() == [], "scan is read-only"


@needs_discovery
def test_reconcile_is_dry_by_default(home: Path) -> None:
    """--apply is opt-in. A scan that writes by default cannot be run twice."""
    (home / "registry").mkdir(parents=True)
    (home / "registry" / "svc.json").write_text(json.dumps({"name": "svc"}))

    payload = json.loads(run("reconcile", "--no-local", "--json").output)

    assert payload["applied"] is False
    assert payload["counts"]["created"] == 1
    assert CMDBManager(home).list_cis() == [], "the dry run must not write"


@needs_discovery
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


@needs_discovery
def test_scan_says_out_loud_when_it_observed_nothing(home: Path) -> None:
    """Reading only specs must not look like finding a clean fleet."""
    result = run("scan", "--no-local")
    assert result.exit_code == 0
    assert "No runners" in result.output


@needs_discovery
def test_drift_says_out_loud_when_it_observed_nothing(home: Path) -> None:
    result = run("drift", "--no-local")
    assert result.exit_code == 0
    assert "drift cannot be measured" in result.output


# ── retire (CMDB-8) ──────────────────────────────────────────────────────


def test_retire_sets_status_and_is_idempotent(seeded: Path) -> None:
    """Retirement is a status event, not a deletion: the record stays."""
    first = run("retire", "ci-service-skgateway", "--json")
    assert first.exit_code == 0
    assert json.loads(first.output) == {
        "retired": ["ci-service-skgateway"],
        "already_retired": [],
        "not_found": [],
    }
    assert CMDBManager(seeded).get_ci("ci-service-skgateway").status == "retired"

    second = run("retire", "ci-service-skgateway", "--json")
    assert json.loads(second.output)["already_retired"] == ["ci-service-skgateway"]

    ci = CMDBManager(seeded).get_ci("ci-service-skgateway")
    assert ci.attributes == {"port": 18991}, "retire must not touch the record"


def test_retire_records_the_reason_in_the_event_log(seeded: Path) -> None:
    run("retire", "ci-service-skgateway", "--note", "ephemeral accretion")
    events = (seeded / "cmdb" / "ci-service-skgateway" / "events").glob("*.jsonl")
    status_events = [
        json.loads(line)
        for f in events
        for line in f.read_text().splitlines()
        if line and json.loads(line).get("action") == "status"
    ]
    assert status_events, "the retire must leave a status event"
    last = status_events[-1]
    assert last["status"] == "retired"
    assert last["note"] == "ephemeral accretion"


def test_retire_with_no_ids_or_orphans_fails_cleanly(home: Path) -> None:
    result = run("retire")
    assert result.exit_code != 0
    assert "--orphans" in result.output


def test_retire_on_a_missing_ci_fails_cleanly(seeded: Path) -> None:
    result = run("retire", "ci-service-nope", "--json")
    assert result.exit_code != 0
    assert "ci-service-nope" in result.output


@needs_discovery
def test_retire_orphans_only_retires_the_unseen(seeded: Path) -> None:
    """A discovered CI the scan no longer sees is an orphan; retire --orphans
    takes exactly those, and a CI the scan still sees is left alone."""
    mgr = CMDBManager(seeded)
    still_seen = mgr.create_ci("still-up", "service", node="testnode", tags=["discovered"])
    (seeded / "registry").mkdir(parents=True, exist_ok=True)
    (seeded / "registry" / "svc.json").write_text(json.dumps({"name": "still-up"}))

    payload = json.loads(run("retire", "--orphans", "--no-local", "--json").output)

    assert payload["retired"] == ["ci-service-skgateway"]
    assert "ci-service-still-up" not in payload["retired"]
    assert CMDBManager(seeded).get_ci(still_seen.id).status == "operational"


@needs_discovery
def test_retire_orphans_on_a_clean_fleet_says_so(home: Path) -> None:
    result = run("retire", "--orphans", "--no-local")
    assert result.exit_code == 0
    assert "No orphan CIs" in result.output


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


@needs_discovery
def test_host_flag_accepts_a_bare_name_and_an_ssh_target() -> None:
    from skcapstone.cli.cmdb import _build_runners

    runners = _build_runners(("alpha", "beta=cbrd21@100.86.156.5"), local=False)

    assert [r.host for r in runners] == ["alpha", "beta"]
    assert runners[0].target == "alpha"
    assert runners[1].target == "cbrd21@100.86.156.5"


def test_no_local_and_no_host_means_no_runners() -> None:
    from skcapstone.cli.cmdb import _build_runners

    assert _build_runners((), local=False) == []
