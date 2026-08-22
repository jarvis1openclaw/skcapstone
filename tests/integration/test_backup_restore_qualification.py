"""SKL-S5-04D backup and restore qualification over disposable PostgreSQL.

This module proves the S5-04D card scope against real disposable
containers instead of the shared persistence-contract instance, because
point-in-time recovery needs a source cluster started with WAL archiving
and a restore cluster booted from a base backup plus archived WAL. The
scenarios are:

- Full logical restore: dump the migrated, seeded, audit-populated source
  into a separate scratch instance whose roles were provisioned first,
  then verify the audit hash chain, outbox rows and deliveries, migration
  readback, ethical-wall rows, and forced row-level security after
  restore.
- Point-in-time restore: take a base backup between two audit event
  batches, archive the WAL of the second batch, then recover fresh
  clusters to a target between the batches and to a target after both.
  The chain must end exactly at the requested target, verify clean, and
  keep row-level security intact.
- Restore authorization: every restore above runs through
  ``authorize_restore`` first, and a tampered manifest is denied before
  any target instance exists.

All identifiers are synthetic. Nothing here touches HammerTime paths,
production data, or the shared development stack.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import subprocess
import time
import unittest
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sklegal_persistence import (
    BackupManifest,
    RestoreRequest,
    authorize_restore,
    hold_wall_digest_rows,
)

from tests.integration.persistence_contract_support import (
    FIXTURE,
    MIGRATION_FILES,
    REPO_ROOT,
    RUNTIME_PROVISIONER,
    PersistenceContractBase,
)

POSTGRES_IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f85"
    "3205ce20"
    "d399df4e"
    "33a0b4c7"
    "15672f23"
    "2f4ee744"
    "0499747e"
    "6e02c126"
)
ARCHIVE_MOUNT = "/wal_archive"
BASEBACKUP_MOUNT = "/basebackup"
DATA_MOUNT = "/var/lib/postgresql/data"
QUALIFICATION_CARD_LABEL = "com.sklegal.test-card=SKL-S5-04D"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout: {result.stdout[-1500:]}\nstderr: {result.stderr[-1500:]}"
        )
    return result


def _psql_container(
    container: str,
    user: str,
    sql: str,
    *,
    database: str = "sklegal",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"psql failed for role {user} on {container}: {result.stderr.strip()}"
        )
    return result


class BackupRestoreQualificationBase(PersistenceContractBase):
    """One dedicated WAL-archiving source cluster for the S5-04D module.

    The inherited migration and seeding helpers from the persistence
    contract are reused unchanged; only the cluster bootstrap differs so
    ``archive_mode`` is on from first boot and archived WAL survives the
    source container for the restore clusters.
    """

    archive_volume: str
    basebackup_volume: str
    source_data_volume: str

    @classmethod
    def setUpClass(cls) -> None:
        cls = BackupRestoreQualificationBase
        if cls._shared_runtime_ready:
            return
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        suffix = f"{cls._pid()}-{uuid.uuid4().hex[:8]}"
        cls.container = f"skl-s504d-src-{suffix}"
        cls.archive_volume = f"skl-s504d-wal-{suffix}"
        cls.basebackup_volume = f"skl-s504d-base-{suffix}"
        cls.source_data_volume = f"skl-s504d-data-{suffix}"
        for volume in (
            cls.archive_volume,
            cls.basebackup_volume,
            cls.source_data_volume,
        ):
            _run(["docker", "volume", "create", volume])
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={cls.archive_volume},dst={ARCHIVE_MOUNT}",
                POSTGRES_IMAGE,
                "chown",
                "70:70",
                ARCHIVE_MOUNT,
            ]
        )
        atexit.register(cls._remove_qualification_runtime)
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                cls.container,
                "--label",
                QUALIFICATION_CARD_LABEL,
                "--network",
                "none",
                "--mount",
                f"type=volume,src={cls.source_data_volume},dst={DATA_MOUNT}",
                "--mount",
                f"type=volume,src={cls.archive_volume},dst={ARCHIVE_MOUNT}",
                "--mount",
                f"type=volume,src={cls.basebackup_volume},dst={BASEBACKUP_MOUNT}",
                "--env",
                "POSTGRES_DB=sklegal",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
                "postgres",
                "-c",
                "archive_mode=on",
                "-c",
                "wal_level=replica",
                "-c",
                f"archive_command=test ! -f {ARCHIVE_MOUNT}/%f "
                f"&& cp %p {ARCHIVE_MOUNT}/%f",
            ]
        )
        _wait_ready(cls.container)
        cls._psql(
            "postgres",
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        _run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(RUNTIME_PROVISIONER),
                "--docker-container",
                cls.container,
            ],
        )
        cls._migrate("up")
        cls._seed_scopes()
        cls._shared_runtime_ready = True

    @classmethod
    def _pid(cls) -> int:
        import os

        return os.getpid()

    @classmethod
    def _remove_qualification_runtime(cls) -> None:
        cls = BackupRestoreQualificationBase
        subprocess.run(
            ["docker", "rm", "--force", cls.container],
            check=False,
            capture_output=True,
            text=True,
        )
        for volume in (
            getattr(cls, "archive_volume", ""),
            getattr(cls, "basebackup_volume", ""),
            getattr(cls, "source_data_volume", ""),
        ):
            if volume:
                subprocess.run(
                    ["docker", "volume", "rm", "--force", volume],
                    check=False,
                    capture_output=True,
                    text=True,
                )

    def _authorized_request(
        self,
        manifest: BackupManifest,
        *,
        requested_tenant_ids: frozenset[UUID],
        tamper: dict[str, Any] | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        record = json.loads(manifest.canonical_bytes().decode("utf-8"))
        if tamper is not None:
            record.update(tamper)
        request = RestoreRequest(
            backup_id=manifest.backup_id,
            target_identity="disposable-restore-target",
            operator="skl-s5-04d-qual",
            approval_reference="approval-s5-04d-disposable-restore",
            requested_tenant_ids=requested_tenant_ids,
            hold_state_acknowledged=True,
            decryption_key_reference=manifest.key_custody_reference,
        )
        decision = authorize_restore(
            record,
            recorded_manifest_sha256=manifest.manifest_sha256(),
            request=request,
        )
        return decision.allowed, decision.reasons

    def _append_event(
        self,
        *,
        event_id: str,
        run_id: str,
        action: str,
        span_ordinal: int,
    ) -> dict[str, Any]:
        value = self.fixture
        result = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.append_event(
                '{event_id}', '{value["tenant_alpha"]}',
                '{value["matter_alpha_one"]}', '{value["principal_alpha_one"]}',
                '{run_id}', 'a7000000-0000-4000-8000-000000000021',
                '{"5" * 32}', '{span_ordinal:016x}', '01', 'api',
                '{action}', 'matter', '{value["matter_alpha_one"]}',
                NULL, NULL, 'success', 'allow',
                clock_timestamp() - interval '1 second', '{{}}'::jsonb
            )::text;
            """,
        )
        return json.loads(
            next(line for line in result.stdout.splitlines() if line.startswith("{"))
        )

    def _deliver_outbox(self, outbox_id: str, delivery_id: str) -> None:
        first = self._psql(
            "sklegal_test_alpha_one",
            "SELECT sklegal_audit.record_outbox_delivery("
            f"'{outbox_id}', 'audit.local', '{delivery_id}');",
        )
        self.assertEqual("t", first.stdout.strip())

    def _chain_head(self, container: str) -> str:
        value = self.fixture
        return _psql_container(
            container,
            "sklegal_test_alpha_one",
            f"""
            SELECT last_event_sequence || ':' || last_event_sha256
            FROM sklegal_audit.chain_heads
            WHERE tenant_id = '{value["tenant_alpha"]}';
            """,
        ).stdout.strip()

    def _chain_verifies(self, container: str) -> bool:
        result = _psql_container(
            container,
            "sklegal_test_alpha_one",
            "SELECT sklegal_audit.verify_current_tenant_chain();",
        )
        return result.stdout.strip().splitlines()[-1] == "t"

    def _rls_isolates_beta(self, container: str, run_id: str) -> None:
        value = self.fixture
        seen = _psql_container(
            container,
            "sklegal_test_beta_one",
            f"""
            SELECT count(*) FROM sklegal_audit.events
            WHERE tenant_id = '{value["tenant_alpha"]}' OR run_id = '{run_id}';
            """,
        )
        self.assertEqual("0", seen.stdout.strip())

    def _migration_count(self, container: str) -> str:
        return _psql_container(
            container,
            "postgres",
            "SELECT count(*) FROM sklegal_migrations.schema_migrations;",
        ).stdout.strip()

    def _wall_rows(self, container: str) -> tuple[tuple[str, ...], ...]:
        output = _psql_container(
            container,
            "postgres",
            """
            SELECT wall_code, tenant_id::text, matter_id::text, active::text
            FROM sklegal_legal.ethical_walls ORDER BY wall_code;
            """,
        ).stdout.strip()
        return tuple(tuple(line.split("|")) for line in output.splitlines() if line)

    def _basebackup_fingerprint(self) -> tuple[str, int]:
        """Digest the base backup directory listing deterministically.

        The fingerprint digests each file's name and the sha256 of its
        contents as reported inside the container, sorted by path. This is
        the manifest identity for the base backup; per-restore integrity
        is still proven by recomputing the audit chain after recovery.
        """

        listing = _run(
            [
                "docker",
                "exec",
                self.container,
                "sh",
                "-c",
                f"cd {BASEBACKUP_MOUNT} && find . -type f -print0 "
                f"| sort -z | xargs -0 sha256sum",
            ]
        ).stdout
        return (
            hashlib.sha256(listing.encode("utf-8")).hexdigest(),
            len(listing.encode("utf-8")),
        )


