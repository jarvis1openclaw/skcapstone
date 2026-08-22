"""Shared disposable PostgreSQL scaffolding for the persistence contract.

Every persistence contract aggregate class derives from
PersistenceContractBase, which starts one networkless disposable container
per process, proves the full migration cycle, and seeds the synthetic
scopes exactly as the single-class suite did before the split.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import time
import unittest
import uuid
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    ClassVar,
)
from uuid import UUID

from sklegal_persistence import (
    MAPPINGS,
    PersistenceMetadata,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_RUNNER = REPO_ROOT / "scripts" / "manage_migrations.py"
PROVISIONER = REPO_ROOT / "scripts" / "provision_postgres_principal.py"
RUNTIME_PROVISIONER = REPO_ROOT / "scripts" / "provision_postgres_runtime.py"
MIGRATION_ROOT = REPO_ROOT / "migrations"
MIGRATION_FILES = [
    entry["file"]
    for entry in json.loads(
        (MIGRATION_ROOT / "manifest.json").read_text(encoding="utf-8")
    )["migrations"]
]
MIGRATION_TOTAL = len(MIGRATION_FILES)
AUDIT_MIGRATION_STEPS = MIGRATION_TOTAL - MIGRATION_FILES.index(
    "0007_append_only_audit_outbox.sql"
)
CAPAUTH_MIGRATION_FILES = (
    "0008_capauth_state.sql",
    "0009_capauth_principal_snapshot.sql",
    "0010_principal_authentication_subject.sql",
    "0011_capauth_runtime_schema_usage.sql",
    "0012_capauth_runtime_type_usage.sql",
)
CAPAUTH_RUNTIME_ROLE = "sklegal_runtime"
CAPAUTH_RUNTIME_PRINCIPAL = "10000000-0000-4000-8000-000000000013"
CAPAUTH_RUNTIME_SUBJECT = "synthetic:service:capauth-runtime"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "persistence" / "synthetic-tenants.json"
PARITY = REPO_ROOT / "tests" / "fixtures" / "persistence" / "domain-table-parity.json"
POSTGRES_IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126"
)


class PersistenceContractBase(unittest.TestCase):
    """Shared disposable PostgreSQL runtime for the persistence contract.

    The container, migration evidence, and seeded scopes initialize once per
    process on first use and are shared by every aggregate subclass. The
    container is removed at process exit via atexit.
    """

    container: str
    fixture: dict[str, str]
    parity: dict[str, object]
    migration_evidence: tuple[str, str, str]
    migration_step_evidence: tuple[tuple[int, str, str], ...]
    unsafe_preflight: subprocess.CompletedProcess[str]
    unsafe_preflight_left_no_schema: str
    skmemory_container_id: str
    _shared_runtime_ready: ClassVar[bool] = False

    @classmethod
    def setUpClass(cls) -> None:
        cls = PersistenceContractBase
        if cls._shared_runtime_ready:
            return
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.parity = json.loads(PARITY.read_text(encoding="utf-8"))
        skmemory = subprocess.run(
            ["docker", "ps", "--quiet", "--filter", "name=^skmem-pg$"],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.skmemory_container_id = skmemory.stdout.strip()
        cls.container = f"sklegal-s102-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                cls.container,
                "--label",
                "com.sklegal.test-card=SKL-S1-02",
                "--label",
                "com.sklegal.test-card-capauth=SKL-S1-08",
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
                "--env",
                "POSTGRES_DB=sklegal",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        atexit.register(cls._remove_container)
        consecutive_ready = 0
        for attempt in range(240):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    cls.container,
                    "pg_isready",
                    "--username",
                    "postgres",
                    "--dbname",
                    "sklegal",
                ],
                capture_output=True,
                text=True,
            )
            if ready.returncode == 0:
                consecutive_ready += 1
                if consecutive_ready == 8:
                    break
            else:
                consecutive_ready = 0
            if attempt == 239:
                raise RuntimeError("disposable PostgreSQL readiness timeout")
            time.sleep(0.25)

        cls.unsafe_preflight = cls._migrate("up", user="postgres", check=False)
        cls.unsafe_preflight_left_no_schema = cls._psql(
            "postgres",
            "SELECT to_regnamespace('sklegal_migrations') IS NULL;",
        ).stdout.strip()
        cls._psql(
            "postgres",
            """
            CREATE ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT CREATE ON DATABASE sklegal TO sklegal_migrator;
            """,
        )
        subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(RUNTIME_PROVISIONER),
                "--docker-container",
                cls.container,
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        first_up = cls._migrate("up").stdout.strip()
        down = cls._migrate("down", "--steps", "all").stdout.strip()
        second_up = cls._migrate("up").stdout.strip()
        cls.migration_evidence = (first_up, down, second_up)
        step_evidence: list[tuple[int, str, str]] = []
        for steps in range(1, AUDIT_MIGRATION_STEPS + 1):
            reverted = cls._migrate("down", "--steps", str(steps)).stdout.strip()
            reapplied = cls._migrate("up").stdout.strip()
            step_evidence.append((steps, reverted, reapplied))
        cls.migration_step_evidence = tuple(step_evidence)
        cls._seed_scopes()
        cls._shared_runtime_ready = True

    @classmethod
    def _remove_container(cls) -> None:
        subprocess.run(
            ["docker", "rm", "--force", cls.container],
            check=False,
            capture_output=True,
            text=True,
        )
        remaining = subprocess.run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"name=^{cls.container}$",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if remaining.stdout.strip():
            raise AssertionError("disposable PostgreSQL container leaked")

    @classmethod
    def _psql_command(cls, user: str) -> list[str]:
        return [
            "docker",
            "exec",
            "-i",
            cls.container,
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "--dbname",
            "sklegal",
            "--tuples-only",
            "--no-align",
        ]

    @classmethod
    def _psql(
        cls, user: str, sql: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cls._psql_command(user),
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"psql failed for role {user}: {result.stderr.strip()}"
            )
        return result

    @classmethod
    def _capauth_execute(cls, sql: str, params: tuple[object, ...]) -> object:
        """Execute the driver-neutral adapter contract against disposable Postgres."""

        def literal(value: object) -> str:
            if isinstance(value, UUID):
                return f"'{value}'::uuid"
            if isinstance(value, datetime):
                return f"'{value.isoformat()}'::timestamptz"
            if isinstance(value, str):
                return "'" + value.replace("'", "''") + "'"
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                items = ",".join(literal(item) for item in value)
                return f"ARRAY[{items}]::sklegal_legal.sha256_digest[]"
            raise TypeError(f"unsupported CapAuth SQL parameter: {type(value)!r}")

        rendered = sql
        for param in params:
            rendered = rendered.replace("%s", literal(param), 1)
        if "%s" in rendered:
            raise ValueError("CapAuth SQL parameter count is incomplete")
        output = cls._psql(CAPAUTH_RUNTIME_ROLE, rendered).stdout.strip()
        if "prune_expired_capability_replay_reservations" in sql:
            return (int(output),)
        if "reserve_capability" in sql:
            return (output == "t",)
        return (output,)

    @classmethod
    def _psql_in_database(
        cls, database: str, user: str, sql: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        command = cls._psql_command(user)
        command[command.index("sklegal")] = database
        result = subprocess.run(
            command,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"psql failed for role {user} in {database}: {result.stderr.strip()}"
            )
        return result

    def _wait_for_sleep(
        self, application_name: str, process: subprocess.Popen[str]
    ) -> None:
        for _ in range(100):
            waiting = self._psql(
                "postgres",
                f"""
                SELECT count(*) FROM pg_stat_activity
                WHERE application_name = '{application_name}'
                  AND wait_event = 'PgSleep';
                """,
            )
            if waiting.stdout.strip() == "1":
                return
            if process.poll() is not None:
                _, stderr = process.communicate()
                self.fail(f"concurrent writer exited before sleep checkpoint: {stderr}")
            time.sleep(0.02)
        process.kill()
        self.fail(f"{application_name} did not reach deterministic sleep checkpoint")

    @staticmethod
    def _native_record(value: Any, *, key: str = "") -> Any:
        if isinstance(value, list):
            return [
                PersistenceContractBase._native_record(item, key=key) for item in value
            ]
        if isinstance(value, dict):
            return {
                item_key: PersistenceContractBase._native_record(
                    item_value, key=item_key
                )
                for item_key, item_value in value.items()
            }
        uuid_text_fields = {
            "id",
            "tenant_id",
            "matter_id",
            "subject_ref",
            "canonical_record_id",
        }
        uuid_text_exclusions = {
            "check_id",
            "correlation_id",
            "external_receipt_id",
            "idempotency_key",
            "legacy_id",
        }
        if (
            isinstance(value, str)
            and (key in uuid_text_fields or key.endswith("_id"))
            and key not in uuid_text_exclusions
        ):
            return UUID(value)
        if isinstance(value, str) and (
            key.endswith("_at")
            or key in {"valid_from", "valid_to", "system_from", "system_to"}
        ):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @staticmethod
    def _canonical_container(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: PersistenceContractBase._canonical_container(item)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(
                PersistenceContractBase._canonical_container(item) for item in value
            )
        return value

    @staticmethod
    def _normalize_controlled_entity_outputs(expected: Any, actual: Any) -> Any:
        """Substitute only fields declared as database-owned writer outputs."""

        if isinstance(expected, Mapping) and isinstance(actual, Mapping):
            normalized = {
                key: PersistenceContractBase._normalize_controlled_entity_outputs(
                    value, actual[key]
                )
                for key, value in expected.items()
            }
            for field in ("version", "created_at", "updated_at"):
                if field in normalized:
                    normalized[field] = actual[field]
            if "outcome" in expected and "validator_principal_id" in expected:
                normalized["validated_at"] = actual["validated_at"]
            if "status" in expected and "reviewer_principal_id" in expected:
                normalized["decided_at"] = actual["decided_at"]
                normalized["revoked_at"] = actual["revoked_at"]
            if "step" in expected and "correlation_id" in expected:
                normalized["id"] = actual["id"]
                normalized["occurred_at"] = actual["occurred_at"]
            return normalized
        if (
            isinstance(expected, Sequence)
            and not isinstance(expected, (str, bytes))
            and isinstance(actual, Sequence)
            and not isinstance(actual, (str, bytes))
        ):
            if len(expected) != len(actual):
                return expected
            return type(expected)(
                PersistenceContractBase._normalize_controlled_entity_outputs(
                    expected_item, actual_item
                )
                for expected_item, actual_item in zip(expected, actual, strict=True)
            )
        return expected

    @staticmethod
    def _normalize_controlled_metadata_outputs(
        expected: PersistenceMetadata, actual: PersistenceMetadata
    ) -> PersistenceMetadata:
        """Normalize audit/history values while retaining relation evidence times."""

        expected_scalar = dict(expected.scalar)
        actual_scalar = dict(actual.scalar)
        for field in ("system_from", "system_to"):
            if field in expected_scalar:
                expected_scalar[field] = actual_scalar[field]
        if "captured_at" in actual_scalar:
            expected_scalar["captured_at"] = actual_scalar["captured_at"]

        expected_relations: dict[str, tuple[dict[str, Any], ...]] = {}
        for relation_name, expected_rows in expected.relations.items():
            actual_rows = actual.relations[relation_name]
            normalized_rows: list[dict[str, Any]] = []
            for expected_row, actual_row in zip(
                expected_rows, actual_rows, strict=True
            ):
                normalized_row = dict(expected_row)
                if "captured_at" in actual_row:
                    normalized_row["captured_at"] = actual_row["captured_at"]
                for field in ("created_at", "relation_created_at"):
                    if field in normalized_row:
                        normalized_row[field] = actual_row[field]
                if relation_name in {"source_reference", "source_references"}:
                    for field in ("version", "updated_at"):
                        normalized_row[field] = actual_row[field]
                if (
                    "claim_id" in expected_row
                    and "theory_kind" in expected_row
                    and "theory_id" not in expected_row
                ):
                    normalized_row["created_at"] = actual_row["created_at"]
                if "nested_metadata" in expected_row:
                    normalized_row["nested_metadata"] = (
                        PersistenceContractBase._normalize_controlled_metadata_outputs(
                            expected_row["nested_metadata"],
                            actual_row["nested_metadata"],
                        )
                    )
                normalized_rows.append(normalized_row)
            expected_relations[relation_name] = tuple(normalized_rows)
        return PersistenceMetadata(
            scalar=expected_scalar,
            relations=expected_relations,
        )

    def _json_rows(
        self,
        role: str,
        table: str,
        where: str = "TRUE",
        order_by: str = "1",
        columns: str = "*",
    ) -> list[dict[str, Any]]:
        result = self._psql(
            role,
            f"""
            SELECT COALESCE(jsonb_agg(to_jsonb(selected) ORDER BY {order_by}), '[]')
            FROM (SELECT {columns} FROM {table} WHERE {where}) AS selected;
            """,
        )
        payload = json.loads(result.stdout)
        return [self._native_record(item) for item in payload]

    def _append_audit_event(
        self,
        *,
        event_id: str,
        run_id: str,
        correlation_id: str,
        span_id: str,
        boundary: str,
        action: str,
        attributes: str = "{}",
        resource_kind: str = "matter",
        occurred_at: str = "2026-08-20T12:00:00Z",
        tenant_scoped: bool = False,
        include_decision_references: bool = True,
        time_zone: str | None = None,
        role: str = "sklegal_test_alpha_one",
        tenant_id: str | None = None,
        matter_id: str | None = None,
        principal_id: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        value = self.fixture
        tenant = tenant_id or value["tenant_alpha"]
        matter = matter_id or value["matter_alpha_one"]
        principal = principal_id or value["principal_alpha_one"]
        matter_sql = "NULL" if tenant_scoped else f"'{matter}'"
        resource_sql = "NULL" if tenant_scoped else f"'{matter}'"
        authorization_sql = (
            "'a6000000-0000-4000-8000-000000000001'"
            if include_decision_references
            else "NULL"
        )
        policy_sql = (
            "'a6000000-0000-4000-8000-000000000002'"
            if include_decision_references
            else "NULL"
        )
        time_zone_setup = f"SET TIME ZONE '{time_zone}';" if time_zone else ""
        return self._psql(
            role,
            f"""
            {time_zone_setup}
            SELECT sklegal_audit.append_event(
                '{event_id}', '{tenant}', {matter_sql}, '{principal}',
                '{run_id}', '{correlation_id}', '{"1" * 32}', '{span_id}', '01',
                '{boundary}', '{action}', '{resource_kind}', {resource_sql},
                {authorization_sql}, {policy_sql},
                'allow', 'allow', '{occurred_at}',
                '{attributes}'::jsonb
            )::text;
            """,
            check=check,
        )

    def _normalized_relations(
        self, role: str, entity_name: str, row: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        tenant = row.get("tenant_id")
        matter = row.get("matter_id")
        identifier = row.get("id")
        scope = f"tenant_id = '{tenant}' AND matter_id = '{matter}'"

        def rows(
            table: str,
            predicate: str,
            order: str = "1",
            columns: str = "*",
        ) -> list[dict[str, Any]]:
            return self._json_rows(role, table, predicate, order, columns)

        def source(reference_id: object) -> list[dict[str, Any]]:
            if reference_id is None:
                return []
            return rows(
                "sklegal_legal.source_references",
                f"{scope} AND id = '{reference_id}'",
            )

        relations: dict[str, list[dict[str, Any]]] = {}
        if entity_name in {
            "Party",
            "PartyRole",
            "Forum",
            "MatterEvent",
            "FactAssertion",
            "EvidenceItem",
            "CustodyEvent",
            "Authority",
        }:
            relations["source_reference"] = source(row.get("source_reference_id"))
        if entity_name in {"Matter", "MatterEvent"}:
            kind = "matter" if entity_name == "Matter" else "matter_event"
            relations["aliases"] = rows(
                "sklegal_legal.legacy_aliases",
                f"{scope} AND canonical_record_kind = '{kind}' "
                f"AND canonical_record_id = '{identifier}'",
                "legacy_id",
            )
        if entity_name == "Transaction":
            relations["party_roles"] = rows(
                "sklegal_legal.transaction_party_roles",
                f"{scope} AND transaction_id = '{identifier}'",
                "party_role_id",
            )
            relations["source_references"] = rows(
                "sklegal_legal.source_references AS source_reference "
                "JOIN sklegal_legal.transaction_source_references AS link "
                "ON link.tenant_id = source_reference.tenant_id "
                "AND link.matter_id = source_reference.matter_id "
                "AND link.source_reference_id = source_reference.id",
                f"link.tenant_id = '{tenant}' AND link.matter_id = '{matter}' "
                f"AND link.transaction_id = '{identifier}'",
                "id",
                "source_reference.*, link.transaction_id, "
                "link.created_at AS relation_created_at",
            )
        if entity_name == "TensionGroup":
            relations["assertions"] = rows(
                "sklegal_legal.tension_assertions",
                f"{scope} AND tension_group_id = '{identifier}'",
                "assertion_id",
            )
        if entity_name in {"Claim", "Defense"}:
            theory_kind = entity_name.casefold()
            relations["elements"] = rows(
                "sklegal_legal.elements",
                f"{scope} AND theory_kind = '{theory_kind}' "
                f"AND claim_id = '{identifier}'",
                "id",
                "id, tenant_id, matter_id, theory_kind, claim_id, created_at",
            )
            relations["evidence"] = rows(
                "sklegal_legal.theory_evidence",
                f"{scope} AND theory_kind = '{theory_kind}' "
                f"AND theory_id = '{identifier}'",
                "evidence_item_id",
            )
            relations["authorities"] = rows(
                "sklegal_legal.theory_authorities",
                f"{scope} AND theory_kind = '{theory_kind}' "
                f"AND theory_id = '{identifier}'",
                "authority_id",
            )
        if entity_name == "Element":
            relations["evidence"] = rows(
                "sklegal_legal.element_evidence",
                f"{scope} AND element_id = '{identifier}'",
                "evidence_item_id",
            )
        if entity_name == "Remedy":
            relations["authorities"] = rows(
                "sklegal_legal.remedy_authorities",
                f"{scope} AND remedy_id = '{identifier}'",
                "authority_id",
            )
        if entity_name == "DeadlineCalculation":
            relations["source_references"] = rows(
                "sklegal_legal.source_references AS source_reference "
                "JOIN sklegal_legal.deadline_calculation_sources AS link "
                "ON link.tenant_id = source_reference.tenant_id "
                "AND link.matter_id = source_reference.matter_id "
                "AND link.source_reference_id = source_reference.id",
                f"link.tenant_id = '{tenant}' AND link.matter_id = '{matter}' "
                f"AND link.calculation_id = '{identifier}'",
                "id",
                "source_reference.*, link.calculation_id, "
                "link.created_at AS relation_created_at",
            )
        if entity_name == "Communication":
            relations["participants"] = rows(
                "sklegal_legal.communication_participants",
                f"{scope} AND communication_id = '{identifier}'",
                "party_id",
            )
        if entity_name == "ValidationResult":
            relations["checks"] = rows(
                "sklegal_legal.validation_checks",
                f"{scope} AND validation_id = '{identifier}'",
                "check_id",
            )
        if entity_name == "Execution":
            nested_contracts = (
                (
                    "validation_result",
                    "ValidationResult",
                    row.get("validation_result_id"),
                ),
                ("approval", "Approval", row.get("approval_id")),
                ("receipt", "ExecutionReceipt", None),
            )
            for relation_name, nested_name, nested_id in nested_contracts:
                relation_spec = next(
                    spec
                    for spec in MAPPINGS["Execution"].relations
                    if spec.relation == relation_name
                )
                nested_table = (
                    relation_spec.read_relation or MAPPINGS[nested_name].table
                )
                predicate = f"{scope} AND execution_id = '{identifier}'"
                if nested_name == "ValidationResult":
                    if nested_id is None:
                        relations[relation_name] = []
                        continue
                    predicate = f"{scope} AND id = '{nested_id}'"
                elif nested_name == "Approval":
                    approval_version = row.get("approval_version")
                    if nested_id is None and approval_version is None:
                        relations[relation_name] = []
                        continue
                    if nested_id is None or approval_version is None:
                        raise AssertionError(
                            "execution approval snapshot binding is incomplete"
                        )
                    predicate = (
                        f"{scope} AND approval_id = '{nested_id}' "
                        f"AND approval_version = {approval_version}"
                    )
                nested_rows = rows(
                    nested_table,
                    predicate,
                    "approval_id, approval_version"
                    if nested_name == "Approval"
                    else "id",
                )
                relations[relation_name] = [
                    {
                        "row": nested_row,
                        "relations": self._normalized_relations(
                            role, nested_name, nested_row
                        ),
                    }
                    for nested_row in nested_rows
                ]
            event_rows = rows(
                MAPPINGS["ExecutionEvent"].table,
                f"{scope} AND execution_id = '{identifier}'",
                "sequence_no",
            )
            relations["events"] = [
                {
                    "row": event_row,
                    "relations": self._normalized_relations(
                        role, "ExecutionEvent", event_row
                    ),
                }
                for event_row in event_rows
            ]
        return relations

    @classmethod
    def _migrate(
        cls,
        *arguments: str,
        user: str = "sklegal_migrator",
        root: Path = MIGRATION_ROOT,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(MIGRATION_RUNNER),
                *arguments,
                "--root",
                str(root),
                "--docker-container",
                cls.container,
                "--user",
                user,
            ],
            cwd=REPO_ROOT,
            check=check,
            capture_output=True,
            text=True,
        )

    @classmethod
    def _migrate_database(
        cls, database: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(MIGRATION_RUNNER),
                "up",
                "--root",
                str(MIGRATION_ROOT),
                "--docker-container",
                cls.container,
                "--database",
                database,
                "--user",
                "sklegal_migrator",
            ],
            cwd=REPO_ROOT,
            check=check,
            capture_output=True,
            text=True,
        )

    @classmethod
    def _provision(cls, role: str, tenant: str, principal: str) -> None:
        result = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                cls.container,
                "--runtime-role",
                role,
                "--tenant-id",
                tenant,
                "--principal-id",
                principal,
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr.strip())

    @classmethod
    def _seed_scopes(cls) -> None:
        value = cls.fixture
        cls._psql(
            "postgres",
            f"""
            CREATE ROLE sklegal_test_alpha_one LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_test_alpha_two LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_test_beta_one LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_test_unbound LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE ROLE sklegal_test_bypass LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT BYPASSRLS;
            GRANT CREATE ON SCHEMA sklegal_legal TO sklegal_test_alpha_one;

            INSERT INTO sklegal_identity.tenants (id, tenant_id, slug, name, status)
            VALUES
                ('{value["tenant_alpha"]}', '{value["tenant_alpha"]}',
                 'synthetic-alpha', 'Synthetic Alpha Law', 'active'),
                ('{value["tenant_beta"]}', '{value["tenant_beta"]}',
                 'synthetic-beta', 'Synthetic Beta Law', 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name)
            VALUES
                ('{value["principal_alpha_one"]}', '{value["tenant_alpha"]}',
                 'human', 'Synthetic Alpha One'),
                ('{value["principal_alpha_two"]}', '{value["tenant_alpha"]}',
                 'human', 'Synthetic Alpha Two'),
                ('{value["principal_beta_one"]}', '{value["tenant_beta"]}',
                 'human', 'Synthetic Beta One');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name,
                 authentication_subject)
            VALUES
                ('{CAPAUTH_RUNTIME_PRINCIPAL}', '{value["tenant_alpha"]}',
                 'service', 'Synthetic CapAuth Runtime',
                 '{CAPAUTH_RUNTIME_SUBJECT}');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES
                ('{value["tenant_alpha"]}', '{value["principal_alpha_one"]}', 'member'),
                ('{value["tenant_alpha"]}', '{value["principal_alpha_two"]}', 'member'),
                ('{value["tenant_beta"]}', '{value["principal_beta_one"]}', 'member');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES
                ('{value["tenant_alpha"]}', '{CAPAUTH_RUNTIME_PRINCIPAL}',
                 'member');
            INSERT INTO sklegal_identity.database_role_bindings
                (database_role, tenant_id, principal_id)
            VALUES
                ('{CAPAUTH_RUNTIME_ROLE}', '{value["tenant_alpha"]}',
                 '{CAPAUTH_RUNTIME_PRINCIPAL}');
            INSERT INTO sklegal_legal.clients
                (id, tenant_id, display_name, client_kind, status)
            VALUES
                ('{value["client_alpha"]}', '{value["tenant_alpha"]}',
                 'Synthetic Alpha Client', 'company', 'active'),
                ('{value["client_beta"]}', '{value["tenant_beta"]}',
                 'Synthetic Beta Client', 'company', 'active');
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, status, valid_from)
            VALUES
                ('{value["engagement_alpha"]}', '{value["tenant_alpha"]}',
                 '{value["client_alpha"]}', 'Synthetic Alpha Engagement',
                 'Synthetic scope', 'active', '2026-01-01T00:00:00Z'),
                ('{value["engagement_beta"]}', '{value["tenant_beta"]}',
                 '{value["client_beta"]}', 'Synthetic Beta Engagement',
                 'Synthetic scope', 'active', '2026-01-01T00:00:00Z');
            INSERT INTO sklegal_legal.matters
                (id, tenant_id, matter_id, client_id, engagement_id, title,
                 summary, status, opened_at)
            VALUES
                ('{value["matter_alpha_one"]}', '{value["tenant_alpha"]}',
                 '{value["matter_alpha_one"]}', '{value["client_alpha"]}',
                 '{value["engagement_alpha"]}', 'Synthetic Alpha Matter One',
                 'Synthetic matter only.', 'open', '2026-01-02T00:00:00Z'),
                ('{value["matter_alpha_two"]}', '{value["tenant_alpha"]}',
                 '{value["matter_alpha_two"]}', '{value["client_alpha"]}',
                 '{value["engagement_alpha"]}', 'Synthetic Alpha Matter Two',
                 'Synthetic matter only.', 'open', '2026-01-03T00:00:00Z'),
                ('{value["matter_beta_one"]}', '{value["tenant_beta"]}',
                 '{value["matter_beta_one"]}', '{value["client_beta"]}',
                 '{value["engagement_beta"]}', 'Synthetic Beta Matter One',
                 'Synthetic matter only.', 'open', '2026-01-04T00:00:00Z');
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES
                ('{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                 '{value["principal_alpha_one"]}', 'member'),
                ('{value["tenant_alpha"]}', '{value["matter_alpha_two"]}',
                 '{value["principal_alpha_two"]}', 'member'),
                ('{value["tenant_beta"]}', '{value["matter_beta_one"]}',
                 '{value["principal_beta_one"]}', 'member');
            """,
        )
        cls._provision(
            "sklegal_test_alpha_one",
            value["tenant_alpha"],
            value["principal_alpha_one"],
        )
        cls._provision(
            "sklegal_test_alpha_two",
            value["tenant_alpha"],
            value["principal_alpha_two"],
        )
        cls._provision(
            "sklegal_test_beta_one",
            value["tenant_beta"],
            value["principal_beta_one"],
        )
