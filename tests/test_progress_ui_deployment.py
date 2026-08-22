"""Safety and reproducibility contracts for the read-only progress UI."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "chiap01" / "compose.progress-ui.yml"
NGINX = ROOT / "deploy" / "chiap01" / "progress-ui.nginx.conf"
README = ROOT / "deploy" / "chiap01" / "README.md"
PLAN = ROOT / "docs" / "planning" / "UI-FIRST-EXECUTION-PLAN.md"


def test_progress_ui_compose_is_pinned_loopback_only_and_hardened() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert re.search(r"image: nginx:[^\s]+@sha256:[0-9a-f]{64}$", compose, re.MULTILINE)
    assert '"127.0.0.1:15174:8080"' in compose
    assert 'user: "101:101"' in compose
    assert "read_only: true" in compose
    assert "cap_drop:\n      - ALL" in compose
    assert "no-new-privileges:true" in compose
    assert "restart: unless-stopped" in compose
    assert "http://127.0.0.1:8080/status/" in compose


def test_progress_ui_mounts_only_public_status_evidence() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "../../docs/status:/srv/sklegal/status:ro" in compose
    assert "../../docs/approval/ARCHITECTURE-APPROVAL.md" in compose
    assert "../../docs/tasks/SUBAGENT-TASK-TTDS.md" in compose
    assert "../../docs/planning/EPIC-SPRINT-PLAN.md" in compose
    assert "../../docs/planning/UI-FIRST-EXECUTION-PLAN.md" in compose
    assert "../../docs:/" not in compose
    assert "Inbox" not in compose
    assert "postgres.env" not in compose


def test_progress_ui_server_has_closed_routes_and_security_headers() -> None:
    nginx = NGINX.read_text(encoding="utf-8")

    required = (
        "listen 8080;",
        "server_tokens off;",
        "Content-Security-Policy",
        "default-src 'none'",
        "frame-ancestors 'none'",
        'Referrer-Policy "no-referrer"',
        'X-Content-Type-Options "nosniff"',
        'X-Frame-Options "DENY"',
        "location /status/",
        "location = /approval/ARCHITECTURE-APPROVAL.md",
        "location = /tasks/SUBAGENT-TASK-TTDS.md",
        "location = /planning/EPIC-SPRINT-PLAN.md",
        "location = /planning/UI-FIRST-EXECUTION-PLAN.md",
    )
    for value in required:
        assert value in nginx

    assert "autoindex on" not in nginx
    assert "proxy_pass" not in nginx


def test_progress_ui_operations_and_scope_are_documented() -> None:
    readme = README.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")

    assert "compose.progress-ui.yml up -d progress-ui" in readme
    assert "http://127.0.0.1:15174/status/" in readme
    assert "ssh -L 15174:127.0.0.1:15174 chiap01" in readme
    assert "compose.progress-ui.yml down" in readme
    assert "not a production SKLegal deployment" in readme
    assert "Review of all sprints" in plan
    assert "Six UI-first execution waves" not in plan
    assert "## New execution waves" in plan
    assert "Production application activation requires its own eligible task" in plan
    for sprint in range(7):
        assert f"| Sprint {sprint} |" in plan
    for wave in range(7):
        assert f"### Wave {wave}:" in plan
    for card in (
        "SKL-UI-01",
        "SKL-S2-02",
        "SKL-S2-04",
        "SKL-S4-02",
        "SKL-S4-05",
        "SKL-S6-01",
        "SKL-S6-05",
    ):
        assert card in plan