class FullLogicalRestoreTests(BackupRestoreQualificationBase):
    """Full logical dump restored into a separate scratch instance."""

    def test_full_restore_preserves_chain_outbox_walls_and_rls(self) -> None:
        value = self.fixture
        run_id = "a7000000-0000-4000-8000-000000000001"
        first = self._append_event(
            event_id="a7000000-0000-4000-8000-000000000011",
            run_id=run_id,
            action="audit.s504d.restore.before-backup",
            span_ordinal=1,
        )
        second = self._append_event(
            event_id="a7000000-0000-4000-8000-000000000012",
            run_id=run_id,
            action="audit.s504d.restore.before-backup",
            span_ordinal=2,
        )
        self._deliver_outbox(first["outbox_id"], "a7000000-0000-4000-8000-000000000031")
        wall_id = "a7000000-0000-4000-8000-000000000041"
        membership_id = "a7000000-0000-4000-8000-000000000042"
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.ethical_walls
                (id, policy_change_id, tenant_id, matter_id, wall_code, active,
                 membership_complete, effective_from, decided_by_principal_id)
            VALUES ('{wall_id}', 1, '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 's504d-wall', true, false,
                    clock_timestamp(), '{value["principal_alpha_one"]}');
            INSERT INTO sklegal_legal.ethical_wall_memberships
                (id, policy_change_id, tenant_id, matter_id, wall_id,
                 principal_id, disposition, effective_from,
                 decided_by_principal_id)
            VALUES ('{membership_id}', 1, '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', '{wall_id}',
                    '{value["principal_alpha_one"]}', 'allowed',
                    clock_timestamp(), '{value["principal_alpha_one"]}');
            """,
        )
        wall_rows = self._wall_rows(self.container)
        self.assertEqual(1, len(wall_rows))

        started_at = _utcnow()
        dump = _run(
            [
                "docker",
                "exec",
                self.container,
                "pg_dump",
                "--username",
                "postgres",
                "--dbname",
                "sklegal",
                "--no-owner",
            ]
        ).stdout.encode("utf-8")
        completed_at = _utcnow()
        wall_digest, wall_count = hold_wall_digest_rows(wall_rows)
        manifest = BackupManifest(
            backup_id="skl-backup-s504d-logical-001",
            kind="logical_full",
            source_identity=self.container,
            database="sklegal",
            started_at=started_at,
            completed_at=completed_at,
            content_sha256=hashlib.sha256(dump).hexdigest(),
            content_bytes=len(dump),
            tenant_ids=frozenset(
                {UUID(value["tenant_alpha"]), UUID(value["tenant_beta"])}
            ),
            hold_wall_digest=wall_digest,
            hold_wall_rows=wall_count,
            encrypted=False,
            key_custody_reference=None,
            retention_class="qualification-scratch",
        )
        allowed, reasons = self._authorized_request(
            manifest, requested_tenant_ids=frozenset({UUID(value["tenant_alpha"])})
        )
        self.assertTrue(allowed, reasons)
        tampered, tamper_reasons = self._authorized_request(
            manifest,
            requested_tenant_ids=frozenset({UUID(value["tenant_alpha"])}),
            tamper={"content_bytes": manifest.content_bytes + 1},
        )
        self.assertFalse(tampered)
        self.assertIn("manifest_digest_mismatch", tamper_reasons)
        self.assertNotIn(b"-----BEGIN PRIVATE KEY-----", dump)
        self.assertNotIn(b"-----BEGIN OPENSSH PRIVATE KEY-----", dump)

        target = f"{self.container}-restore"
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                target,
                "--label",
                QUALIFICATION_CARD_LABEL,
                "--network",
                "none",
                "--env",
                "POSTGRES_DB=sklegal",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            ]
        )
        try:
            _wait_ready(target)
            _psql_container(
                target,
                "postgres",
                """
                CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOBYPASSRLS;
                CREATE ROLE sklegal_test_alpha_one LOGIN NOSUPERUSER
                    NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                CREATE ROLE sklegal_test_alpha_two LOGIN NOSUPERUSER
                    NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                CREATE ROLE sklegal_test_beta_one LOGIN NOSUPERUSER
                    NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                CREATE ROLE sklegal_test_unbound LOGIN NOSUPERUSER
                    NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                CREATE ROLE sklegal_test_bypass LOGIN NOSUPERUSER
                    NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS;
                """,
            )
            _run(
                [
                    str(REPO_ROOT / ".tools" / "bin" / "uv"),
                    "run",
                    "--locked",
                    "python",
                    str(RUNTIME_PROVISIONER),
                    "--docker-container",
                    target,
                ],
            )
            restore = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    target,
                    "psql",
                    "--no-psqlrc",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--username",
                    "postgres",
                    "--dbname",
                    "sklegal",
                    "--quiet",
                ],
                input=dump.decode("utf-8"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, restore.returncode, restore.stderr[-2000:])

            def counts(container: str) -> str:
                tenant = value["tenant_alpha"]
                return _psql_container(
                    container,
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT (SELECT count(*) FROM sklegal_audit.events
                            WHERE tenant_id = '{tenant}')
                        || ':' || (SELECT count(*) FROM sklegal_audit.outbox
                            WHERE tenant_id = '{tenant}')
                        || ':' || (SELECT count(*) FROM sklegal_audit.outbox_deliveries)
                        || ':' || (SELECT count(*) FROM sklegal_legal.ethical_walls)
                        || ':' || (SELECT count(*) FROM
                            sklegal_legal.ethical_wall_memberships);
                    """,
                ).stdout.strip()

            self.assertEqual(counts(self.container), counts(target))
            self.assertTrue(self._chain_verifies(target))
            self._rls_isolates_beta(target, run_id)
            self.assertEqual(self._chain_head(self.container), self._chain_head(target))
            self.assertIn(second["event_sha256"], self._chain_head(target))
            self.assertEqual(self._wall_rows(self.container), self._wall_rows(target))
            self.assertEqual(str(len(MIGRATION_FILES)), self._migration_count(target))
        finally:
            _run(["docker", "rm", "--force", target])


