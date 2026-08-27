#!/usr/bin/env python3
"""Run the reversible SKL-MVP-COMPOSE-01 loopback qualification."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

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
from sklegal_api.features.joined_analysis.contract import (
    JoinedAnalysisSnapshotProjection,
)
from sklegal_api.features.joined_analysis.service import (
    projection_provenance_sha256,
    projection_sha256,
)
from sklegal_capauth import VERIFIER_POLICY_VERSION, Audience, Capability, PrincipalType
from sklegal_persistence.features.governed_corpus.models import (
    Classification,
    CorpusSourceVersion,
    ProjectionState,
    VerificationState,
)
from sklegal_persistence.features.governed_corpus.models import (
    canonical_sha256 as corpus_sha256,
)
from sklegal_persistence.features.joined_analysis.repository import (
    canonical_projection_sha256,
)
from sklegal_persistence.features.work_products.models import (
    WorkProductAggregate,
)
from sklegal_persistence.features.work_products.models import (
    canonical_sha256 as work_product_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/chiap01/compose.mvp.yml"
FIXTURE = ROOT / "tests/fixtures/mvp/public-synthetic-mvp-v1.json"
AGENT_FIXTURE = (
    ROOT / "tests/fixtures/mvp/fragments/agent_runs/public-synthetic-agent-run-v1.json"
)
ARTIFACT_FIXTURE = (
    ROOT
    / "tests/fixtures/mvp/fragments/artifact_intake/public-synthetic-artifact-intake.json"
)
JOINED_FIXTURE = (
    ROOT / "tests/fixtures/mvp/fragments/joined_analysis/public-synthetic-v1.json"
)
WORK_PRODUCT_FIXTURE = (
    ROOT
    / "tests/fixtures/mvp/fragments/work_products/public-synthetic-work-product-v1.json"
)
V2_MANIFEST = ROOT / "docs/contracts/v2-mvp/v2-surface-manifest.v1.json"
CHROME_QUALIFICATION = ROOT / "tests/qualification/session_reload_csp_qualification.mjs"
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
PRINCIPAL_ID = UUID("22222222-2222-4222-8222-222222222221")
MATTER_ID = UUID("44444444-4444-4444-8444-444444444441")
CORPUS_SOURCE_ID = "public-synthetic-authority-primary"
FEATURE_POLICY_REVISION = hashlib.sha256(
    b"sklegal-public-synthetic-policy-v1"
).hexdigest()
FEATURE_RIGHTS_REVISION = hashlib.sha256(
    b"sklegal-public-synthetic-rights-v1"
).hexdigest()
FEATURE_RELEASE_ID = "public-synthetic-release-v1"
FEATURE_PROJECTION_GENERATION = 1
FEATURE_CORE_WATERMARK = 1
WORK_PRODUCT_ID = UUID("50000000-0000-4000-8000-000000000001")
WORK_PRODUCT_VERSION_ID = UUID("50000000-0000-4000-8000-000000000002")


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


def _governed_corpus_records(
    fixture: dict[str, Any],
) -> tuple[ProjectionState, CorpusSourceVersion]:
    authority = fixture["authorities"][0]
    exact_span = "Invented fixture authority for the invented delivery date only."
    recorded_at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    projection = ProjectionState(
        release_id=FEATURE_RELEASE_ID,
        projection_generation=FEATURE_PROJECTION_GENERATION,
        backend_watermark=FEATURE_CORE_WATERMARK,
        core_watermark=FEATURE_CORE_WATERMARK,
        lag_events=0,
        lag_seconds=0,
        max_lag_events=0,
        max_lag_seconds=0,
    )
    source = CorpusSourceVersion(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        source_id=CORPUS_SOURCE_ID,
        source_version_id=uuid5(NAMESPACE_URL, f"{MATTER_ID}:{CORPUS_SOURCE_ID}"),
        source_version=authority["sourceVersion"],
        release_id=FEATURE_RELEASE_ID,
        projection_generation=FEATURE_PROJECTION_GENERATION,
        title=authority["title"],
        citation=authority["citation"],
        source_role="official_authority",
        classification=Classification.PUBLIC,
        rights_revision=FEATURE_RIGHTS_REVISION,
        permitted_principal_ids=frozenset({PRINCIPAL_ID}),
        source_sha256=authority["contentSha256"],
        document_id="public-synthetic-authority-document",
        chunk_id="public-synthetic-authority-chunk-1",
        chunk_sha256=hashlib.sha256(exact_span.encode()).hexdigest(),
        locator={"kind": "character", "start": 0, "end": len(exact_span)},
        exact_span=exact_span,
        embedding=(0.1, 0.2, 0.3),
        verification_state=VerificationState.OFFICIAL_AUTHORITY_VERIFIED,
        jurisdiction=authority["jurisdiction"],
        recorded_at=recorded_at,
        recorded_by_principal_id=PRINCIPAL_ID,
        authorization_decision_id=uuid5(
            NAMESPACE_URL, f"{MATTER_ID}:corpus-authorization"
        ),
        policy_decision_id=uuid5(NAMESPACE_URL, f"{MATTER_ID}:corpus-policy"),
        policy_revision=FEATURE_POLICY_REVISION,
    )
    return projection, source


def _replace_fixture_scope(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_fixture_scope(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_fixture_scope(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def _joined_analysis_seed() -> tuple[dict[str, Any], str]:
    projection = json.loads(JOINED_FIXTURE.read_text(encoding="utf-8"))
    projection = _replace_fixture_scope(
        projection,
        {
            projection["tenant_id"]: str(TENANT_ID),
            projection["matter_id"]: str(MATTER_ID),
        },
    )
    typed = JoinedAnalysisSnapshotProjection.model_validate(projection)
    projection["snapshot"].update(projection_provenance_sha256(typed))
    typed = JoinedAnalysisSnapshotProjection.model_validate(projection)
    digest = projection_sha256(typed)
    projection["snapshot"]["projection_sha256"] = digest
    if canonical_projection_sha256(projection) != digest:
        raise QualificationError("joined analysis canonical digest drifted")
    return projection, digest


def _work_product_seed() -> WorkProductAggregate:
    payload = json.loads(WORK_PRODUCT_FIXTURE.read_text(encoding="utf-8"))
    payload = _replace_fixture_scope(
        payload,
        {
            payload["tenantId"]: str(TENANT_ID),
            payload["matterId"]: str(MATTER_ID),
            "30000000-0000-4000-8000-000000000001": str(PRINCIPAL_ID),
            "30000000-0000-4000-8000-000000000002": str(PRINCIPAL_ID),
        },
    )
    now = datetime.now(UTC)
    payload["status"] = "validated"
    payload["updatedAt"] = now.isoformat()
    approval = payload["approvals"][0]
    approval.update(
        {
            "approvalId": payload["currentVersionId"],
            "status": "pending",
            "reviewerPrincipalId": None,
            "decidedAt": None,
            "rationale": None,
            "decisionPolicyRevision": None,
            "decisionAuthorization": None,
            "revokerPrincipalId": None,
            "revokedAt": None,
            "revocationRationale": None,
            "revocationAuthorization": None,
            "supersededAt": None,
            "supersededByApprovalId": None,
            "supersedingVersionId": None,
            "requestedAt": (now - timedelta(minutes=1)).isoformat(),
        }
    )
    payload["validations"][0]["validatedAt"] = (now - timedelta(minutes=2)).isoformat()
    for authorization in (
        payload["validations"][0]["authorization"],
        approval["requestAuthorization"],
    ):
        authorization["authorizedAt"] = (now - timedelta(minutes=5)).isoformat()
        authorization["expiresAt"] = (now + timedelta(hours=1)).isoformat()
    return WorkProductAggregate.model_validate(payload)


def _seed_activity(core_admin_dsn: str, core_app_dsn: str) -> dict[str, Any]:
    source_id = UUID("82000000-0000-4000-8000-000000000001")
    event_id = UUID("81000000-0000-4000-8000-000000000001")
    projection_event_id = UUID("88000000-0000-4000-8000-000000000001")
    source_sha256 = "a" * 64
    occurred_at = datetime.now(UTC) - timedelta(seconds=2)
    with psycopg.connect(core_admin_dsn) as core:
        core.execute(
            """GRANT EXECUTE ON FUNCTION sklegal_audit.append_event(
                   uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text,
                   text, text, uuid, uuid, uuid, text, text, timestamptz, jsonb
               ) TO sklegal_core_app"""
        )
    with psycopg.connect(core_app_dsn, row_factory=dict_row) as core:
        _scope(core, TENANT_ID, MATTER_ID)
        event = core.execute(
            """SELECT sklegal_audit.append_event(
                   %s, %s, %s, %s, %s, %s, %s, %s, '01', 'api',
                   'matter_activity.source.recorded', 'matter_event', %s,
                   %s, %s, 'success', 'recorded', %s,
                   %s::jsonb
               ) AS event""",
            (
                event_id,
                TENANT_ID,
                MATTER_ID,
                PRINCIPAL_ID,
                UUID("83000000-0000-4000-8000-000000000001"),
                UUID("84000000-0000-4000-8000-000000000001"),
                "85000000000040008000000000000001",
                "8500000000004000",
                source_id,
                UUID("86000000-0000-4000-8000-000000000001"),
                UUID("87000000-0000-4000-8000-000000000001"),
                occurred_at,
                Jsonb(
                    {
                        "operation": "read",
                        "resource_version": 1,
                        "resource_sha256": source_sha256,
                    }
                ),
            ),
        ).fetchone()["event"]
        projected_at = datetime.now(UTC)
        projected = core.execute(
            """SELECT sklegal_activity.project_event(
                   %s, %s, %s, %s, %s, 'matter_event', %s, 1, %s,
                   'recorded', %s, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                   %s, %s, %s, %s
               ) AS projected""",
            (
                TENANT_ID,
                MATTER_ID,
                event_id,
                int(event["event_sequence"]),
                event["event_sha256"],
                source_id,
                source_sha256,
                occurred_at,
                "b" * 64,
                "c" * 64,
                projection_event_id,
                projected_at,
            ),
        ).fetchone()["projected"]
    if projected is not True:
        raise QualificationError("Matter activity seed did not project")
    return {
        "first_event_sequence": int(event["event_sequence"]),
        "projected_sequence": int(event["event_sequence"]),
        "projected_sha256": str(event["event_sha256"]),
    }


def _seed(core_dsn: str, core_app_dsn: str, retrieval_dsn: str) -> dict[str, str]:
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    artifact_governance = json.loads(ARTIFACT_FIXTURE.read_text(encoding="utf-8"))[
        "governance"
    ]
    search, span = _corpus_payloads(fixture)
    feature_projection, feature_source = _governed_corpus_records(fixture)
    feature_source_json = feature_source.model_dump(mode="json", by_alias=True)
    feature_projection_json = feature_projection.model_dump(mode="json", by_alias=True)
    joined_projection, joined_sha256 = _joined_analysis_seed()
    work_product = _work_product_seed()
    work_product_json = work_product.model_dump(mode="json", by_alias=True)
    client_id = uuid5(NAMESPACE_URL, f"{TENANT_ID}:public-synthetic-client")
    engagement_id = uuid5(NAMESPACE_URL, f"{TENANT_ID}:public-synthetic-engagement")
    principal_revision = hashlib.sha256(
        f"{TENANT_ID}:{PRINCIPAL_ID}:active".encode()
    ).hexdigest()
    outbox_payload = {"query": "invented delivery date", "search": search, "span": span}
    with psycopg.connect(core_dsn) as core:
        _scope(core, TENANT_ID)
        core.execute(
            """INSERT INTO sklegal_identity.tenants
                   (id, tenant_id, slug, name, status, classification)
               VALUES (%s, %s, 'public-synthetic', 'Public Synthetic Tenant',
                       'active', 'public')""",
            (TENANT_ID, TENANT_ID),
        )
        core.execute(
            """INSERT INTO sklegal_identity.principals
                   (id, tenant_id, principal_kind, display_name, status,
                    classification)
               VALUES (%s, %s, 'human', 'Public Synthetic Reviewer', 'active',
                       'public')""",
            (PRINCIPAL_ID, TENANT_ID),
        )
        core.execute(
            """INSERT INTO sklegal_identity.database_role_bindings
                   (database_role, tenant_id, principal_id)
               VALUES ('sklegal_core_app', %s, %s)""",
            (TENANT_ID, PRINCIPAL_ID),
        )
        core.execute(
            """INSERT INTO sklegal_identity.tenant_memberships
                   (tenant_id, principal_id, membership_role)
               VALUES (%s, %s, 'reviewer')""",
            (TENANT_ID, PRINCIPAL_ID),
        )
        core.execute(
            """INSERT INTO sklegal_legal.clients
                   (id, tenant_id, display_name, client_kind, status,
                    classification)
               VALUES (%s, %s, 'Synthetic Client', 'company', 'active',
                       'public')""",
            (client_id, TENANT_ID),
        )
        core.execute(
            """INSERT INTO sklegal_legal.engagements
                   (id, tenant_id, client_id, title, scope, status, valid_from,
                    classification)
               VALUES (%s, %s, %s, 'Synthetic Engagement', 'Synthetic only',
                       'active', '2026-08-27T00:00:00Z', 'public')""",
            (engagement_id, TENANT_ID, client_id),
        )
        core.execute(
            """INSERT INTO sklegal_legal.matters
                   (id, tenant_id, matter_id, client_id, engagement_id, title,
                    summary, status, opened_at, classification)
               VALUES (%s, %s, %s, %s, %s, 'Synthetic Matter',
                       'Public synthetic qualification only.', 'open',
                       '2026-08-27T00:00:00Z', 'public')""",
            (MATTER_ID, TENANT_ID, MATTER_ID, client_id, engagement_id),
        )
        core.execute(
            """INSERT INTO sklegal_legal.matter_memberships
                   (tenant_id, matter_id, principal_id, membership_role)
               VALUES (%s, %s, %s, 'reviewer')""",
            (TENANT_ID, MATTER_ID, PRINCIPAL_ID),
        )
        core.execute(
            """INSERT INTO sklegal_legal.retention_policies
                   (id, tenant_id, matter_id, retain_for_days, effective_from,
                    decided_by_principal_id)
               VALUES (%s, %s, %s, 3650, '2026-08-01T00:00:00Z', %s)""",
            (
                UUID(artifact_governance["retention_policy_id"]),
                TENANT_ID,
                MATTER_ID,
                PRINCIPAL_ID,
            ),
        )
        for legal_hold_id in artifact_governance["legal_hold_ids"]:
            core.execute(
                """INSERT INTO sklegal_legal.legal_holds
                       (id, tenant_id, matter_id, status, hold_scope,
                        issued_by_principal_id, effective_from)
                   VALUES (%s, %s, %s, 'active', 'matter', %s,
                           '2026-08-01T00:00:00Z')""",
                (UUID(legal_hold_id), TENANT_ID, MATTER_ID, PRINCIPAL_ID),
            )
        snapshot = joined_projection["snapshot"]
        core.execute(
            """INSERT INTO sklegal_legal.joined_analysis_snapshots
                   (tenant_id, matter_id, snapshot_id, version, observed_at,
                    matter_snapshot_sha256, claim_projection_revision,
                    authority_snapshot, projection_revision, projection_sha256,
                    projection)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                UUID(snapshot["snapshot_id"]),
                snapshot["version"],
                snapshot["observed_at"],
                snapshot["matter_snapshot_sha256"],
                snapshot["claim_projection_revision"],
                snapshot["authority_snapshot"],
                snapshot["projection_revision"],
                joined_sha256,
                Jsonb(joined_projection),
            ),
        )
        core.execute(
            """INSERT INTO sklegal_legal.work_product_feature_identities
                   (tenant_id, matter_id, work_product_id,
                    current_aggregate_version, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                work_product.work_product_id,
                work_product.aggregate_version,
                work_product.created_at,
                work_product.updated_at,
            ),
        )
        core.execute(
            """INSERT INTO sklegal_legal.work_product_feature_versions
                   (tenant_id, matter_id, work_product_id, aggregate_version,
                    current_version_id, current_version_number,
                    current_content_sha256, status, aggregate_sha256,
                    aggregate_payload, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                work_product.work_product_id,
                work_product.aggregate_version,
                work_product.current_version_id,
                work_product.current_version.version_number,
                work_product.current_version.content_sha256,
                work_product.status.value,
                work_product_sha256(work_product),
                Jsonb(work_product_json),
                work_product.updated_at,
            ),
        )
        core.execute(
            """INSERT INTO sklegal_governed_corpus.projection_registry
                   (tenant_id, matter_id, projection_generation, release_id,
                    core_watermark, policy_revision, rights_revision, record,
                    recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                FEATURE_PROJECTION_GENERATION,
                FEATURE_RELEASE_ID,
                FEATURE_CORE_WATERMARK,
                FEATURE_POLICY_REVISION,
                FEATURE_RIGHTS_REVISION,
                Jsonb(feature_projection_json),
                feature_source.recorded_at,
            ),
        )
        core.execute(
            """INSERT INTO sklegal_governed_corpus.source_versions
                   (tenant_id, matter_id, source_id, source_version_id,
                    source_version, release_id, projection_generation,
                    classification, rights_revision, permitted_principal_ids,
                    source_sha256, chunk_sha256, exact_span,
                    supersedes_source_version_id, record, record_sha256,
                    authorization_decision_id, recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                feature_source.source_id,
                feature_source.source_version_id,
                feature_source.source_version,
                feature_source.release_id,
                feature_source.projection_generation,
                int(feature_source.classification),
                feature_source.rights_revision,
                list(feature_source.permitted_principal_ids),
                feature_source.source_sha256,
                feature_source.chunk_sha256,
                feature_source.exact_span,
                feature_source.supersedes_source_version_id,
                Jsonb(feature_source_json),
                corpus_sha256(feature_source),
                feature_source.authorization_decision_id,
                feature_source.recorded_at,
            ),
        )
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
            "INSERT INTO sklegal_mvp.policy_state VALUES (%s, %s, false, 0)",
            (TENANT_ID, FEATURE_POLICY_REVISION),
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
            """INSERT INTO sklegal_governed_corpus.projection_state
                   (tenant_id, matter_id, projection_generation, release_id,
                    backend_watermark, core_watermark, record, recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                FEATURE_PROJECTION_GENERATION,
                FEATURE_RELEASE_ID,
                FEATURE_CORE_WATERMARK,
                FEATURE_CORE_WATERMARK,
                Jsonb(feature_projection_json),
                feature_source.recorded_at,
            ),
        )
        retrieval.execute(
            """INSERT INTO sklegal_governed_corpus.source_projections
                   (tenant_id, matter_id, source_id, source_version_id,
                    source_version, release_id, projection_generation,
                    classification, rights_revision, permitted_principal_ids,
                    source_sha256, chunk_sha256, exact_span, search_document,
                    embedding, supersedes_source_version_id, record,
                    record_sha256, recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, to_tsvector('english', %s), %s::public.vector, %s,
                       %s, %s, %s)""",
            (
                TENANT_ID,
                MATTER_ID,
                feature_source.source_id,
                feature_source.source_version_id,
                feature_source.source_version,
                feature_source.release_id,
                feature_source.projection_generation,
                int(feature_source.classification),
                feature_source.rights_revision,
                list(feature_source.permitted_principal_ids),
                feature_source.source_sha256,
                feature_source.chunk_sha256,
                feature_source.exact_span,
                f"{feature_source.title} {feature_source.exact_span}",
                json.dumps(feature_source.embedding),
                feature_source.supersedes_source_version_id,
                Jsonb(feature_source_json),
                corpus_sha256(feature_source),
                feature_source.recorded_at,
            ),
        )
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
    _seed_activity(core_dsn, core_app_dsn)
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


def _feature_readback(core_dsn: str) -> dict[str, int]:
    with psycopg.connect(core_dsn, row_factory=dict_row) as core:
        _scope(core, TENANT_ID, MATTER_ID)
        row = core.execute(
            """SELECT
              (SELECT count(*) FROM sklegal_mvp.audit_events) AS authorization_audit,
              (SELECT count(*) FROM sklegal_workflow.agent_run_idempotency) AS agent_idempotency,
              (SELECT count(*) FROM sklegal_workflow.agent_run_audit_events) AS agent_audit,
              (SELECT count(*) FROM sklegal_artifact.artifact_idempotency_receipts) AS artifact_idempotency,
              (SELECT count(*) FROM sklegal_artifact.artifact_audit_facts) AS artifact_audit,
              (SELECT count(*) FROM sklegal_artifact.artifact_outbox) AS artifact_outbox,
              (SELECT count(*) FROM sklegal_legal.work_product_feature_idempotency) AS work_product_idempotency,
              (SELECT count(*) FROM sklegal_audit.work_product_feature_events) AS work_product_audit,
              (SELECT count(*) FROM sklegal_audit.work_product_feature_outbox) AS work_product_outbox,
              (SELECT count(*) FROM sklegal_task_deadline.idempotency_receipts) AS task_idempotency,
              (SELECT count(*) FROM sklegal_task_deadline.audit_events) AS task_audit,
              (SELECT count(*) FROM sklegal_task_deadline.outbox) AS task_outbox,
              (SELECT count(*) FROM sklegal_activity.export_proposals) AS activity_exports,
              (SELECT count(*) FROM sklegal_activity.projection_receipts) AS activity_idempotency,
              (SELECT count(*) FROM sklegal_audit.outbox) AS activity_outbox"""
        ).fetchone()
    counts = {key: int(value) for key, value in row.items()}
    if not all(value > 0 for value in counts.values()):
        raise QualificationError(f"durable feature evidence is incomplete: {counts}")
    return counts


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
        retrieval.execute(
            """TRUNCATE sklegal_governed_corpus.projection_commands,
                      sklegal_governed_corpus.source_projections,
                      sklegal_governed_corpus.projection_state,
                      sklegal_retrieval.projections"""
        )
    with psycopg.connect(core_dsn) as core:
        _scope(core, TENANT_ID)
        core.execute("TRUNCATE sklegal_identity.tenants CASCADE")
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
                      sklegal_mvp.policy_state,
                      sklegal_mvp.artifact_objects
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


def _v2_openapi_inventory(api_port: int, headers: dict[str, str]) -> dict[str, Any]:
    status, schema, _ = _http(
        f"http://127.0.0.1:{api_port}/openapi.json", headers=headers
    )
    if status != 200:
        raise QualificationError(
            f"OpenAPI inventory is unavailable: status={status} body={schema}"
        )
    manifest = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))
    expected = {
        (item["method"], item["path"], item["operation_id"])
        for item in manifest["operations"]
    }
    actual = [
        (method.upper(), path, operation["operationId"])
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        if method.lower() in {"delete", "get", "patch", "post", "put"}
    ]
    exact = expected.intersection(actual)
    operation_ids = [item[2] for item in actual]
    if len(exact) != 21 or len(operation_ids) != len(set(operation_ids)):
        raise QualificationError("frozen V2 OpenAPI inventory mismatch")
    return {
        "required": len(expected),
        "exact_matches": len(exact),
        "operation_ids_unique": True,
        "openapi_sha256": hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "manifest_sha256": hashlib.sha256(V2_MANIFEST.read_bytes()).hexdigest(),
    }


def _execute_v2_operations(api_port: int, headers: dict[str, str]) -> dict[str, Any]:
    base = f"http://127.0.0.1:{api_port}"
    matter = f"/v1/matters/{MATTER_ID}"
    results: dict[str, int] = {}
    replayed: list[str] = []
    replay_conflicts: list[str] = []

    def invoke(
        operation_id: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency: bool = False,
        decision_bound_replay: bool = False,
    ) -> dict[str, Any]:
        request_headers = dict(headers)
        if idempotency:
            request_headers["Idempotency-Key"] = str(
                uuid5(NAMESPACE_URL, f"c0bc2c68:{operation_id}")
            )
        status, payload, _ = _http(f"{base}{path}", method, body, request_headers)
        if status not in {200, 201}:
            raise QualificationError(
                f"V2 operation {operation_id} failed: status={status} body={payload}"
            )
        results[operation_id] = status
        if idempotency:
            replay_status, replay_payload, _ = _http(
                f"{base}{path}", method, body, request_headers
            )
            if decision_bound_replay:
                detail = replay_payload.get("detail", {})
                if replay_status != 409 or detail.get("code") != "idempotency_conflict":
                    raise QualificationError(
                        f"V2 operation {operation_id} did not protect its "
                        "decision-bound idempotency receipt"
                    )
                replay_conflicts.append(operation_id)
                return payload
            expected_replay = dict(payload)
            if "replayed" in expected_replay:
                expected_replay["replayed"] = True
            if replay_status != status or replay_payload != expected_replay:
                raise QualificationError(
                    f"V2 operation {operation_id} idempotency replay drifted: "
                    f"status={replay_status} body={replay_payload}"
                )
            replayed.append(operation_id)
        return payload

    invoke("get_workspace", "GET", f"{matter}/workspace")
    invoke("get_claim_ledger", "GET", f"{matter}/claims")
    invoke("get_joined_analysis", "GET", f"{matter}/analysis")

    agent_fixture = json.loads(AGENT_FIXTURE.read_text(encoding="utf-8"))
    analysis = dict(agent_fixture["request"])
    for field in (
        "tenantId",
        "matterId",
        "principalId",
        "requestId",
        "idempotencyKey",
    ):
        analysis.pop(field)
    analysis["schemaVersion"] = "sklegal.agent-analysis-command/v1"
    run = invoke(
        "create_analysis_run",
        "POST",
        f"{matter}/agent-runs",
        analysis,
        idempotency=True,
    )
    run_id = run["runId"]
    recommendation = run["recommendations"][0]
    invoke("get_agent_run", "GET", f"{matter}/agent-runs/{run_id}")
    challenged = invoke(
        "create_challenge",
        "POST",
        f"{matter}/claims/{run_id}/challenges",
        {
            "schemaVersion": "sklegal.agent-blind-challenge-command/v1",
            "expectedRunVersion": run["version"],
            "recommendationId": recommendation["recommendationId"],
            "recommendationVersion": recommendation["version"],
            "challengerSpecId": "sklegal.public-independent-challenger",
            "challengerSpecVersion": 1,
            "challengerSpecSha256": "a1" * 32,
            "requestedLogicalRouteId": "sklegal.public-independent-challenge",
        },
        idempotency=True,
    )
    invoke("list_recommendations", "GET", f"{matter}/recommendations")
    invoke(
        "decide_recommendation",
        "POST",
        f"{matter}/recommendations/{run_id}/decisions",
        {
            "schemaVersion": "sklegal.agent-human-disposition-command/v1",
            "expectedRunVersion": challenged["version"],
            "recommendationId": recommendation["recommendationId"],
            "recommendationVersion": recommendation["version"],
            "decision": "accept_as_proposed_task",
            "rationale": "Public synthetic recommendation reviewed.",
            "policyRevision": VERIFIER_POLICY_VERSION,
        },
        idempotency=True,
    )

    artifact_fixture = json.loads(ARTIFACT_FIXTURE.read_text(encoding="utf-8"))
    content = artifact_fixture["original"]["content_utf8"].encode()
    artifact = invoke(
        "create_artifact_intake",
        "POST",
        f"{matter}/artifacts",
        {
            "source": artifact_fixture["source"],
            "original": {
                "filename": artifact_fixture["original"]["filename"],
                "mediaType": artifact_fixture["original"]["media_type"],
                "byteCount": len(content),
                "contentSha256": hashlib.sha256(content).hexdigest(),
                "contentBase64": base64.b64encode(content).decode(),
            },
            "acquisitionMethod": "synthetic_adapter",
            "classification": "public",
            "privilegeState": "not_privileged",
            "retentionPolicyId": artifact_fixture["governance"]["retention_policy_id"],
            "legalHoldIds": artifact_fixture["governance"]["legal_hold_ids"],
            "ethicalWallIds": [],
            "requestedDerivations": [],
            "proposedLinks": [],
        },
        idempotency=True,
    )
    artifact_id = artifact["artifact"]["artifactId"]
    invoke("get_artifact", "GET", f"{matter}/artifacts/{artifact_id}")

    current = invoke(
        "get_work_product",
        "GET",
        f"{matter}/work-products/{WORK_PRODUCT_ID}",
    )
    binding = {
        "workProductVersionId": current["currentVersionId"],
        "versionNumber": current["versions"][-1]["versionNumber"],
        "contentSha256": current["versions"][-1]["contentSha256"],
    }
    approved = invoke(
        "decide_approval",
        "POST",
        f"{matter}/work-products/{WORK_PRODUCT_ID}/versions/"
        f"{WORK_PRODUCT_VERSION_ID}/approval-decisions",
        {
            "schemaVersion": "sklegal.approval-decision-command/v1",
            "expectedAggregateVersion": current["aggregateVersion"],
            "binding": binding,
            "decision": "approved",
            "rationale": "Exact public synthetic version reviewed.",
        },
        idempotency=True,
    )
    approved_model = WorkProductAggregate.model_validate(approved)
    approval = next(
        item for item in approved_model.approvals if item.status.value == "approved"
    )

    task = invoke(
        "upsert_task",
        "POST",
        f"{matter}/tasks",
        {
            "schemaVersion": "sklegal.task-command/v1",
            "expectedVersion": 0,
            "title": "Review public synthetic Work Product",
            "description": "Qualification Task with no external effect.",
            "assignedPrincipalId": str(PRINCIPAL_ID),
        },
        idempotency=True,
    )
    deadline_fixture = json.loads(
        (
            ROOT
            / "tests/fixtures/mvp/fragments/task_deadlines/public-synthetic-task-deadlines-v1.json"
        ).read_text(encoding="utf-8")
    )["deadlines"][0]
    invoke(
        "compute_deadline",
        "POST",
        f"{matter}/deadlines",
        {
            "schemaVersion": "sklegal.deadline-command/v1",
            "expectedVersion": 0,
            "title": "Public synthetic qualification Deadline",
            "trigger": deadline_fixture["trigger"],
            "rule": deadline_fixture["rule"],
            "calendar": deadline_fixture["calendar"],
            "reminderOffsetsDays": [1],
        },
        idempotency=True,
    )
    invoke(
        "create_simulation_handoff",
        "POST",
        f"{matter}/action-simulations",
        {
            "schemaVersion": "sklegal.action-simulation-command/v1",
            "taskId": task["task"]["taskId"],
            "deadlineId": None,
            "workProductId": str(approved_model.work_product_id),
            "workProductVersionId": str(approved_model.current_version_id),
            "workProductVersionNumber": approved_model.current_version.version_number,
            "workProductContentSha256": approved_model.current_version.content_sha256,
            "approvalId": str(approval.approval_id),
            "approvalSnapshotSha256": work_product_sha256(approval),
            "approvalCurrent": True,
            "destinationSha256": "d" * 64,
            "actionKind": "email",
            "simulationOnly": True,
        },
        idempotency=True,
    )

    new_content = "The repaired public synthetic Work Product remains local."
    versioned = invoke(
        "create_work_product_version",
        "POST",
        f"{matter}/work-products/{WORK_PRODUCT_ID}/versions",
        {
            "schemaVersion": "sklegal.work-product-version-command/v1",
            "expectedAggregateVersion": approved["aggregateVersion"],
            "expectedCurrent": binding,
            "content": new_content,
            "contentSha256": hashlib.sha256(new_content.encode()).hexdigest(),
            "source": approved["versions"][-1]["source"],
        },
        idempotency=True,
    )
    new_version = versioned["versions"][-1]
    invoke(
        "validate_work_product",
        "POST",
        f"{matter}/work-products/{WORK_PRODUCT_ID}/versions/"
        f"{new_version['versionId']}/validations",
        {
            "schemaVersion": "sklegal.work-product-validation-command/v1",
            "expectedAggregateVersion": versioned["aggregateVersion"],
            "binding": {
                "workProductVersionId": new_version["versionId"],
                "versionNumber": new_version["versionNumber"],
                "contentSha256": new_version["contentSha256"],
            },
            "checkIds": ["content_hash", "sentence_grounding", "source_lineage"],
            "rationale": "Deterministic qualification validation.",
        },
        idempotency=True,
    )

    activity = invoke("list_activity", "GET", f"{matter}/activity")
    invoke(
        "create_activity_export",
        "POST",
        f"{matter}/activity-exports",
        {
            "title": "Public synthetic qualification activity",
            "firstEventSequence": 1,
            "lastEventSequence": 1,
            "expectedProjectedSequence": activity["snapshotSequence"],
            "expectedProjectedSha256": activity["snapshotSha256"],
            "expectedResourceVersion": activity["snapshotSequence"],
        },
        idempotency=True,
        decision_bound_replay=True,
    )
    invoke(
        "search_corpus",
        "POST",
        f"{matter}/corpus/search",
        {
            "query": "invented delivery date",
            "expectedReleaseId": FEATURE_RELEASE_ID,
            "expectedProjectionGeneration": FEATURE_PROJECTION_GENERATION,
            "requiredCoreWatermark": FEATURE_CORE_WATERMARK,
        },
    )
    invoke(
        "get_corpus_span",
        "GET",
        f"{matter}/corpus/sources/{CORPUS_SOURCE_ID}/span"
        f"?expectedReleaseId={FEATURE_RELEASE_ID}"
        f"&expectedProjectionGeneration={FEATURE_PROJECTION_GENERATION}"
        f"&requiredCoreWatermark={FEATURE_CORE_WATERMARK}",
    )

    expected = {
        item["operation_id"]
        for item in json.loads(V2_MANIFEST.read_text())["operations"]
    }
    if set(results) != expected:
        raise QualificationError("not every frozen V2 operation executed")
    return {
        "operations": results,
        "all_21_succeeded": len(results) == 21,
        "idempotency_replays": sorted(replayed),
        "decision_bound_replay_conflicts": sorted(replay_conflicts),
    }


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
    project = f"sklegal-c0bc2c68-{suffix}"
    ports: set[int] = set()
    while len(ports) < 5:
        ports.add(_port())
    core_port, retrieval_port, api_port, web_port, debug_port = sorted(ports)
    core_admin = secrets.token_urlsafe(30)
    core_app = secrets.token_urlsafe(30)
    retrieval_admin = secrets.token_urlsafe(30)
    retrieval_app = secrets.token_urlsafe(30)
    runtime = Path(tempfile.mkdtemp(prefix="sklegal-c0bc2c68-"))
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
        pins = _seed(core_admin_dsn, core_app_dsn, retrieval_admin_dsn)
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
                sys.executable,
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
                sys.executable,
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
        v2_openapi = _v2_openapi_inventory(api_port, request_headers)
        try:
            v2_operations = _execute_v2_operations(api_port, request_headers)
        except QualificationError as error:
            api_log.flush()
            detail = (runtime / "api.log").read_text(encoding="utf-8")[-6000:]
            raise QualificationError(f"{error}\nAPI log:\n{detail}") from None
        feature_readback = _feature_readback(core_admin_dsn)
        workspace_status = v2_operations["operations"]["get_workspace"]
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
            {
                "query": "invented delivery date",
                "expectedReleaseId": FEATURE_RELEASE_ID,
                "expectedProjectionGeneration": FEATURE_PROJECTION_GENERATION,
                "requiredCoreWatermark": FEATURE_CORE_WATERMARK,
            },
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
                """INSERT INTO sklegal_identity.capability_revocations
                       (tenant_id, credential_digest, revoked_by, rationale)
                   VALUES (%s, %s, %s, %s)""",
                (TENANT_ID, "a" * 64, PRINCIPAL_ID, "qualification revocation"),
            )
            revoked = core.execute(
                "SELECT count(*) FROM sklegal_identity.capability_revocations"
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
        reset_pins = _seed(core_admin_dsn, core_app_dsn, retrieval_admin_dsn)
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
        replay_pins = _seed(core_admin_dsn, core_app_dsn, retrieval_admin_dsn)
        replay_readback = _readback(core_admin_dsn, retrieval_admin_dsn)
        if replay_pins != pins or replay_readback != readback_before:
            raise QualificationError("fresh migration replay was not deterministic")

        result = {
            "status": "PASS",
            "card": "c0bc2c68",
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
            "v2_openapi": v2_openapi,
            "v2_operations": v2_operations,
            "feature_readback": feature_readback,
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
        for cleanup_process in (web, api):
            if cleanup_process is not None and cleanup_process.poll() is None:
                cleanup_process.terminate()
                try:
                    cleanup_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    cleanup_process.kill()
                    cleanup_process.wait(timeout=5)
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
