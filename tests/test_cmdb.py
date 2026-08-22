"""Tests for the Phase 6 CMDB / asset management (cmdb + dashboard_cmdb)."""

from __future__ import annotations

import json

import pytest

from skcapstone import dashboard_cmdb as dc
from skcapstone.cmdb import CMDBManager, make_ci_id


@pytest.fixture
def home(tmp_path):
    return tmp_path


def _declare_inventory(home):
    """Write declared fleet/registry/agent sources for the discovery reconciler.

    The reconciler only imports declared sources (fleet objects, service
    registry, agent homes); it never invents hosts and never derives asset
    health from ITIL incidents.
    """
    node_dir = home / "fleet" / "objects" / "node"
    node_dir.mkdir(parents=True)
    (node_dir / "noroc2027.json").write_text(
        json.dumps(
            {
                "name": "noroc2027",
                "spec": {"role": "worker", "address": {"hostname": "noroc2027", "ip": "10.0.0.27"}},
            }
        )
    )
    svc_dir = home / "fleet" / "objects" / "service"
    svc_dir.mkdir(parents=True)
    (svc_dir / "skmem-pg.json").write_text(
        json.dumps({"name": "skmem-pg", "spec": {"runtime": "systemd"}})
    )
    registry = home / "registry"
    registry.mkdir()
    (registry / "skchat.json").write_text(
        json.dumps({"name": "skchat", "registered_at": "2026-08-21T00:00:00Z"})
    )
    (home / "agents" / "lumina").mkdir(parents=True)


def test_create_and_get_ci(home):
    mgr = CMDBManager(home)
    ci = mgr.create_ci("skgateway", "service", node="noroc2027", attributes={"port": 18780})
    assert ci.id == make_ci_id("service", "skgateway")
    got = mgr.get_ci(ci.id)
    assert got.name == "skgateway" and got.attributes["port"] == 18780
    assert got.status == "operational"


def test_create_is_idempotent(home):
    mgr = CMDBManager(home)
    a = mgr.create_ci("skmem-pg", "datastore")
    b = mgr.create_ci("skmem-pg", "datastore")
    assert a.id == b.id
    assert len(mgr.list_cis()) == 1


def test_status_and_relationships_fold(home):
    mgr = CMDBManager(home)
    host = mgr.create_ci("noroc2027", "host")
    svc = mgr.create_ci("skchat", "service")
    mgr.set_status(svc.id, "opus", "down", note="daemon crash")
    mgr.add_relationship(svc.id, "opus", "runs_on", host.id)
    folded = mgr.get_ci(svc.id)
    assert folded.status == "down"
    assert any(r.rel_type == "runs_on" and r.target == host.id for r in folded.relationships)
    mgr.remove_relationship(svc.id, "opus", "runs_on", host.id)
    assert not mgr.get_ci(svc.id).relationships


def test_impact_analysis(home):
    mgr = CMDBManager(home)
    host = mgr.create_ci("noroc2027", "host")
    svc = mgr.create_ci("skchat", "service")
    mgr.add_relationship(svc.id, "opus", "runs_on", host.id)
    impact = mgr.impact_analysis(host.id)
    assert any(d["id"] == svc.id for d in impact["dependents"])


def test_impact_links_incidents(home):
    from skcapstone.itil import ITILManager

    mgr = CMDBManager(home)
    svc = mgr.create_ci("skchat", "service")
    itil = ITILManager(home)
    itil.create_incident(
        title="skchat down", severity="sev2", created_by="opus", affected_services=["skchat"]
    )
    impact = mgr.impact_analysis(svc.id)
    assert impact["open_incidents"] and impact["open_incidents"][0]["severity"] == "sev2"


def test_seed_from_inventory(home):
    from skcapstone.itil import ITILManager

    ITILManager(home).create_incident(
        title="skmem-pg down", severity="sev1", created_by="lumina", affected_services=["skmem-pg"]
    )
    _declare_inventory(home)
    mgr = CMDBManager(home)
    res = mgr.seed_from_inventory()
    assert res["cis"] >= 4  # host + fleet service + registry service + agent
    assert res["deprecated"] is True
    # the declared service is imported, but incident state never derives CI health
    svc = mgr.find_for_service("skmem-pg")
    assert svc and svc.status == "operational"


def test_seed_never_invents_assets(home):
    # An empty home has no declared sources: the reconciler must not fabricate any.
    mgr = CMDBManager(home)
    res = mgr.seed_from_inventory()
    assert res["cis"] == 0 and res["touched"] == 0


def test_dashboard_cmdb_overview_and_detail(home):
    _declare_inventory(home)
    dc.seed(home)
    ov = dc.get_overview(home)
    assert ov["total"] >= 4 and ov["types"]
    # detail on a host includes dependents (services running on it)
    hid = make_ci_id("host", "noroc2027")
    detail = dc.get_ci(home, hid)
    assert detail["ci"]["id"] == hid
    assert "relationships" in detail and "dependents" in detail


def test_dashboard_cmdb_routes(home):
    from starlette.testclient import TestClient

    from skcapstone.dashboard import create_app

    _declare_inventory(home)
    client = TestClient(create_app(home))
    assert client.post("/api/cmdb/seed").json()["cis"] >= 4
    assert "types" in client.get("/api/cmdb/overview").json()
    r = client.get("/cmdb")
    assert r.status_code == 200 and "CMDB" in r.text