class PointInTimeRestoreTests(BackupRestoreQualificationBase):
    """Base backup between two batches, recovered to exact target times."""

    def test_pitr_recovers_exactly_to_the_requested_target(self) -> None:
        value = self.fixture
        run_id = "a7000000-0000-4000-8000-000000000002"
        batch_one = self._append_event(
            event_id="a7000000-0000-4000-8000-000000000111",
            run_id=run_id,
            action="audit.s504d.pitr.batch-one",
            span_ordinal=11,
        )
        _switch_wal_and_wait_archive(self.container)
        _run(
            [
                "docker",
                "exec",
                self.container,
                "pg_basebackup",
                "--username",
                "postgres",
                "--pgdata",
                BASEBACKUP_MOUNT,
                "--format",
                "plain",
                "--wal-method",
                "stream",
                "--checkpoint",
                "fast",
            ]
        )
        target_between = _psql_container(
            self.container,
            "postgres",
            "SELECT clock_timestamp()::text;",
        ).stdout.strip()
        time.sleep(1.1)
        batch_two = self._append_event(
            event_id="a7000000-0000-4000-8000-000000000112",
            run_id=run_id,
            action="audit.s504d.pitr.batch-two",
            span_ordinal=12,
        )
        target_after = _psql_container(
            self.container,
            "postgres",
            "SELECT clock_timestamp()::text;",
        ).stdout.strip()
        self._append_event(
            event_id="a7000000-0000-4000-8000-000000000113",
            run_id=run_id,
            action="audit.s504d.pitr.post-target-marker",
            span_ordinal=13,
        )
        _switch_wal_and_wait_archive(self.container)
        base_digest, base_bytes = self._basebackup_fingerprint()
        manifest = BackupManifest(
            backup_id="skl-backup-s504d-pitr-001",
            kind="pitr_base",
            source_identity=self.container,
            database="sklegal",
            started_at=_utcnow(),
            completed_at=_utcnow(),
            content_sha256=base_digest,
            content_bytes=base_bytes,
            tenant_ids=frozenset(
                {UUID(value["tenant_alpha"]), UUID(value["tenant_beta"])}
            ),
            hold_wall_digest="0" * 64,
            hold_wall_rows=0,
            encrypted=False,
            key_custody_reference=None,
            retention_class="qualification-scratch",
        )
        allowed, reasons = self._authorized_request(
            manifest, requested_tenant_ids=frozenset({UUID(value["tenant_alpha"])})
        )
        self.assertTrue(allowed, reasons)

        for label, recovery_target, expected_tail, absent in (
            ("between-batches", target_between, batch_one, batch_two),
            ("after-both-batches", target_after, batch_two, None),
        ):
            with self.subTest(target=label):
                self._recover_and_verify(
                    label=label,
                    recovery_target=recovery_target,
                    expected_tail=expected_tail,
                    absent=absent,
                    run_id=run_id,
                )

    def _recover_and_verify(
        self,
        *,
        label: str,
        recovery_target: str,
        expected_tail: dict[str, Any],
        absent: dict[str, Any] | None,
        run_id: str,
    ) -> None:
        target = f"{self.container}-pitr-{label}"
        restore_volume = f"skl-s504d-restore-{label}-{uuid.uuid4().hex[:8]}"
        _run(["docker", "volume", "create", restore_volume])
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--label",
                QUALIFICATION_CARD_LABEL,
                "--network",
                "none",
                "--mount",
                f"type=volume,src={restore_volume},dst={DATA_MOUNT}",
                "--mount",
                f"type=volume,src={self.basebackup_volume},dst={BASEBACKUP_MOUNT}",
                "--mount",
                f"type=volume,src={self.archive_volume},dst={ARCHIVE_MOUNT}",
                POSTGRES_IMAGE,
                "sh",
                "-c",
                "cp -a "
                + BASEBACKUP_MOUNT
                + "/. "
                + DATA_MOUNT
                + "/ && chown -R 70:70 "
                + DATA_MOUNT
                + " && chmod 0700 "
                + DATA_MOUNT
                + " && printf \"\\nrestore_command = 'cp "
                + ARCHIVE_MOUNT
                + "/%%f %%p'\\nrecovery_target_time = '"
                + recovery_target
                + "'\\nrecovery_target_action = 'promote'\\n"
                + 'archive_mode = off\\n" >> '
                + DATA_MOUNT
                + "/postgresql.auto.conf && touch "
                + DATA_MOUNT
                + "/recovery.signal",
            ]
        )
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                target,
                "--label",
                QUALIFICATION_CARD_LABEL,
                "--network",
                "none",
                "--mount",
                f"type=volume,src={restore_volume},dst={DATA_MOUNT}",
                "--mount",
                f"type=volume,src={self.archive_volume},dst={ARCHIVE_MOUNT}",
                "--env",
                "POSTGRES_DB=sklegal",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            ]
        )
        try:
            _wait_ready(target)
            self.assertTrue(
                self._chain_verifies(target),
                "restored audit chain must verify clean",
            )
            head = self._chain_head(target)
            self.assertEqual(str(expected_tail["event_sequence"]), head.split(":")[0])
            self.assertIn(expected_tail["event_sha256"], head)
            if absent is not None:
                self.assertNotIn(absent["event_sha256"], head)
                gone = _psql_container(
                    target,
                    "postgres",
                    f"SELECT count(*) FROM sklegal_audit.events "
                    f"WHERE id = '{absent['event_id']}';",
                ).stdout.strip()
                self.assertEqual("0", gone)
            self._rls_isolates_beta(target, run_id)
            self.assertEqual(str(len(MIGRATION_FILES)), self._migration_count(target))
        finally:
            _run(["docker", "rm", "--force", target])
            _run(["docker", "volume", "rm", "--force", restore_volume])


