"""Public-synthetic API and reversible preview lifecycle tests."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sklegal_api.browser_sessions import PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE
from sklegal_api.public_synthetic_preview import CLIENT_ID, MATTER_ID, TENANT_ID, app

from scripts import mvp_preview


def test_preview_api_bootstrap_and_repeatable_client_matter_reads() -> None:
    with TestClient(app, base_url="https://testserver") as client:
        bootstrap = client.post(
            "/v1/session/bootstrap",
            json={
                "credentialReference": PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
                "tenantId": str(TENANT_ID),
            },
            headers={"Origin": "https://testserver"},
        )
        assert bootstrap.status_code == 200
        for _ in range(2):
            clients = client.get(
                "/v1/clients", headers={"X-SKLegal-Tenant": str(TENANT_ID)}
            )
            matters = client.get(
                "/v1/matters", headers={"X-SKLegal-Tenant": str(TENANT_ID)}
            )
            matter = client.get(
                f"/v1/matters/{MATTER_ID}",
                headers={"X-SKLegal-Tenant": str(TENANT_ID)},
            )
            workspace = client.get(
                f"/v1/matters/{MATTER_ID}/workspace",
                headers={"X-SKLegal-Tenant": str(TENANT_ID)},
            )
            assert clients.status_code == 200
            assert clients.json()[0]["id"] == str(CLIENT_ID)
            assert matters.status_code == 200
            assert matter.status_code == 200
            assert workspace.status_code == 200
            assert workspace.json()["matter"]["matterId"] == str(MATTER_ID)


def test_preview_api_is_explicitly_synthetic_and_contains_no_secret_values() -> None:
    assert app.state.public_synthetic_preview is True
    composition = app.state.mvp_composition
    assert composition.mode == "development"
    assert all(probe.synthetic for probe in composition.probes)
    rendered = repr(composition.browser_session_audit.events)
    assert PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE not in rendered
    for marker in ("sk-", "Bearer ", "BEGIN PRIVATE KEY"):
        assert marker not in rendered


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((mvp_preview.LOOPBACK, 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_port_collision_and_non_loopback_contract() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind((mvp_preview.LOOPBACK, 0))
    try:
        assert not mvp_preview._port_available(int(occupied.getsockname()[1]))
    finally:
        occupied.close()
    assert mvp_preview.LOOPBACK == "127.0.0.1"
    with pytest.raises(mvp_preview.PreviewError):
        mvp_preview._port_available(80)


def test_candidate_revision_and_dirty_tree_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    (root / "apps/web").mkdir(parents=True)
    (root / "uv.lock").write_text("uv\n", encoding="utf-8")
    (root / "package-lock.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=SKLegal Preview Test",
            "-c",
            "user.email=preview-test@invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert mvp_preview._validate_candidate(root, commit)["commit"] == commit
    with pytest.raises(mvp_preview.PreviewError, match="required revision"):
        mvp_preview._validate_candidate(root, "0" * 40)
    (root / "uv.lock").write_text("changed\n", encoding="utf-8")
    with pytest.raises(mvp_preview.PreviewError, match="dirty"):
        mvp_preview._validate_candidate(root, commit)


def test_stop_refuses_foreign_pid_and_clears_stale_state(tmp_path: Path) -> None:
    runtime = tmp_path / "preview"
    runtime.mkdir(mode=0o700)
    state_path = runtime / "state.json"
    process = subprocess.Popen(["sleep", "30"])
    try:
        identity = mvp_preview._process_identity(process.pid)
        foreign = dict(identity)
        foreign["cmdline_sha256"] = "0" * 64
        mvp_preview._write_state(
            state_path,
            {
                "schema": mvp_preview.STATE_SCHEMA,
                "processes": {"api": foreign, "web": foreign},
            },
        )
        with pytest.raises(mvp_preview.PreviewError, match="refusing to signal"):
            mvp_preview._stop_state(state_path)
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
    stale = {"pid": process.pid, "start_ticks": "0", "cmdline_sha256": "0" * 64}
    mvp_preview._write_state(
        state_path,
        {
            "schema": mvp_preview.STATE_SCHEMA,
            "processes": {"api": stale, "web": stale},
        },
    )
    assert mvp_preview._stop_state(state_path) == {
        "api": "already-stopped",
        "web": "already-stopped",
    }
    assert not state_path.exists()


def test_web_route_fallback_and_bounded_api_outage(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>public synthetic</main>", encoding="utf-8")
    web_port = _free_port()
    api_port = _free_port()
    server = mvp_preview._web_server(dist, web_port, api_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while True:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{web_port}/__preview/healthz", timeout=0.2
            ) as response:
                assert response.status == 200
            break
        except urllib.error.URLError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{web_port}/matters/{MATTER_ID}", timeout=1
        ) as response:
            assert b"public synthetic" in response.read()
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                f"http://127.0.0.1:{web_port}/api/healthz", timeout=1
            )
        assert caught.value.code == 502
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_state_is_mode_restricted_and_value_free(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    mvp_preview._write_state(
        state_path,
        {
            "schema": mvp_preview.STATE_SCHEMA,
            "mode": mvp_preview.MODE,
            "identity": {"commit": "a" * 40},
            "processes": {},
        },
    )
    assert state_path.stat().st_mode & 0o077 == 0
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "public-synthetic"
    text = state_path.read_text(encoding="utf-8")
    for marker in ("credential", "Bearer", "cookie", "csrf", "protected"):
        assert marker not in text


def test_built_bundle_must_enable_bootstrap_and_remove_disabled_branch(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    bundle = assets / "index.js"
    bundle.write_text("Start public-synthetic session", encoding="utf-8")
    mvp_preview._assert_preview_bundle(dist)
    bundle.write_text(
        "Start public-synthetic session;"
        "Internal authentication is unavailable in this build",
        encoding="utf-8",
    )
    with pytest.raises(mvp_preview.PreviewError, match="disabled authentication"):
        mvp_preview._assert_preview_bundle(dist)
    bundle.write_text("no bootstrap", encoding="utf-8")
    with pytest.raises(mvp_preview.PreviewError, match="does not enable"):
        mvp_preview._assert_preview_bundle(dist)
