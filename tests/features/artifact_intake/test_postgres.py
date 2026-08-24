from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.artifact_intake.postgres_store import (
    PostgresArtifactStore,
)
from sklegal_api.features.artifact_intake.router import build_artifact_intake_router
from sklegal_api.features.artifact_intake.service import (
    ArtifactIntakeService,
    StaticArtifactPolicy,
)
from sklegal_api.features.artifact_intake.store import ArtifactStore
from sklegal_api.features.artifact_intake.synthetic import SyntheticArtifactAdapter
from sklegal_capauth import (
    BoundaryScope,
    Capability,
    PrincipalContext,
    PrincipalType,
    Purpose,
)
from sklegal_persistence.features.artifact_intake.repository import (
    ArtifactCorrectionBatch,
    ArtifactMutationContext,
    ArtifactPersistenceBatch,
    ArtifactPersistencePrecondition,
    ArtifactReadContext,
    ArtifactReviewBatch,
    ArtifactSupersessionBatch,
    DerivedArtifactRow,
    PostgresArtifactRepository,
)

from tests.features.artifact_intake.factories import command
from tests.features.artifact_intake.test_persistence import batch as unit_batch
from tests.integration import persistence_contract_support
from tests.integration.persistence_contract_support import (
    CAPAUTH_RUNTIME_PRINCIPAL,
    CAPAUTH_RUNTIME_ROLE,
    CAPAUTH_RUNTIME_SUBJECT,
    PersistenceContractBase,
)
from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

MIGRATION = Path("migrations/0024_artifact_intake.sql")
RETENTION_ID = UUID("f0000000-0000-4000-8000-000000000001")
T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, UUID):
        return f"'{value}'::uuid"
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'::timestamptz"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, tuple) and all(isinstance(item, UUID) for item in value):
        rendered = ",".join(f"'{item}'::uuid" for item in value)
        return f"ARRAY[{rendered}]::uuid[]"
    if isinstance(value, list):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return "'" + encoded.replace("'", "''") + "'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"unsupported synthetic SQL value: {type(value)!r}")


class DockerPsqlSession:
    def execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        rendered = statement
        for name in sorted(parameters, key=len, reverse=True):
            rendered = rendered.replace(f"%({name})s", _literal(parameters[name]))
        if "%(" in rendered:
            raise ValueError("synthetic SQL parameters are incomplete")
        output = PersistenceContractBase._psql(
            CAPAUTH_RUNTIME_ROLE, rendered
        ).stdout.strip()
        if not output:
            return []
        if statement.strip() == "SELECT true AS ready":
            keys = ("ready",)
        elif "response_document::text AS response_document" in statement:
            keys = ("request_sha256", "response_document")
        elif "projection.projection_document::text AS projection_document" in statement:
            keys = ("artifact_id", "projection_document")
        elif "SELECT id AS outbox_id FROM inserted_outbox" in statement:
            keys = ("outbox_id",)
        elif (
            "SELECT result_artifact_id AS artifact_id, duplicate, false AS replayed"
            in statement
        ):
            keys = (
                "artifact_id",
                "duplicate",
                "replayed",
                "conflict",
                "projection_revision",
            )
        elif "artifact.artifact_kind" in statement:
            keys = (
                "artifact_id",
                "artifact_kind",
                "content_sha256",
                "original_sha256",
                "review_state",
                "projection_revision",
                "custody_count",
            )
        else:
            keys = (
                "artifact_id",
                "projection_revision",
                "replayed",
                "conflict",
                "precondition",
            )

        def value(key: str, item: str) -> object:
            if not item:
                return None
            if key in {"artifact_id", "outbox_id"}:
                return UUID(item)
            if key in {"projection_revision", "custody_count"}:
                return int(item)
            if key in {"duplicate", "replayed", "conflict", "precondition"}:
                return item == "t"
            if key == "ready":
                return item == "t"
            return item

        return [
            {
                key: value(key, item)
                for key, item in zip(keys, line.split("|"), strict=True)
            }
            for line in output.splitlines()
        ]


@contextmanager
def _session() -> Iterator[DockerPsqlSession]:
    yield DockerPsqlSession()


