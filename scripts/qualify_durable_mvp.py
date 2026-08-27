#!/usr/bin/env python3
"""Run the reversible SKL-MVP-COMPOSE-01 loopback qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sklegal_api.corpus import (
    CorpusResultRead,
    CorpusScopeOptionRead,
    CorpusSearchResponseRead,
    CorpusSpanAvailableRead,
    CorpusTraceRead,
)
from sklegal_capauth import VERIFIER_POLICY_VERSION, Audience, Capability, PrincipalType

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/chiap01/compose.mvp.yml"
FIXTURE = ROOT / "tests/fixtures/mvp/public-synthetic-mvp-v1.json"
CHROME_QUALIFICATION = ROOT / "tests/qualification/session_reload_csp_qualification.mjs"
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
PRINCIPAL_ID = UUID("22222222-2222-4222-8222-222222222221")
MATTER_ID = UUID("44444444-4444-4444-8444-444444444441")
CORPUS_SOURCE_ID = "public-synthetic-authority-primary"


class QualificationError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise QualificationError(
            f"command failed: {command[0]}: {(result.stderr or result.stdout)[-1200:]}"
        )
    return result


def _port() -> int:
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait(url: str, *, expected: int = 200, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == expected:
                    return
                last = f"status {response.status}"
        except urllib.error.HTTPError as error:
            if error.code == expected:
                return
            last = f"status {error.code}"
        except OSError as error:
            last = type(error).__name__
        time.sleep(0.2)
    raise QualificationError(f"timed out waiting for {url}: {last}")


def _wait_postgres(dsn: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as connection:
                if connection.execute("SELECT 1").fetchone() == (1,):
                    return
        except psycopg.Error:
            time.sleep(0.2)
    raise QualificationError("PostgreSQL host listener did not become ready")


def _gpg_identity(home: Path) -> str:
    home.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    _run(
        [
            "gpg",
            "--batch",
            "--homedir",
            str(home),
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-gen-key",
            "SKLegal Public Synthetic Qualification",
            "ed25519",
            "sign",
            "1d",
        ]
    )
    listing = _run(
        [
            "gpg",
            "--batch",
            "--homedir",
            str(home),
            "--with-colons",
            "--list-secret-keys",
        ]
    ).stdout
    fingerprints = [
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    ]
    if not fingerprints:
        raise QualificationError("ephemeral issuer fingerprint is absent")
    return fingerprints[0]


def _issuer_policy(path: Path, fingerprint: str) -> None:
    payload = {
        "schema_version": "sklegal-trusted-issuers/v1",
        "policy_version": VERIFIER_POLICY_VERSION,
        "issuers": [
            {
                "fingerprint": fingerprint,
                "capabilities": sorted(item.value for item in Capability),
                "audiences": sorted(item.value for item in Audience),
                "principal_types": sorted(item.value for item in PrincipalType),
            }
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(path, 0o600)


def _scope(
    connection: psycopg.Connection[Any], tenant: UUID, matter: UUID | None = None
) -> None:
    connection.execute(
        "SELECT set_config('sklegal.tenant_id', %s, true)", (str(tenant),)
    )
    if matter is not None:
        connection.execute(
            "SELECT set_config('sklegal.matter_id', %s, true)", (str(matter),)
        )


def _corpus_payloads(fixture: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    authority = fixture["authorities"][0]
    query = "invented delivery date"
    search = CorpusSearchResponseRead(
        matter_id=MATTER_ID,
        query=query,
        scope_options=(
            CorpusScopeOptionRead(scope="this_matter", state="active"),
            CorpusScopeOptionRead(
                scope="tenant_corpus",
                state="unavailable",
                reason="Public synthetic composition is Matter scoped.",
            ),
            CorpusScopeOptionRead(
                scope="official_sources",
                state="unavailable",
                reason="No external source retrieval is enabled.",
            ),
        ),
        results=(
            CorpusResultRead(
                rank=1,
                score=1.0,
                snippet="Invented fixture authority for the invented delivery date.",
                source_id=CORPUS_SOURCE_ID,
                title=authority["title"],
                citation=authority["citation"],
                classification="public",
                origin="matter_corpus",
                verification_state=authority["verificationState"],
                source_version=authority["sourceVersion"],
                source_sha256=authority["contentSha256"],
                document_id="public-synthetic-authority-document",
                chunk_id="public-synthetic-authority-chunk-1",
                chunk_sha256=authority["contentSha256"],
                source_locator="fixture:authority:1",
                span_kind="text_offset",
                span_start=0,
                span_end=67,
                supersession_status="current",
            ),
        ),
        trace=CorpusTraceRead(
            scope_kind="this_matter",
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            release_id="public-synthetic-release-v1",
            projection_generation=1,
            current_projection_generation=1,
            projection_stale=False,
            backend_watermark=1,
            lag_events=0,
            lag_seconds=0.0,
            query_template_id="public-synthetic-query",
            query_template_version="v1",
            query_template_sha256="f" * 64,
            rank_path=("postgres_fts", "pgvector_exact"),
            retrieval_adapter_version="durable-postgres-v1",
            source_ids=(CORPUS_SOURCE_ID,),
            source_hashes=(authority["contentSha256"],),
        ),
    )
    span = CorpusSpanAvailableRead(
        source_id=CORPUS_SOURCE_ID,
        source_version=authority["sourceVersion"],
        source_sha256=authority["contentSha256"],
        document_id="public-synthetic-authority-document",
        citation=authority["citation"],
        title=authority["title"],
        classification="public",
        source_locator="fixture:authority:1",
        span_kind="text_offset",
        span_start=0,
        span_end=67,
        span_text="Invented fixture authority for the invented delivery date only.",
        supersession_status="current",
        jurisdiction=authority["jurisdiction"],
    )
    return search.model_dump(mode="json", by_alias=True), span.model_dump(
        mode="json", by_alias=True
    )


def _seed(core_dsn: str, retrieval_dsn: str) -> dict[str, str]:
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    search, span = _corpus_payloads(fixture)
    principal_revision = hashlib.sha256(
        f"{TENANT_ID}:{PRINCIPAL_ID}:active".encode()
    ).hexdigest()
    outbox_payload = {"query": "invented delivery date", "search": search, "span": span}
    with psycopg.connect(core_dsn) as core:
        _scope(core, TENANT_ID)
        core.execute(
            """INSERT INTO sklegal_mvp.principals
                   (tenant_id, principal_id, principal_type, subject, active, revision)
               VALUES (%s, %s, 'human', 'public-synthetic:durable-reviewer', true, %s)""",
            (TENANT_ID, PRINCIPAL_ID, principal_revision),
        )
        core.execute(
            "INSERT INTO sklegal_mvp.matter_memberships VALUES (%s, %s, %s)",
            (TENANT_ID, MATTER_ID, PRINCIPAL_ID),
        )
        for kind, record_id, matter_id, payload in (
            ("client", fixture["clients"][0]["id"], None, fixture["clients"][0]),
            ("matter", fixture["matters"][0]["id"], MATTER_ID, fixture["matters"][0]),
            ("workspace", str(MATTER_ID), MATTER_ID, fixture["workspace"]),
            ("claims", str(MATTER_ID), MATTER_ID, fixture["claimLedger"]),
        ):
            core.execute(
                """INSERT INTO sklegal_mvp.records
                       (tenant_id, record_kind, record_id, matter_id, payload)
                   VALUES (%s, %s, %s, %s, %s)""",
                (TENANT_ID, kind, record_id, matter_id, Jsonb(payload)),
            )
        core.execute(
            "INSERT INTO sklegal_mvp.policy_state VALUES (%s, 'public-synthetic-policy-v1', false, 0)",
            (TENANT_ID,),
        )
        sequence = core.execute(
            """INSERT INTO sklegal_mvp.outbox
                   (tenant_id, matter_id, idempotency_key, payload)
               VALUES (%s, %s, 'public-synthetic-projection-v1', %s)
               RETURNING sequence""",
            (TENANT_ID, MATTER_ID, Jsonb(outbox_payload)),
        ).fetchone()[0]
        core.execute(
            """INSERT INTO sklegal_mvp.projection_registry
                   VALUES (%s, %s, 1, %s, 0, 'lagging')""",
            (TENANT_ID, MATTER_ID, sequence),
        )
    with psycopg.connect(retrieval_dsn) as retrieval:
        _scope(retrieval, TENANT_ID, MATTER_ID)
        retrieval.execute(
            """INSERT INTO sklegal_retrieval.projections
                   (tenant_id, matter_id, generation, outbox_sequence,
                    idempotency_key, query, search_payload, span_payload, embedding)
               VALUES (%s, %s, 1, %s, 'public-synthetic-projection-v1', %s,
                       %s, %s, '[0.1,0.2,0.3]')
               ON CONFLICT (idempotency_key) DO NOTHING""",
            (
                TENANT_ID,
                MATTER_ID,
                sequence,
                outbox_payload["query"],
                Jsonb(search),
                Jsonb(span),
            ),
        )
    with psycopg.connect(core_dsn) as core:
        _scope(core, TENANT_ID)
        core.execute(
            """UPDATE sklegal_mvp.projection_registry
               SET retrieval_watermark = core_watermark, state = 'current'
               WHERE tenant_id = %s AND matter_id = %s""",
            (TENANT_ID, MATTER_ID),
        )
    return {
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "projection_sha256": hashlib.sha256(
            json.dumps(outbox_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _readback(core_dsn: str, retrieval_dsn: str) -> dict[str, int]:
    with psycopg.connect(core_dsn, row_factory=dict_row) as core:
        _scope(core, TENANT_ID)
        core_counts = core.execute(
            """SELECT
                 (SELECT count(*) FROM sklegal_mvp.records) AS records,
                 (SELECT count(*) FROM sklegal_mvp.outbox) AS outbox,
                 (SELECT count(*) FROM sklegal_mvp.audit_events) AS audit"""
        ).fetchone()
    with psycopg.connect(retrieval_dsn) as retrieval:
        _scope(retrieval, TENANT_ID, MATTER_ID)
        projections = retrieval.execute(
            "SELECT count(*) FROM sklegal_retrieval.projections"
        ).fetchone()[0]
        extension = retrieval.execute(
            "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()[0]
    return {
        "records": int(core_counts["records"]),
        "outbox": int(core_counts["outbox"]),
        "audit": int(core_counts["audit"]),
        "projections": int(projections),
        "pgvector": int(extension),
    }


def _rls_denials(core_app_dsn: str, retrieval_app_dsn: str) -> dict[str, bool]:
    foreign_tenant = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    foreign_matter = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    with psycopg.connect(core_app_dsn) as core:
        _scope(core, foreign_tenant)
        tenant_denied = (
            core.execute("SELECT count(*) FROM sklegal_mvp.records").fetchone()[0] == 0
        )
    with psycopg.connect(retrieval_app_dsn) as retrieval:
        _scope(retrieval, TENANT_ID, foreign_matter)
        matter_denied = (
            retrieval.execute(
                "SELECT count(*) FROM sklegal_retrieval.projections"
            ).fetchone()[0]
            == 0
        )
    return {"cross_tenant": tenant_denied, "cross_matter": matter_denied}


def _rebuild_projection(core_dsn: str, retrieval_dsn: str) -> dict[str, Any]:
    with psycopg.connect(core_dsn, row_factory=dict_row) as core:
        _scope(core, TENANT_ID)
        event = core.execute(
            """SELECT sequence, idempotency_key, payload
               FROM sklegal_mvp.outbox ORDER BY sequence"""
        ).fetchone()
        core.execute(
            """UPDATE sklegal_mvp.projection_registry
               SET retrieval_watermark = 0, state = 'rebuilding'
               WHERE tenant_id = %s AND matter_id = %s""",
            (TENANT_ID, MATTER_ID),
        )
    payload = event["payload"]
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with psycopg.connect(retrieval_dsn, row_factory=dict_row) as retrieval:
        _scope(retrieval, TENANT_ID, MATTER_ID)
        retrieval.execute("DELETE FROM sklegal_retrieval.projections")
        for _ in range(2):
            retrieval.execute(
                """INSERT INTO sklegal_retrieval.projections
                       (tenant_id, matter_id, generation, outbox_sequence,
                        idempotency_key, query, search_payload, span_payload,
                        embedding)
                   VALUES (%s, %s, 1, %s, %s, %s, %s, %s, '[0.1,0.2,0.3]')
                   ON CONFLICT (idempotency_key) DO NOTHING""",
                (
                    TENANT_ID,
                    MATTER_ID,
                    event["sequence"],
                    event["idempotency_key"],
                    payload["query"],
                    Jsonb(payload["search"]),
                    Jsonb(payload["span"]),
                ),
            )
        row = retrieval.execute(
            """SELECT search_payload, span_payload, query
               FROM sklegal_retrieval.projections"""
        ).fetchone()
        count = retrieval.execute(
            "SELECT count(*) AS projection_count FROM sklegal_retrieval.projections"
        ).fetchone()["projection_count"]
    rebuilt = {
        "query": row["query"],
        "search": row["search_payload"],
        "span": row["span_payload"],
    }
    actual = hashlib.sha256(
        json.dumps(rebuilt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with psycopg.connect(core_dsn) as core:
        _scope(core, TENANT_ID)
        core.execute(
            """UPDATE sklegal_mvp.projection_registry
               SET retrieval_watermark = core_watermark, state = 'current'
               WHERE tenant_id = %s AND matter_id = %s""",
            (TENANT_ID, MATTER_ID),
        )
    return {
        "expected_sha256": expected,
        "actual_sha256": actual,
        "idempotent_rows": int(count),
        "pass": expected == actual and count == 1,
    }


def _backup_restore(project: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for service, database, tenant_setting in (
        ("core", "sklegal_core", f"SET sklegal.tenant_id = '{TENANT_ID}';"),
        (
            "retrieval",
            "sklegal_retrieval",
            f"SET sklegal.tenant_id = '{TENANT_ID}'; SET sklegal.matter_id = '{MATTER_ID}';",
        ),
    ):
        container = f"{project}-{service}-1"
        admin = f"sklegal_{service}_admin"
        scratch = f"sklegal_{service}_restore"
        dump = f"/tmp/{service}.dump"
        _run(
            [
                "docker",
                "exec",
                container,
                "pg_dump",
                "--format=custom",
                "--username",
                admin,
                "--dbname",
                database,
                "--file",
                dump,
            ]
        )
        digest = _run(["docker", "exec", container, "sha256sum", dump]).stdout.split()[
            0
        ]
        _run(["docker", "exec", container, "createdb", "--username", admin, scratch])
        try:
            _run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_restore",
                    "--no-owner",
                    "--username",
                    admin,
                    "--dbname",
                    scratch,
                    dump,
                ]
            )
            table = (
                "sklegal_mvp.records"
                if service == "core"
                else "sklegal_retrieval.projections"
            )
            query = f"{tenant_setting} SELECT count(*) FROM {table};"
            output = _run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "--tuples-only",
                    "--no-align",
                    "--username",
                    admin,
                    "--dbname",
                    scratch,
                    "--command",
                    query,
                ]
            ).stdout.splitlines()
            count = int(output[-1])
        finally:
            _run(["docker", "exec", container, "dropdb", "--username", admin, scratch])
        results[service] = {"sha256": digest, "restore_rows": count, "pass": count > 0}
    return results


def _reset(core_dsn: str, retrieval_dsn: str) -> None:
    with psycopg.connect(retrieval_dsn) as retrieval:
        _scope(retrieval, TENANT_ID, MATTER_ID)
        retrieval.execute("TRUNCATE sklegal_retrieval.projections")
    with psycopg.connect(core_dsn) as core:
        _scope(core, TENANT_ID)
        core.execute(
            """TRUNCATE sklegal_mvp.audit_events,
                      sklegal_mvp.browser_sessions,
                      sklegal_mvp.replay_reservations,
                      sklegal_mvp.revocations,
                      sklegal_mvp.projection_registry,
                      sklegal_mvp.outbox,
                      sklegal_mvp.records,
                      sklegal_mvp.matter_memberships,
                      sklegal_mvp.principals,
                      sklegal_mvp.policy_state
               RESTART IDENTITY"""
        )


def _http(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return (
                response.status,
                json.loads(raw) if raw else {},
                dict(response.headers),
            )
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else {}, dict(error.headers)


def _compose(env: dict[str, str], project: str, *arguments: str) -> None:
    _run(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "--file",
            str(COMPOSE),
            *arguments,
        ],
        env=env,
    )


def qualify(output: Path) -> dict[str, Any]:
    suffix = secrets.token_hex(4)
    project = f"sklegal-06a2686f-{suffix}"
    ports: set[int] = set()
    while len(ports) < 5:
        ports.add(_port())
    core_port, retrieval_port, api_port, web_port, debug_port = sorted(ports)
    core_admin = secrets.token_urlsafe(30)
    core_app = secrets.token_urlsafe(30)
    retrieval_admin = secrets.token_urlsafe(30)
    retrieval_app = secrets.token_urlsafe(30)
    runtime = Path(tempfile.mkdtemp(prefix="sklegal-06a2686f-"))
    gpg_home = runtime / "gnupg"
    issuer_policy = runtime / "issuer-policy.json"
    browser_output = runtime / "browser.json"
    api_log = (runtime / "api.log").open("w", encoding="utf-8")
    web_log = (runtime / "web.log").open("w", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "SKLEGAL_MVP_CORE_ADMIN_PASSWORD": core_admin,
            "SKLEGAL_MVP_CORE_APP_PASSWORD": core_app,
            "SKLEGAL_MVP_RETRIEVAL_ADMIN_PASSWORD": retrieval_admin,
            "SKLEGAL_MVP_RETRIEVAL_APP_PASSWORD": retrieval_app,
            "SKLEGAL_MVP_CORE_PORT": str(core_port),
            "SKLEGAL_MVP_RETRIEVAL_PORT": str(retrieval_port),
            "SKLEGAL_MVP_CORE_VOLUME": f"{project}-core-data",
            "SKLEGAL_MVP_RETRIEVAL_VOLUME": f"{project}-retrieval-data",
        }
    )
    core_admin_dsn = f"postgresql://sklegal_core_admin:{core_admin}@127.0.0.1:{core_port}/sklegal_core"
    core_app_dsn = (
        f"postgresql://sklegal_core_app:{core_app}@127.0.0.1:{core_port}/sklegal_core"
    )
    retrieval_admin_dsn = f"postgresql://sklegal_retrieval_admin:{retrieval_admin}@127.0.0.1:{retrieval_port}/sklegal_retrieval"
    retrieval_app_dsn = f"postgresql://sklegal_retrieval_app:{retrieval_app}@127.0.0.1:{retrieval_port}/sklegal_retrieval"
    api: subprocess.Popen[str] | None = None
    web: subprocess.Popen[str] | None = None
    started = time.monotonic()
    try:
        fingerprint = _gpg_identity(gpg_home)
        _issuer_policy(issuer_policy, fingerprint)
        _compose(env, project, "config", "--quiet")
        _compose(env, project, "up", "--detach", "--wait")
        _wait_postgres(core_admin_dsn)
        _wait_postgres(retrieval_admin_dsn)
        pins = _seed(core_admin_dsn, retrieval_admin_dsn)
        readback_before = _readback(core_admin_dsn, retrieval_admin_dsn)
        rls = _rls_denials(core_app_dsn, retrieval_app_dsn)
        if not all(rls.values()) or readback_before["pgvector"] != 1:
            raise QualificationError("RLS or pgvector gate failed")

        app_env = dict(env)
        app_env.update(
            {
                "GNUPGHOME": str(gpg_home),
                "SKLEGAL_MVP_GNUPGHOME": str(gpg_home),
                "SKLEGAL_MVP_ISSUER_FINGERPRINT": fingerprint,
                "SKLEGAL_MVP_ISSUER_POLICY": str(issuer_policy),
                "SKLEGAL_MVP_CORE_DSN": core_app_dsn,
                "SKLEGAL_MVP_RETRIEVAL_DSN": retrieval_app_dsn,
            }
        )
        api = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "sklegal_api.durable_public_synthetic:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=app_env,
            stdout=api_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait(f"http://127.0.0.1:{api_port}/healthz")
        build_env = dict(app_env)
        build_env.update(
            {
                "VITE_SKLEGAL_API_BASE": "/api",
                "VITE_SKLEGAL_PUBLIC_SYNTHETIC_PREVIEW": "1",
            }
        )
        _run(["npm", "run", "build", "--workspace", "@sklegal/web"], env=build_env)
        web = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/mvp_preview.py"),
                "serve-web",
                "--dist",
                str(ROOT / "apps/web/dist"),
                "--web-port",
                str(web_port),
                "--api-port",
                str(api_port),
            ],
            cwd=ROOT,
            env=app_env,
            stdout=web_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait(f"http://127.0.0.1:{web_port}/__preview/healthz")
        try:
            _run(
                [
                    "node",
                    str(CHROME_QUALIFICATION),
                    "--web-url",
                    f"http://127.0.0.1:{web_port}",
                    "--matter-id",
                    str(MATTER_ID),
                    "--debug-port",
                    str(debug_port),
                    "--output",
                    str(browser_output),
                ]
            )
        except QualificationError as error:
            api_log.flush()
            detail = (runtime / "api.log").read_text(encoding="utf-8")[-4000:]
            raise QualificationError(f"{error}\nAPI log:\n{detail}") from None

        status, session, session_headers = _http(
            f"http://127.0.0.1:{api_port}/v1/session/bootstrap",
            "POST",
            {
                "credentialReference": "development:public-synthetic:mvp",
                "tenantId": str(TENANT_ID),
            },
        )
        if status != 200:
            raise QualificationError("session bootstrap failed")
        set_cookie = next(
            value
            for key, value in session_headers.items()
            if key.lower() == "set-cookie"
        )
        cookie = set_cookie.split(";", 1)[0]
        request_headers = {"Cookie": cookie, "X-CSRF-Token": session["csrfToken"]}
        workspace_status, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=request_headers,
        )
        if workspace_status != 200:
            raise QualificationError("durable workspace request failed")
        cross_matter_status, _, _ = _http(
            "http://127.0.0.1:{}/v1/matters/{}/workspace".format(
                api_port, UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
            ),
            headers=request_headers,
        )
        cross_tenant_headers = {
            **request_headers,
            "X-SKLegal-Tenant": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }
        cross_tenant_status, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=cross_tenant_headers,
        )
        if cross_matter_status != 403 or cross_tenant_status != 403:
            raise QualificationError("API cross-scope authorization did not deny")

        _compose(env, project, "stop", "retrieval")
        safe_workspace, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=request_headers,
        )
        retrieval_outage, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/corpus/search",
            "POST",
            {"query": "invented delivery date"},
            request_headers,
        )
        _compose(env, project, "start", "retrieval")
        _wait(f"http://127.0.0.1:{api_port}/healthz")
        if safe_workspace != 200 or retrieval_outage != 503:
            raise QualificationError("retrieval outage behavior failed")

        _compose(env, project, "stop", "core")
        core_outage, _, _ = _http(f"http://127.0.0.1:{api_port}/healthz")
        _compose(env, project, "start", "core")
        _wait(f"http://127.0.0.1:{api_port}/healthz")
        post_core_restart, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=request_headers,
        )
        if core_outage != 503 or post_core_restart != 200:
            raise QualificationError("core outage or restart recovery failed")

        with psycopg.connect(core_admin_dsn) as core:
            _scope(core, TENANT_ID)
            core.execute(
                "UPDATE sklegal_mvp.policy_state SET stale = true WHERE tenant_id = %s",
                (TENANT_ID,),
            )
        stale_status, _, _ = _http(f"http://127.0.0.1:{api_port}/healthz")
        stale_workspace, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=request_headers,
        )
        with psycopg.connect(core_admin_dsn) as core:
            _scope(core, TENANT_ID)
            core.execute(
                "UPDATE sklegal_mvp.policy_state SET stale = false WHERE tenant_id = %s",
                (TENANT_ID,),
            )
        if stale_status != 503 or stale_workspace != 503:
            raise QualificationError("stale policy did not fail closed")

        with psycopg.connect(core_admin_dsn) as core:
            _scope(core, TENANT_ID)
            core.execute(
                """INSERT INTO sklegal_mvp.revocations
                       VALUES (%s, %s, now())""",
                (TENANT_ID, "a" * 64),
            )
            revoked = core.execute(
                "SELECT count(*) FROM sklegal_mvp.revocations"
            ).fetchone()[0]
        if revoked != 1:
            raise QualificationError("revocation persistence failed")
        os.environ.update(
            {
                key: value
                for key, value in app_env.items()
                if key.startswith("SKLEGAL_MVP_") or key == "GNUPGHOME"
            }
        )
        from sklegal_api.durable_public_synthetic import (
            DurableRevocationBackend,
            PostgresBoundary,
        )

        revocation_snapshot = DurableRevocationBackend(
            PostgresBoundary(core_app_dsn), TENANT_ID
        ).snapshot(("a" * 64, "b" * 64))
        if revocation_snapshot.revoked_credential_digests != frozenset({"a" * 64}):
            raise QualificationError("CapAuth revocation snapshot failed")

        with psycopg.connect(core_admin_dsn) as core:
            _scope(core, TENANT_ID)
            core.execute(
                """UPDATE sklegal_mvp.projection_registry
                   SET retrieval_watermark = 0, state = 'lagging'
                   WHERE tenant_id = %s AND matter_id = %s""",
                (TENANT_ID, MATTER_ID),
            )
        lag_safe_workspace, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=request_headers,
        )
        if lag_safe_workspace != 200:
            raise QualificationError("projection lag interrupted canonical workflow")
        projection_rebuild = _rebuild_projection(core_admin_dsn, retrieval_admin_dsn)
        if not projection_rebuild["pass"]:
            raise QualificationError("deterministic projection rebuild failed")
        backup_restore = _backup_restore(project)
        if not all(item["pass"] for item in backup_restore.values()):
            raise QualificationError("backup restore readback failed")
        signout_status, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/session",
            "DELETE",
            headers=request_headers,
        )
        revoked_session_status, _, _ = _http(
            f"http://127.0.0.1:{api_port}/v1/matters/{MATTER_ID}/workspace",
            headers=request_headers,
        )
        if signout_status != 204 or revoked_session_status != 401:
            raise QualificationError("revoked browser session did not fail closed")
        readback_after = _readback(core_admin_dsn, retrieval_admin_dsn)
        _reset(core_admin_dsn, retrieval_admin_dsn)
        reset_pins = _seed(core_admin_dsn, retrieval_admin_dsn)
        reset_readback = _readback(core_admin_dsn, retrieval_admin_dsn)
        if (
            reset_pins != pins
            or reset_readback["records"] != readback_before["records"]
        ):
            raise QualificationError("reset and reseed was not deterministic")

        browser = json.loads(browser_output.read_text(encoding="utf-8"))
        for process in (web, api):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        web = None
        api = None
        _compose(env, project, "down", "--volumes", "--remove-orphans")
        _compose(env, project, "up", "--detach", "--wait")
        _wait_postgres(core_admin_dsn)
        _wait_postgres(retrieval_admin_dsn)
        replay_pins = _seed(core_admin_dsn, retrieval_admin_dsn)
        replay_readback = _readback(core_admin_dsn, retrieval_admin_dsn)
        if replay_pins != pins or replay_readback != readback_before:
            raise QualificationError("fresh migration replay was not deterministic")

        result = {
            "status": "PASS",
            "card": "06a2686f",
            "duration_seconds": round(time.monotonic() - started, 3),
            "source_commit": _run(["git", "rev-parse", "HEAD"]).stdout.strip(),
            "source_tree": _run(["git", "rev-parse", "HEAD^{tree}"]).stdout.strip(),
            "pins": pins,
            "readback_before": readback_before,
            "readback_after": readback_after,
            "reset_reseed": {
                "pins_equal": reset_pins == pins,
                "readback": reset_readback,
            },
            "migration_replay": {
                "pins_equal": replay_pins == pins,
                "readback": replay_readback,
            },
            "rls": rls,
            "api_authorization": {
                "authorized_matter": workspace_status == 200,
                "cross_matter_denied": cross_matter_status == 403,
                "cross_tenant_denied": cross_tenant_status == 403,
                "revoked_session_denied": revoked_session_status == 401,
            },
            "browser": browser,
            "outages": {
                "retrieval_workspace_safe": True,
                "retrieval_failed_closed": True,
                "core_failed_closed": True,
                "core_restart_recovered": True,
                "stale_policy_failed_closed": True,
                "projection_lag_workspace_safe": True,
            },
            "revocation_rows": revoked,
            "revocation_revision": revocation_snapshot.revision,
            "projection_rebuild": projection_rebuild,
            "backup_restore": backup_restore,
            "topology": {
                "core_port_loopback": core_port,
                "retrieval_port_loopback": retrieval_port,
                "separate_admins": True,
                "separate_app_roles": True,
                "separate_volumes": True,
                "separate_networks": True,
            },
            "limitations": [
                "public-synthetic corpus only",
                "simulated connectors only",
                "optional AGE remains activation-gated",
            ],
            "rollback": "Stop the exact task project and remove only its two named volumes.",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return result
    finally:
        for process in (web, api):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        api_log.close()
        web_log.close()
        _compose(env, project, "down", "--volumes", "--remove-orphans")
        if output.exists():
            payload = json.loads(output.read_text(encoding="utf-8"))
            containers = _run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--filter",
                    f"name={project}",
                    "--format",
                    "{{.Names}}",
                ]
            ).stdout.strip()
            volumes = _run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "--filter",
                    f"name={project}",
                    "--format",
                    "{{.Name}}",
                ]
            ).stdout.strip()
            payload["safe_state"] = {
                "task_containers_absent": not containers,
                "task_volumes_absent": not volumes,
            }
            output.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        shutil.rmtree(runtime, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = qualify(args.output.resolve())
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}))
        return 1
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