def _switch_wal_and_wait_archive(container: str) -> str:
    """Switch WAL and block until the archiver stored the closed segment.

    The archive_command copies each closed segment into the shared
    archive volume asynchronously, so proceeding before the copy lands
    would make the restore clusters miss the WAL they must replay.
    Returns the archived segment filename.
    """

    segment = (
        _psql_container(
            container, "postgres", "SELECT pg_walfile_name(pg_switch_wal());"
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    for _ in range(240):
        listing = _run(
            ["docker", "exec", container, "ls", "-1", ARCHIVE_MOUNT], check=False
        )
        if segment in listing.stdout.split():
            return segment
        time.sleep(0.25)
    raise AssertionError(f"WAL segment {segment} never reached {ARCHIVE_MOUNT}")


def _wait_ready(container: str) -> None:
    consecutive_ready = 0
    for attempt in range(480):
        ready = _run(
            [
                "docker",
                "exec",
                container,
                "pg_isready",
                "--username",
                "postgres",
                "--dbname",
                "sklegal",
            ],
            check=False,
        )
        if ready.returncode == 0:
            consecutive_ready += 1
            if consecutive_ready == 8:
                return
        else:
            consecutive_ready = 0
        if attempt == 479:
            logs = subprocess.run(
                ["docker", "logs", "--tail", "60", container],
                capture_output=True,
                text=True,
                check=False,
            )
            raise RuntimeError(
                f"disposable PostgreSQL readiness timeout: {container}\n"
                f"container logs:\n{logs.stdout[-2000:]}{logs.stderr[-2000:]}"
            )
        time.sleep(0.25)


if __name__ == "__main__":
    unittest.main()