class TestArtifactPostgres:
    @classmethod
    def setup_class(cls) -> None:
        cls._base_migration_root = Path(
            tempfile.mkdtemp(prefix="sklegal-art01f-base-migrations-")
        )
        source_root = persistence_contract_support.MIGRATION_ROOT
        shutil.copy2(source_root / "manifest.json", cls._base_migration_root)
        for name in persistence_contract_support.MIGRATION_FILES:
            shutil.copy2(source_root / name, cls._base_migration_root)
        original_migrate = PersistenceContractBase.__dict__["_migrate"]

        def migrate_base(
            target: type[PersistenceContractBase],
            *arguments: str,
            user: str = "sklegal_migrator",
            root: Path = cls._base_migration_root,
            check: bool = True,
        ) -> object:
            return original_migrate.__func__(
                target, *arguments, user=user, root=root, check=check
            )

        PersistenceContractBase._migrate = classmethod(migrate_base)  # type: ignore[method-assign]
        try:
            PersistenceContractBase.setUpClass()
        finally:
            PersistenceContractBase._migrate = original_migrate  # type: ignore[method-assign]
        base = PersistenceContractBase
        tenant_id = base.fixture["tenant_alpha"]
        matter_id = base.fixture["matter_alpha_one"]
        base._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_memberships (
                tenant_id, matter_id, principal_id, membership_role
            ) VALUES (
                '{tenant_id}', '{matter_id}', '{CAPAUTH_RUNTIME_PRINCIPAL}',
                'member'
            ) ON CONFLICT DO NOTHING;
            INSERT INTO sklegal_legal.retention_policies (
                id, policy_change_id, tenant_id, matter_id, retain_for_days,
                effective_from, decided_by_principal_id
            ) VALUES (
                '{RETENTION_ID}', 9001, '{tenant_id}', '{matter_id}', 30,
                '2026-01-01T00:00:00Z', '{CAPAUTH_RUNTIME_PRINCIPAL}'
            );
            """,
        )
        up = MIGRATION.read_text(encoding="utf-8").split("-- sklegal:down", 1)[0]
        base._psql("sklegal_migrator", up.replace("-- sklegal:up", "", 1))

    @classmethod
    def teardown_class(cls) -> None:
        base = PersistenceContractBase
        down = MIGRATION.read_text(encoding="utf-8").split("-- sklegal:down", 1)[1]
        base._psql("sklegal_migrator", down)
        assert (
            base._psql(
                "postgres", "SELECT to_regnamespace('sklegal_artifact') IS NULL;"
            ).stdout.strip()
            == "t"
        )
        up = MIGRATION.read_text(encoding="utf-8").split("-- sklegal:down", 1)[0]
        base._psql("sklegal_migrator", up.replace("-- sklegal:up", "", 1))
        shutil.rmtree(cls._base_migration_root)

    @classmethod
    def _batch(cls) -> ArtifactPersistenceBatch:
        source = unit_batch()
        tenant_id = UUID(PersistenceContractBase.fixture["tenant_alpha"])
        matter_id = UUID(PersistenceContractBase.fixture["matter_alpha_one"])
        second = replace(
            source.derived[0],
            artifact_id=UUID("b0000000-0000-4000-8000-000000000011"),
            scan_id=UUID("b0000000-0000-4000-8000-000000000012"),
            custody_event_id=UUID("b0000000-0000-4000-8000-000000000013"),
            filename="synthetic-exhibit.txt.text.txt",
            content_sha256="8" * 64,
            storage_locator="synthetic:sha256:" + "8" * 64,
            derivation_kind="text_extraction",
        )
        return replace(
            source,
            tenant_id=tenant_id,
            matter_id=matter_id,
            principal_id=UUID(CAPAUTH_RUNTIME_PRINCIPAL),
            retention_policy_id=RETENTION_ID,
            occurred_at=T0,
            derived=(source.derived[0], second),
        )

    @staticmethod
    def _context(
        source: ArtifactPersistenceBatch,
        artifact_id: UUID,
        *,
        revision: int,
        suffix: int,
    ) -> ArtifactMutationContext:
        return ArtifactMutationContext(
            tenant_id=source.tenant_id,
            matter_id=source.matter_id,
            artifact_id=artifact_id,
            principal_id=source.principal_id,
            expected_projection_revision=revision,
            idempotency_key_sha256=f"{suffix:x}".zfill(64),
            request_sha256=f"{suffix + 16:x}".zfill(64),
            policy_decision_id=UUID(f"60000000-0000-4000-8000-{suffix:012d}"),
            policy_revision=source.policy_revision,
            audit_event_id=UUID(f"90000000-0000-4000-8000-{suffix:012d}"),
            outbox_id=UUID(f"a0000000-0000-4000-8000-{suffix:012d}"),
            occurred_at=T0,
        )

    def test_capauth_router_uses_only_durable_artifact_store(self) -> None:
        tenant_id = UUID(PersistenceContractBase.fixture["tenant_alpha"])
        beta_tenant_id = UUID(PersistenceContractBase.fixture["tenant_beta"])
        matter_id = UUID(PersistenceContractBase.fixture["matter_alpha_one"])
        other_matter_id = UUID(PersistenceContractBase.fixture["matter_alpha_two"])
        rig = CapabilityTestRig()
        principal = PrincipalContext(
            principal_id=UUID(CAPAUTH_RUNTIME_PRINCIPAL),
            principal_type=PrincipalType.SERVICE,
            subject=CAPAUTH_RUNTIME_SUBJECT,
            tenant_id=tenant_id,
        )
        beta_principal = rig.principal(
            PrincipalType.SERVICE,
            tenant_id=beta_tenant_id,
            subject="synthetic:service:artifact-beta-denial",
        )
        rig.principals.set(principal, active=True)
        store: ArtifactStore = PostgresArtifactStore(
            PostgresArtifactRepository(_session)
        )
        policy = StaticArtifactPolicy(
            memberships={
                (tenant_id, matter_id, principal.principal_id),
            },
            allowed_classifications={"public"},
            revision="8" * 64,
            valid_until=rig.clock() + timedelta(hours=1),
        )
        service = ArtifactIntakeService(
            store=store,
            policy=policy,
            adapter=SyntheticArtifactAdapter(),
            clock=rig.clock,
        )

        def selected(request: Request) -> PrincipalContext:
            if request.headers.get("X-Synthetic-Principal") == "beta":
                return beta_principal
            return principal

        def scope_resolver(request: Request) -> BoundaryScope:
            request_principal = selected(request)
            request_matter = UUID(str(request.path_params["matter_id"]))
            resource_id = request.path_params.get("artifact_id") or str(request_matter)
            return BoundaryScope(
                tenant_id=request_principal.tenant_id,
                matter_id=request_matter,
                resource_id=str(resource_id),
            )

        app = FastAPI()
        app.include_router(
            build_artifact_intake_router(
                service=service,
                authorizer=rig.authorizer,
                principal_resolver=selected,
                scope_resolver=scope_resolver,
            )
        )

        def token(
            route: str,
            capability: Capability,
            *,
            resource_id: str | None = None,
            token_principal: PrincipalContext = principal,
            token_matter_id: UUID = matter_id,
        ) -> str:
            grant = rig.grant(
                capability=capability,
                purpose=Purpose.EVIDENCE_REVIEW,
                target=f"api:artifact_intake.{route}",
                tenant_id=token_principal.tenant_id,
                matter_id=token_matter_id,
                resource_id=resource_id or str(token_matter_id),
            )
            return raw_leaf(rig.issue(token_principal, grant))

        artifact_command = command(
            content=b"ART-01F2 public synthetic durable route bytes.",
            source_identity="art01f2-durable-route",
            derivations=("ocr", "text_extraction"),
        ).model_copy(update={"retention_policy_id": RETENTION_ID})
        body = artifact_command.model_dump(mode="json", by_alias=True)
        try:
            with TestClient(app) as client:
                created = client.post(
                    f"/v1/matters/{matter_id}/artifacts",
                    headers={
                        "Authorization": f"Bearer {token('create', Capability.EVIDENCE_MANAGE)}",
                        "Idempotency-Key": "art01f2-router-create",
                    },
                    json=body,
                )
                assert created.status_code == 201, created.text
                first = created.json()
                artifact_id = first["artifact"]["artifactId"]
                assert first["artifact"]["projectionRevision"] == 1

                duplicate = client.post(
                    f"/v1/matters/{matter_id}/artifacts",
                    headers={
                        "Authorization": f"Bearer {token('create', Capability.EVIDENCE_MANAGE)}",
                        "Idempotency-Key": "art01f2-router-duplicate",
                    },
                    json=body,
                )
                assert duplicate.status_code == 201, duplicate.text
                assert duplicate.json()["duplicate"] is True
                assert duplicate.json()["artifact"]["projectionRevision"] == 2

                replay = client.post(
                    f"/v1/matters/{matter_id}/artifacts",
                    headers={
                        "Authorization": f"Bearer {token('create', Capability.EVIDENCE_MANAGE)}",
                        "Idempotency-Key": "art01f2-router-create",
                    },
                    json=body,
                )
                assert replay.status_code == 201, replay.text
                assert replay.json()["replayed"] is True
                assert replay.json()["artifact"]["projectionRevision"] == 1

                fetched = client.get(
                    f"/v1/matters/{matter_id}/artifacts/{artifact_id}",
                    headers={
                        "Authorization": f"Bearer {token('get', Capability.EVIDENCE_READ, resource_id=artifact_id)}"
                    },
                )
                assert fetched.status_code == 200, fetched.text
                assert fetched.json()["projectionRevision"] == 2
                assert len(fetched.json()["custody"]) == 2

                reviewed = client.post(
                    f"/v1/matters/{matter_id}/artifacts/{artifact_id}/reviews",
                    headers={
                        "Authorization": f"Bearer {token('review', Capability.EVIDENCE_MANAGE, resource_id=artifact_id)}",
                        "Idempotency-Key": "art01f2-router-review",
                    },
                    json={
                        "artifactId": artifact_id,
                        "expectedProjectionRevision": 2,
                        "decision": "accepted",
                        "rationale": "Durable public synthetic review is complete.",
                    },
                )
                assert reviewed.status_code == 200, reviewed.text
                assert reviewed.json()["artifact"]["projectionRevision"] == 3

                correction_target = first["derivedArtifacts"][0]["artifactId"]
                corrected = client.post(
                    f"/v1/matters/{matter_id}/artifacts/{correction_target}/corrections",
                    headers={
                        "Authorization": f"Bearer {token('correct', Capability.EVIDENCE_MANAGE, resource_id=correction_target)}",
                        "Idempotency-Key": "art01f2-router-correction",
                    },
                    json={
                        "targetArtifactId": correction_target,
                        "expectedProjectionRevision": 1,
                        "corrected": command(
                            content=b"ART-01F2 corrected public synthetic bytes.",
                            derivations=(),
                        ).original.model_dump(mode="json", by_alias=True),
                        "reason": "Human review corrected the durable derivation.",
                        "toolName": "human-review",
                        "toolVersion": "1",
                    },
                )
                assert corrected.status_code == 201, corrected.text
                successor_id = corrected.json()["artifact"]["artifactId"]

                supersession_target = first["derivedArtifacts"][1]["artifactId"]
                superseded = client.post(
                    f"/v1/matters/{matter_id}/artifacts/{supersession_target}/supersessions",
                    headers={
                        "Authorization": f"Bearer {token('supersede', Capability.EVIDENCE_MANAGE, resource_id=supersession_target)}",
                        "Idempotency-Key": "art01f2-router-supersession",
                    },
                    json={
                        "artifactId": supersession_target,
                        "successorArtifactId": successor_id,
                        "expectedProjectionRevision": 1,
                        "reason": "The corrected durable artifact is preferred.",
                    },
                )
                assert superseded.status_code == 201, superseded.text
                assert superseded.json()["artifact"]["reviewState"] == "superseded"

                before_denials = PersistenceContractBase._psql(
                    "postgres",
                    f"""
                    SELECT count(*) FROM sklegal_artifact.artifact_audit_facts
                     WHERE tenant_id = '{tenant_id}'
                       AND matter_id = '{matter_id}'
                       AND occurred_at = '{rig.clock().isoformat()}';
                    """,
                ).stdout.strip()
                wrong_matter = client.get(
                    f"/v1/matters/{other_matter_id}/artifacts/{artifact_id}",
                    headers={
                        "Authorization": f"Bearer {token('get', Capability.EVIDENCE_READ, resource_id=artifact_id, token_matter_id=other_matter_id)}"
                    },
                )
                assert wrong_matter.status_code == 403
                assert wrong_matter.json()["detail"]["code"] == "access_denied"
                wrong_tenant = client.get(
                    f"/v1/matters/{matter_id}/artifacts/{artifact_id}",
                    headers={
                        "Authorization": f"Bearer {token('get', Capability.EVIDENCE_READ, resource_id=artifact_id, token_principal=beta_principal)}",
                        "X-Synthetic-Principal": "beta",
                    },
                )
                assert wrong_tenant.status_code == 403
                assert wrong_tenant.json()["detail"]["code"] == "access_denied"
                assert (
                    PersistenceContractBase._psql(
                        "postgres",
                        f"""
                        SELECT count(*) FROM sklegal_artifact.artifact_audit_facts
                         WHERE tenant_id = '{tenant_id}'
                           AND matter_id = '{matter_id}'
                           AND occurred_at = '{rig.clock().isoformat()}';
                        """,
                    ).stdout.strip()
                    == before_denials
                )
        finally:
            rig.close()

        counts = PersistenceContractBase._psql(
            "postgres",
            f"""
            SELECT
                (SELECT count(*) FROM sklegal_artifact.artifact_idempotency_receipts
                  WHERE tenant_id = '{tenant_id}' AND matter_id = '{matter_id}'
                    AND created_at = '{rig.clock().isoformat()}'
                    AND response_document IS NOT NULL),
                (SELECT count(*) FROM sklegal_artifact.artifact_projection_revisions
                  WHERE tenant_id = '{tenant_id}' AND matter_id = '{matter_id}'
                    AND recorded_at = '{rig.clock().isoformat()}'
                    AND projection_document IS NOT NULL),
                (SELECT count(*) FROM sklegal_artifact.artifact_audit_facts
                  WHERE tenant_id = '{tenant_id}' AND matter_id = '{matter_id}'
                    AND occurred_at = '{rig.clock().isoformat()}'),
                (SELECT count(*) FROM sklegal_artifact.artifact_outbox
                  WHERE tenant_id = '{tenant_id}' AND matter_id = '{matter_id}'
                    AND available_at = '{rig.clock().isoformat()}');
            """,
        ).stdout.strip()
        assert counts == "5|8|6|6"

    def test_runtime_governance_duplicate_and_lifecycle_parity(self) -> None:
        repository = PostgresArtifactRepository(_session)
        source = self._batch()
        first = repository.append_intake(source)
        duplicate_source = replace(
            source,
            artifact_id=UUID("30000000-0000-4000-8000-000000000099"),
            source_identity="synthetic-source-duplicate",
            custody_event_id=UUID("80000000-0000-4000-8000-000000000099"),
            idempotency_key="artifact-persistence-duplicate",
            idempotency_key_sha256="a" * 64,
            request_sha256="b" * 64,
            audit_event_id=UUID("90000000-0000-4000-8000-000000000099"),
            outbox_id=UUID("a0000000-0000-4000-8000-000000000099"),
            derived=(),
            proposed_links=(),
        )
        duplicate = repository.append_intake(duplicate_source)
        replay = repository.append_intake(duplicate_source)
        assert first.projection_revision == 1
        assert duplicate.artifact_id == first.artifact_id
        assert duplicate.duplicate is True
        assert duplicate.projection_revision == 2
        assert replay.replayed is True
        assert replay.projection_revision == 2
        current = repository.read(
            ArtifactReadContext(
                tenant_id=source.tenant_id,
                matter_id=source.matter_id,
                artifact_id=source.artifact_id,
                principal_id=source.principal_id,
                policy_decision_id=source.policy_decision_id,
                policy_revision=source.policy_revision,
                request_sha256="9" * 64,
                audit_event_id=UUID("90000000-0000-4000-8000-000000000091"),
                outbox_id=UUID("a0000000-0000-4000-8000-000000000091"),
                occurred_at=T0,
            )
        )
        assert current is not None
        assert current.projection_revision == 2
        assert current.custody_count == 2
        assert (
            PersistenceContractBase._psql(
                CAPAUTH_RUNTIME_ROLE,
                f"""
                SELECT count(*) FROM sklegal_artifact.artifact_audit_facts
                 WHERE tenant_id = '{source.tenant_id}'
                   AND matter_id = '{source.matter_id}'
                   AND id = '90000000-0000-4000-8000-000000000091'
                   AND action = 'artifact.read';
                """,
            ).stdout.strip()
            == "1"
        )

        reviewed = repository.append_review(
            ArtifactReviewBatch(
                context=self._context(
                    source, source.artifact_id, revision=2, suffix=21
                ),
                review_id=UUID("e0000000-0000-4000-8000-000000000021"),
                decision="accepted",
                rationale="Synthetic durable review matches the public projection.",
            )
        )
        assert reviewed.projection_revision == 3

        target, second = source.derived
        corrected = DerivedArtifactRow(
            artifact_id=UUID("e0000000-0000-4000-8000-000000000031"),
            scan_id=UUID("e0000000-0000-4000-8000-000000000032"),
            custody_event_id=UUID("e0000000-0000-4000-8000-000000000033"),
            filename="synthetic.corrected.txt",
            media_type="text/plain",
            byte_count=28,
            content_sha256="c" * 64,
            storage_locator="synthetic:sha256:" + "c" * 64,
            derivation_kind="human_correction",
            tool_name="human-review",
            tool_version="1",
        )
        correction = repository.append_correction(
            ArtifactCorrectionBatch(
                context=self._context(
                    source, target.artifact_id, revision=1, suffix=31
                ),
                correction_id=UUID("e0000000-0000-4000-8000-000000000034"),
                supersession_id=UUID("e0000000-0000-4000-8000-000000000035"),
                corrected=corrected,
                reason="Human review corrected the public synthetic derivation.",
            )
        )
        assert correction.artifact_id == corrected.artifact_id
        supersession = repository.append_supersession(
            ArtifactSupersessionBatch(
                context=self._context(
                    source, second.artifact_id, revision=1, suffix=41
                ),
                supersession_id=UUID("e0000000-0000-4000-8000-000000000041"),
                successor_artifact_id=corrected.artifact_id,
                reason="The reviewed synthetic correction is the current successor.",
            )
        )
        assert supersession.projection_revision == 2

        with pytest.raises(ArtifactPersistencePrecondition):
            repository.append_review(
                ArtifactReviewBatch(
                    context=self._context(
                        source, target.artifact_id, revision=2, suffix=51
                    ),
                    review_id=UUID("e0000000-0000-4000-8000-000000000051"),
                    decision="accepted",
                    rationale="Superseded projections cannot be accepted again.",
                )
            )
        with pytest.raises(ArtifactPersistencePrecondition):
            repository.append_correction(
                ArtifactCorrectionBatch(
                    context=self._context(
                        source, source.artifact_id, revision=3, suffix=61
                    ),
                    correction_id=UUID("e0000000-0000-4000-8000-000000000061"),
                    supersession_id=UUID("e0000000-0000-4000-8000-000000000062"),
                    corrected=replace(
                        corrected,
                        artifact_id=UUID("e0000000-0000-4000-8000-000000000063"),
                        scan_id=UUID("e0000000-0000-4000-8000-000000000064"),
                        custody_event_id=UUID("e0000000-0000-4000-8000-000000000065"),
                        content_sha256="d" * 64,
                        storage_locator="synthetic:sha256:" + "d" * 64,
                    ),
                    reason="Original bytes cannot be corrected or superseded.",
                )
            )

    def test_hash_lineage_and_cross_scope_fail_closed(self) -> None:
        source = self._batch()
        derived = source.derived[0]
        bad = PersistenceContractBase._psql(
            CAPAUTH_RUNTIME_ROLE,
            f"""
            BEGIN;
            INSERT INTO sklegal_artifact.artifact_derivations (
                tenant_id, matter_id, parent_artifact_id, child_artifact_id,
                derivation_kind, tool_name, tool_version, input_sha256,
                output_sha256, created_at
            ) VALUES (
                '{source.tenant_id}', '{source.matter_id}', '{source.artifact_id}',
                '{derived.artifact_id}', 'ocr', 'synthetic-ocr', '1.0.0',
                '{"0" * 64}', '{derived.content_sha256}', '{T0.isoformat()}'
            );
            ROLLBACK;
            """,
            check=False,
        )
        assert bad.returncode != 0
        assert "content-hash connected" in bad.stderr

        disconnected = PersistenceContractBase._psql(
            CAPAUTH_RUNTIME_ROLE,
            f"""
            BEGIN;
            INSERT INTO sklegal_artifact.artifacts (
                tenant_id, matter_id, id, artifact_kind, parent_artifact_id,
                source_identity, source_version, source_identity_sha256,
                filename, media_type, byte_count, content_sha256,
                original_sha256, storage_locator, acquisition_method,
                quarantine_state, extraction_state, classification,
                privilege_state, retention_policy_id, legal_hold_ids,
                ethical_wall_ids, policy_decision_id, policy_revision,
                projection_revision, created_at
            )
            SELECT tenant_id, matter_id,
                   'e0000000-0000-4000-8000-000000000071', 'derived', id,
                   source_identity, source_version, source_identity_sha256,
                   'synthetic.disconnected.txt', 'text/plain', 8,
                   '{"4" * 64}', original_sha256,
                   'synthetic:sha256:{"4" * 64}', 'derived', 'released',
                   'complete', classification, privilege_state,
                   retention_policy_id, legal_hold_ids, ethical_wall_ids,
                   policy_decision_id, policy_revision, 1, '{T0.isoformat()}'
              FROM sklegal_artifact.artifacts
             WHERE tenant_id = '{source.tenant_id}'
               AND matter_id = '{source.matter_id}'
               AND id = '{source.artifact_id}';
            COMMIT;
            """,
            check=False,
        )
        assert disconnected.returncode != 0
        assert "no content-hash-connected lineage" in disconnected.stderr

        other = replace(
            source,
            artifact_id=UUID("30000000-0000-4000-8000-000000000077"),
            matter_id=UUID(PersistenceContractBase.fixture["matter_alpha_two"]),
            content_sha256="e" * 64,
            storage_locator="synthetic:sha256:" + "e" * 64,
            idempotency_key_sha256="f" * 64,
            request_sha256="1" * 64,
            derived=(),
            proposed_links=(),
        )
        with pytest.raises(Exception, match="transaction unavailable"):
            PostgresArtifactRepository(_session).append_intake(other)
        assert (
            PersistenceContractBase._psql(
                CAPAUTH_RUNTIME_ROLE,
                f"""
                SELECT count(*) FROM sklegal_artifact.artifact_idempotency_receipts
                 WHERE tenant_id = '{other.tenant_id}'
                   AND matter_id = '{other.matter_id}'
                   AND idempotency_key_sha256 = '{other.idempotency_key_sha256}';
                """,
            ).stdout.strip()
            == "0"
        )

    def test_runtime_acl_is_read_insert_only(self) -> None:
        privileges = PersistenceContractBase._psql(
            "postgres",
            """
            SELECT has_schema_privilege('sklegal_runtime', 'sklegal_artifact', 'USAGE'),
                   has_table_privilege('sklegal_runtime', 'sklegal_artifact.artifacts', 'SELECT'),
                   has_table_privilege('sklegal_runtime', 'sklegal_artifact.artifacts', 'INSERT'),
                   has_table_privilege('sklegal_runtime', 'sklegal_artifact.artifacts', 'UPDATE'),
                   has_table_privilege('sklegal_runtime', 'sklegal_artifact.artifacts', 'DELETE');
            """,
        ).stdout.strip()
        assert privileges == "t|t|t|f|f"
