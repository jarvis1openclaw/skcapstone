from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import sklegal_domain
from pydantic import ValidationError
from sklegal_audit import (
    AuditEventDraft,
    DurableAuditEvent,
    PostgresAuditRepository,
    recompute_event_sha256,
    verify_event_chain,
)
from sklegal_audit.ledger import _canonical_timestamp
from sklegal_domain import EffectiveInterval, Forum
from sklegal_domain.base import DomainEntity
from sklegal_persistence import (
    MAPPINGS,
    MappingContractError,
    PersistenceMetadata,
    decompose,
    reconstruct,
    reconstruct_with_metadata,
    validate_mapping_contract,
)

from tests.support.fresh_persistence_contract import build_fresh_contract
from tests.support.persistence_write_adapter import (
    auxiliary_statements,
    create_communication_statement,
    insert_statement,
    relation_statements,
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
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "persistence" / "synthetic-tenants.json"
PARITY = REPO_ROOT / "tests" / "fixtures" / "persistence" / "domain-table-parity.json"
POSTGRES_IMAGE = (
    "postgres:17.7-alpine@sha256:"
    "a6d31f853205ce20d399df4e33a0b4c715672f232f4ee7440499747e6e02c126"
)


class PersistenceContractTests(unittest.TestCase):
    container: str
    fixture: dict[str, str]
    parity: dict[str, object]
    migration_evidence: tuple[str, str, str]
    migration_step_evidence: tuple[tuple[int, str, str], ...]
    unsafe_preflight: subprocess.CompletedProcess[str]
    unsafe_preflight_left_no_schema: str
    skmemory_container_id: str

    @classmethod
    def setUpClass(cls) -> None:
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
        cls.addClassCleanup(cls._remove_container)
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
        for steps in range(1, 8):
            reverted = cls._migrate("down", "--steps", str(steps)).stdout.strip()
            reapplied = cls._migrate("up").stdout.strip()
            step_evidence.append((steps, reverted, reapplied))
        cls.migration_step_evidence = tuple(step_evidence)
        cls._seed_scopes()

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
                PersistenceContractTests._native_record(item, key=key) for item in value
            ]
        if isinstance(value, dict):
            return {
                item_key: PersistenceContractTests._native_record(
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
                key: PersistenceContractTests._canonical_container(item)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(
                PersistenceContractTests._canonical_container(item) for item in value
            )
        return value

    @staticmethod
    def _normalize_controlled_entity_outputs(expected: Any, actual: Any) -> Any:
        """Substitute only fields declared as database-owned writer outputs."""

        if isinstance(expected, Mapping) and isinstance(actual, Mapping):
            normalized = {
                key: PersistenceContractTests._normalize_controlled_entity_outputs(
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
                PersistenceContractTests._normalize_controlled_entity_outputs(
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
                        PersistenceContractTests._normalize_controlled_metadata_outputs(
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
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES
                ('{value["tenant_alpha"]}', '{value["principal_alpha_one"]}', 'member'),
                ('{value["tenant_alpha"]}', '{value["principal_alpha_two"]}', 'member'),
                ('{value["tenant_beta"]}', '{value["principal_beta_one"]}', 'member');
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

    def test_00_migrations_preflight_up_down_up_and_owners(self) -> None:
        self.assertNotEqual(0, self.unsafe_preflight.returncode)
        self.assertIn("exact sklegal_migrator", self.unsafe_preflight.stderr)
        self.assertEqual("t", self.unsafe_preflight_left_no_schema)
        self.assertEqual(
            (
                f"applied {MIGRATION_TOTAL} migration(s)",
                f"reverted {MIGRATION_TOTAL} migration(s)",
                f"applied {MIGRATION_TOTAL} migration(s)",
            ),
            self.migration_evidence,
        )
        self.assertEqual(
            f"migration status: {MIGRATION_TOTAL}/{MIGRATION_TOTAL} applied",
            self._migrate("status").stdout.strip(),
        )
        self.assertEqual(
            tuple(
                (
                    steps,
                    f"reverted {steps} migration(s)",
                    f"applied {steps} migration(s)",
                )
                for steps in range(1, 8)
            ),
            self.migration_step_evidence,
        )
        with tempfile.TemporaryDirectory(prefix="sklegal-s102-migration-") as directory:
            synthetic_root = Path(directory) / "migrations"
            shutil.copytree(MIGRATION_ROOT, synthetic_root)
            migration_name = (
                f"{MIGRATION_TOTAL + 1:04d}_intentionally_failing_synthetic_probe.sql"
            )
            migration_path = synthetic_root / migration_name
            migration_path.write_text(
                "-- sklegal:up\n"
                "CREATE TABLE sklegal_legal.synthetic_rollback_probe (id integer);\n"
                "SELECT 1 / 0;\n"
                "-- sklegal:down\n"
                "DROP TABLE sklegal_legal.synthetic_rollback_probe;\n",
                encoding="utf-8",
            )
            manifest_path = synthetic_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["migrations"].append(
                {
                    "file": migration_name,
                    "sha256": hashlib.sha256(migration_path.read_bytes()).hexdigest(),
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            failure = self._migrate("up", root=synthetic_root, check=False)
            self.assertNotEqual(0, failure.returncode)
            self.assertIn("division by zero", failure.stderr)
        rollback_state = self._psql(
            "postgres",
            f"""
            SELECT to_regclass('sklegal_legal.synthetic_rollback_probe') IS NULL,
                   count(*) = {MIGRATION_TOTAL}
            FROM sklegal_migrations.schema_migrations;
            """,
        )
        self.assertEqual("t|t", rollback_state.stdout.strip())
        self.assertEqual(
            f"migration status: {MIGRATION_TOTAL}/{MIGRATION_TOTAL} applied",
            self._migrate("status").stdout.strip(),
        )
        owners = self._psql(
            "postgres",
            """
            SELECT count(*) FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND relation.relowner <> 'sklegal_migrator'::regrole;
            """,
        )
        self.assertEqual("0", owners.stdout.strip())

    def test_00_migrator_exact_profile_rejects_every_drift_before_bootstrap(
        self,
    ) -> None:
        database = "sklegal_migrator_profile_probe"
        graph_role = "sklegal_migrator_graph_probe"
        self._psql(
            "postgres",
            f"""
            DROP DATABASE IF EXISTS {database} WITH (FORCE);
            CREATE DATABASE {database};
            CREATE ROLE {graph_role} NOLOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            """,
        )
        self.addCleanup(
            self._psql,
            "postgres",
            f"DROP DATABASE IF EXISTS {database} WITH (FORCE);",
        )
        self.addCleanup(
            self._psql,
            "postgres",
            f"""
            ALTER ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            REVOKE {graph_role} FROM sklegal_migrator;
            REVOKE sklegal_migrator FROM {graph_role};
            DROP ROLE IF EXISTS {graph_role};
            """,
        )
        exact_profile = (
            "ALTER ROLE sklegal_migrator LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;"
        )
        attribute_drifts = (
            ("NOLOGIN", "ALTER ROLE sklegal_migrator NOLOGIN;"),
            ("INHERIT", "ALTER ROLE sklegal_migrator INHERIT;"),
            ("SUPERUSER", "ALTER ROLE sklegal_migrator SUPERUSER;"),
            ("BYPASSRLS", "ALTER ROLE sklegal_migrator BYPASSRLS;"),
            ("CREATEROLE", "ALTER ROLE sklegal_migrator CREATEROLE;"),
            ("CREATEDB", "ALTER ROLE sklegal_migrator CREATEDB;"),
            ("REPLICATION", "ALTER ROLE sklegal_migrator REPLICATION;"),
        )
        for label, mutation in attribute_drifts:
            with self.subTest(profile_drift=label):
                try:
                    self._psql("postgres", mutation)
                    result = self._migrate_database(database, check=False)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(
                        "t",
                        self._psql_in_database(
                            database,
                            "postgres",
                            "SELECT to_regnamespace('sklegal_migrations') IS NULL;",
                        ).stdout.strip(),
                    )
                finally:
                    self._psql("postgres", exact_profile)

        membership_drifts = (
            (
                "migrator_is_member",
                f"GRANT {graph_role} TO sklegal_migrator;",
                f"REVOKE {graph_role} FROM sklegal_migrator;",
            ),
            (
                "migrator_is_granted",
                f"GRANT sklegal_migrator TO {graph_role};",
                f"REVOKE sklegal_migrator FROM {graph_role};",
            ),
        )
        for label, mutation, restoration in membership_drifts:
            with self.subTest(profile_drift=label):
                try:
                    self._psql("postgres", mutation)
                    result = self._migrate_database(database, check=False)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("no role membership edge", result.stderr)
                    self.assertEqual(
                        "t",
                        self._psql_in_database(
                            database,
                            "postgres",
                            "SELECT to_regnamespace('sklegal_migrations') IS NULL;",
                        ).stdout.strip(),
                    )
                finally:
                    self._psql("postgres", restoration)
        self.assertEqual(
            "t|t|f|f|f|f|f|f|f",
            self._psql(
                "postgres",
                """
                SELECT rolname = 'sklegal_migrator', rolcanlogin, rolinherit,
                       rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                       rolreplication,
                       EXISTS (
                           SELECT 1 FROM pg_auth_members
                           WHERE member = role_record.oid
                              OR roleid = role_record.oid
                       )
                FROM pg_roles AS role_record
                WHERE rolname = 'sklegal_migrator';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            f"migration status: {MIGRATION_TOTAL}/{MIGRATION_TOTAL} applied",
            self._migrate("status").stdout.strip(),
        )

    def test_01_audit_timestamp_domain_rejects_without_side_effects(self) -> None:
        value = self.fixture
        before = self._psql(
            "postgres",
            """
            SELECT (SELECT count(*) FROM sklegal_audit.events)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.outbox)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.rollback_guard)::text;
            """,
        )
        self.assertEqual("0:0:0", before.stdout.strip())

        for label, occurred_at in (
            ("bc", "0001-01-02 03:04:05.123456 BC"),
            ("year-10000", "10000-01-02 03:04:05.123456 AD"),
        ):
            denied = self._append_audit_event(
                event_id=(
                    "a6700000-0000-4000-8000-000000000001"
                    if label == "bc"
                    else "a6700000-0000-4000-8000-000000000002"
                ),
                run_id="a6700000-0000-4000-8000-000000000010",
                correlation_id="a6700000-0000-4000-8000-000000000020",
                span_id=f"{1 if label == 'bc' else 2:016x}",
                boundary="api",
                action=f"audit.synthetic.timestamp-{label}",
                occurred_at=occurred_at,
                check=False,
            )
            with self.subTest(append=label):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("canonical audit timestamp", denied.stderr)

        for label, expression in (
            ("bc", "'0001-01-02 03:04:05.123456 BC'::timestamptz"),
            ("year-10000", "'10000-01-02 03:04:05.123456 AD'::timestamptz"),
            ("infinity", "'infinity'::timestamptz"),
            ("negative-infinity", "'-infinity'::timestamptz"),
        ):
            denied = self._psql(
                "postgres",
                f"SELECT sklegal_audit.canonical_timestamp({expression});",
                check=False,
            )
            with self.subTest(helper=label):
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("canonical audit timestamp", denied.stderr)

        after = self._psql(
            "postgres",
            f"""
            SELECT (SELECT count(*) FROM sklegal_audit.events)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.outbox)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.rollback_guard)::text || ':' ||
                   (SELECT count(*) FROM sklegal_audit.events
                    WHERE tenant_id = '{value["tenant_alpha"]}'
                      AND id IN (
                          'a6700000-0000-4000-8000-000000000001',
                          'a6700000-0000-4000-8000-000000000002'
                      ))::text;
            """,
        )
        self.assertEqual("0:0:0:0", after.stdout.strip())

    def test_01_information_barrier_snapshot_is_scoped_sanitized_and_sealed(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        other_matter = value["matter_alpha_two"]
        principal = value["principal_alpha_one"]
        other_principal = value["principal_alpha_two"]
        source_one = "93000000-0000-4000-8000-000000000001"
        source_two = "93000000-0000-4000-8000-000000000002"
        party_one = "93000000-0000-4000-8001-000000000001"
        party_two = "93000000-0000-4000-8001-000000000002"
        normalization_one = "93000000-0000-4000-8002-000000000001"
        normalization_two = "93000000-0000-4000-8002-000000000002"
        association = "93000000-0000-4000-8003-000000000001"
        conflict_check = "93000000-0000-4000-8004-000000000001"
        conflict_match = "93000000-0000-4000-8005-000000000001"
        conflict_decision = "93000000-0000-4000-8006-000000000001"
        conflict_hold = "93000000-0000-4000-8007-000000000001"
        classification = "93000000-0000-4000-8008-000000000001"
        retention = "93000000-0000-4000-8009-000000000001"
        retention_successor = "93000000-0000-4000-8009-000000000002"
        legal_hold = "93000000-0000-4000-8010-000000000001"
        policy_state = "93000000-0000-4000-8011-000000000001"
        policy_state_successor = "93000000-0000-4000-8011-000000000002"
        policy_state_after_release = "93000000-0000-4000-8011-000000000003"
        material = "93000000-0000-4000-8012-000000000001"
        normalized_digest = "d" * 64
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES
                ('{source_one}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"a" * 64}', 'synthetic:policy-one',
                 '2026-08-20T06:00:00Z'),
                ('{source_two}', '{tenant}', '{other_matter}', 'synthetic', 'v1',
                 '{"b" * 64}', 'synthetic:policy-two',
                 '2026-08-20T06:00:00Z');
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{party_one}', '{tenant}', '{matter}', 'Synthetic Policy One',
                 'company', '{source_one}'),
                ('{party_two}', '{tenant}', '{other_matter}',
                 'Synthetic Policy Two', 'company', '{source_two}');
            INSERT INTO sklegal_legal.party_normalizations
                (id, policy_change_id, tenant_id, matter_id, party_id, party_kind,
                 normalized_identity_digest, normalization_version,
                 reviewed_by_principal_id, reviewed_at)
            VALUES
                ('{normalization_one}', 999999, '{tenant}', '{matter}', '{party_one}',
                 'company', '{normalized_digest}',
                 'sklegal-party-normalization/v1', '{principal}',
                 '2026-08-20T06:01:00Z'),
                ('{normalization_two}', 999999, '{tenant}', '{other_matter}',
                 '{party_two}',
                 'company', '{normalized_digest}',
                 'sklegal-party-normalization/v1', '{other_principal}',
                 '2026-08-20T06:01:00Z');
            INSERT INTO sklegal_legal.party_associations
                (id, tenant_id, matter_id, party_id, relationship, active,
                 valid_from, source_reference_id)
            VALUES
                ('{association}', '{tenant}', '{other_matter}', '{party_two}',
                 'adverse_party', true, '2026-08-20T06:00:00Z', '{source_two}');
            INSERT INTO sklegal_legal.conflict_checks
                (id, tenant_id, matter_id, checked_by_principal_id, checked_at,
                 normalization_version, result, complete)
            VALUES
                ('{conflict_check}', '{tenant}', '{matter}', '{principal}',
                 '2026-08-20T06:02:00Z', 'sklegal-party-normalization/v1',
                 'hold', true);
            INSERT INTO sklegal_legal.conflict_matches
                (id, tenant_id, matter_id, conflict_check_id, candidate_party_id,
                 existing_matter_id, association_id,
                 normalized_identity_digest, collision_kind)
            VALUES
                ('{conflict_match}', '{tenant}', '{matter}', '{conflict_check}',
                 '{party_one}', '{other_matter}', '{association}',
                 '{normalized_digest}', 'exact_normalized_name');
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at)
            VALUES
                ('{conflict_decision}', '{tenant}', '{matter}', '{conflict_check}',
                 'hold', '{principal}', '2026-08-20T06:03:00Z');
            INSERT INTO sklegal_legal.conflict_holds
                (id, tenant_id, matter_id, conflict_decision_id, reason_code,
                 effective_from)
            VALUES
                ('{conflict_hold}', '{tenant}', '{matter}', '{conflict_decision}',
                 'adverse_party_collision', '2026-08-20T06:03:00Z');
            INSERT INTO sklegal_legal.material_classifications
                (id, tenant_id, matter_id, material_id, material_version,
                 source_kind, source_id, classification,
                 classified_by_principal_id, classified_at)
            VALUES
                ('{classification}', '{tenant}', '{matter}', '{material}', 1,
                 'material', '{material}', 'confidential', '{principal}',
                 '2026-08-20T06:04:00Z');
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 effective_to, decided_by_principal_id)
            VALUES
                ('{retention}', '{tenant}', '{matter}', 30,
                 '2026-08-20T06:00:00Z', '2026-08-20T07:00:00Z',
                 '{principal}');
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope,
                 issued_by_principal_id, effective_from)
            VALUES
                ('{legal_hold}', '{tenant}', '{matter}', 'active', 'matter',
                 '{principal}', '2026-08-20T06:05:00Z');
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id,
                 conflict_state_complete, wall_state_complete,
                 classification_state_complete, legal_hold_state_complete,
                 ownership_resolved, pending_export, preservation_required,
                 recorded_by_principal_id, recorded_at)
            VALUES
                ('{policy_state}', '{tenant}', '{matter}', '{conflict_decision}',
                 '{retention}',
                 true, true, true, true, true, false, false, '{principal}',
                 '2026-08-20T06:06:00Z');
            """,
        )
        direct = self._psql(
            "sklegal_test_alpha_one",
            "SELECT count(*) FROM sklegal_legal.conflict_matches;",
        )
        self.assertEqual("0", direct.stdout.strip())
        self.assertEqual(
            "t",
            self._psql(
                "postgres",
                f"""
                SELECT pg_catalog.bool_and(policy_change_id <> 999999)
                FROM sklegal_legal.party_normalizations
                WHERE tenant_id = '{tenant}';
                """,
            ).stdout.strip(),
        )
        snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT snapshot->>'policy_revision' ~ '^[0-9a-f]{{64}}$',
                   snapshot#>>'{{conflict_decision,disposition}}',
                   snapshot#>>'{{classification_sources,0,classification}}',
                   snapshot#>>'{{legal_holds,0,status}}',
                   snapshot->>'profile_owner_principal_id',
                   snapshot ? 'content', snapshot ? 'display_name'
            FROM (
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}'
                ) AS snapshot
            ) AS policy;
            """,
        )
        self.assertEqual(
            "t|hold|confidential|active||f|f",
            snapshot.stdout.strip(),
        )
        initial_revision = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_legal.material_policy_snapshot(
                '{tenant}', '{matter}', '{material}', 1, '{principal}'
            )->>'policy_revision';
            """,
        ).stdout.strip()
        self.assertEqual(64, len(initial_revision))

        duplicate_decision_head = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at)
            VALUES
                ('93000000-0000-4000-8095-000000000001', '{tenant}', '{matter}',
                 '{conflict_check}', 'hold', '{principal}',
                 '2026-08-20T06:03:01Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate_decision_head.returncode)
        self.assertIn("linear current head", duplicate_decision_head.stderr)

        tied_decision = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at, supersedes_decision_id)
            VALUES
                ('93000000-0000-4000-8095-000000000002', '{tenant}', '{matter}',
                 '{conflict_check}', 'hold', '{principal}',
                 '2026-08-20T06:03:00Z', '{conflict_decision}');
            """,
            check=False,
        )
        self.assertNotEqual(0, tied_decision.returncode)
        self.assertIn("strictly advance", tied_decision.stderr)

        future_decision = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at, supersedes_decision_id)
            VALUES
                ('93000000-0000-4000-8095-000000000003', '{tenant}', '{matter}',
                 '{conflict_check}', 'hold', '{principal}',
                 '2099-08-20T06:03:00Z', '{conflict_decision}');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_decision.returncode)
        self.assertIn("future", future_decision.stderr)

        successor_check = "93000000-0000-4000-8095-000000000004"
        successor_decision = "93000000-0000-4000-8095-000000000005"
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_checks
                (id, tenant_id, matter_id, checked_by_principal_id, checked_at,
                 normalization_version, result, complete)
            VALUES
                ('{successor_check}', '{tenant}', '{matter}', '{principal}',
                 '2026-08-20T06:06:30Z', 'sklegal-party-normalization/v1',
                 'clear', true);
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at, supersedes_decision_id)
            VALUES
                ('{successor_decision}', '{tenant}', '{matter}',
                 '{successor_check}', 'clear', '{principal}',
                 '2026-08-20T06:06:31Z', '{conflict_decision}');
            """,
        )
        stale_snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_legal.material_policy_snapshot(
                '{tenant}', '{matter}', '{material}', 1, '{principal}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(0, stale_snapshot.returncode)
        self.assertIn("policy state is stale", stale_snapshot.stderr)

        overlapping_retention = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 decided_by_principal_id, supersedes_retention_policy_id)
            VALUES
                ('93000000-0000-4000-8095-000000000006', '{tenant}', '{matter}',
                 60, '2026-08-20T06:59:59Z', '{principal}', '{retention}');
            """,
            check=False,
        )
        self.assertNotEqual(0, overlapping_retention.returncode)
        self.assertIn("overlap", overlapping_retention.stderr)

        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 decided_by_principal_id, supersedes_retention_policy_id)
            VALUES
                ('{retention_successor}', '{tenant}', '{matter}', 60,
                 '2026-08-20T07:00:00Z', '{principal}', '{retention}');
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('{policy_state_successor}', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:01Z', '{policy_state}');
            """,
        )
        current_snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT snapshot#>>'{{conflict_decision,disposition}}',
                   snapshot#>>'{{retention_policy,retention_policy_id}}',
                   snapshot->>'policy_revision' <> '{initial_revision}'
            FROM (
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}'
                ) AS snapshot
            ) AS policy;
            """,
        )
        self.assertEqual(
            f"clear|{retention_successor}|t",
            current_snapshot.stdout.strip(),
        )

        branched_state = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000007', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:02Z', '{policy_state}');
            """,
            check=False,
        )
        self.assertNotEqual(0, branched_state.returncode)
        self.assertIn("linear current head", branched_state.stderr)

        tied_state = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000008', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:01Z', '{policy_state_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, tied_state.returncode)
        self.assertIn("strictly advance", tied_state.stderr)
        missing_conflict_head = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, retention_policy_id,
                 conflict_state_complete, wall_state_complete,
                 classification_state_complete, legal_hold_state_complete,
                 ownership_resolved, pending_export, preservation_required,
                 recorded_by_principal_id, recorded_at, supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000010', '{tenant}', '{matter}',
                 '{retention_successor}', true, true, true, true, true, false,
                 false, '{principal}', '2026-08-20T07:00:02Z',
                 '{policy_state_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, missing_conflict_head.returncode)
        self.assertIn("exact decision head", missing_conflict_head.stderr)

        future_state = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('93000000-0000-4000-8095-000000000011', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2099-08-20T07:00:02Z', '{policy_state_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_state.returncode)
        self.assertIn("future head", future_state.stderr)

        future_retention = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.retention_policies
                (id, tenant_id, matter_id, retain_for_days, effective_from,
                 decided_by_principal_id, supersedes_retention_policy_id)
            VALUES
                ('93000000-0000-4000-8095-000000000012', '{tenant}', '{matter}',
                 90, '2099-08-20T07:00:02Z', '{principal}',
                 '{retention_successor}');
            """,
            check=False,
        )
        self.assertNotEqual(0, future_retention.returncode)
        self.assertIn("future head", future_retention.stderr)
        mismatched_decision = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.conflict_decisions
                (id, tenant_id, matter_id, conflict_check_id, disposition,
                 decided_by_principal_id, decided_at)
            VALUES
                ('93000000-0000-4000-8098-000000000001', '{tenant}', '{matter}',
                 '{conflict_check}', 'clear', '{principal}',
                 '2026-08-20T06:03:01Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, mismatched_decision.returncode)
        self.assertIn("matching complete check evidence", mismatched_decision.stderr)
        mismatched_release = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope, material_id,
                 issued_by_principal_id, effective_from, supersedes_hold_id,
                 released_by_principal_id, released_at)
            VALUES
                ('93000000-0000-4000-8097-000000000001', '{tenant}', '{matter}',
                 'released', 'material', '{material}', '{principal}',
                 '2026-08-20T06:05:00Z', '{legal_hold}', '{principal}',
                 '2026-08-20T06:06:00Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, mismatched_release.returncode)
        self.assertIn("exact active hold", mismatched_release.stderr)
        valid_release = "93000000-0000-4000-8096-000000000001"
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope,
                 issued_by_principal_id, effective_from, supersedes_hold_id,
                 released_by_principal_id, released_at)
            VALUES
                ('{valid_release}', '{tenant}', '{matter}', 'released', 'matter',
                 '{principal}', '2026-08-20T06:05:00Z', '{legal_hold}',
                 '{principal}', '2026-08-20T06:06:00Z');
            """,
        )
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, conflict_decision_id,
                 retention_policy_id, conflict_state_complete,
                 wall_state_complete, classification_state_complete,
                 legal_hold_state_complete, ownership_resolved, pending_export,
                 preservation_required, recorded_by_principal_id, recorded_at,
                 supersedes_state_id)
            VALUES
                ('{policy_state_after_release}', '{tenant}', '{matter}',
                 '{successor_decision}', '{retention_successor}', true, true,
                 true, true, true, false, false, '{principal}',
                 '2026-08-20T07:00:03Z', '{policy_state_successor}');
            """,
        )
        duplicate_release = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.legal_holds
                (id, tenant_id, matter_id, status, hold_scope,
                 issued_by_principal_id, effective_from, supersedes_hold_id,
                 released_by_principal_id, released_at)
            VALUES
                ('93000000-0000-4000-8095-000000000009', '{tenant}', '{matter}',
                 'released', 'matter', '{principal}',
                 '2026-08-20T06:05:00Z', '{legal_hold}', '{principal}',
                 '2026-08-20T07:00:04Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate_release.returncode)
        released_snapshot = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT snapshot->'legal_holds' @> pg_catalog.jsonb_build_array(
                pg_catalog.jsonb_build_object(
                    'legal_hold_id', '{valid_release}'::uuid,
                    'status', 'released',
                    'supersedes_hold_id', '{legal_hold}'::uuid
                )
            )
            FROM (
                SELECT sklegal_legal.material_policy_snapshot(
                    '{tenant}', '{matter}', '{material}', 1, '{principal}'
                ) AS snapshot
            ) AS policy;
            """,
        )
        self.assertEqual("t", released_snapshot.stdout.strip())
        for target_matter, target_principal in (
            (other_matter, principal),
            (matter, other_principal),
        ):
            with self.subTest(
                target_matter=target_matter,
                target_principal=target_principal,
            ):
                denied = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_legal.material_policy_snapshot(
                        '{tenant}', '{target_matter}', '{material}', 1,
                        '{target_principal}'
                    );
                    """,
                    check=False,
                )
                self.assertNotEqual(0, denied.returncode)
                self.assertIn("policy snapshot scope is not authorized", denied.stderr)
        direct_insert = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.matter_policy_states
                (id, tenant_id, matter_id, policy_revision,
                 conflict_state_complete, wall_state_complete,
                 classification_state_complete, legal_hold_state_complete,
                 ownership_resolved, pending_export, preservation_required,
                 recorded_by_principal_id, recorded_at)
            VALUES
                ('93000000-0000-4000-8099-000000000001', '{tenant}', '{matter}',
                 '{"f" * 64}', true, true, true, true, true, false, false,
                 '{principal}', '2026-08-20T06:07:00Z');
            """,
            check=False,
        )
        sealed_update = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.conflict_decisions
            SET version = 2 WHERE tenant_id = '{tenant}'
              AND matter_id = '{matter}' AND id = '{conflict_decision}';
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_insert.returncode)
        self.assertNotEqual(0, sealed_update.returncode)
        self.assertIn("append-only", sealed_update.stderr)
        self.assertEqual(
            "t|f|f",
            self._psql(
                "postgres",
                """
                SELECT has_function_privilege(
                           'sklegal_test_alpha_one',
                           'sklegal_legal.material_policy_snapshot(uuid,uuid,uuid,bigint,uuid)',
                           'EXECUTE'),
                       EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'sklegal_legal'
                             AND table_name = 'matter_policy_states'
                             AND column_name = 'profile_owner_principal_id'
                       ),
                       EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'sklegal_legal'
                             AND table_name = 'matter_memberships'
                             AND column_name = 'profile_owner_principal_id'
                       );
                """,
            ).stdout.strip(),
        )

    def test_01_domain_parity_matrix_and_forced_rls(self) -> None:
        validate_mapping_contract()
        entities = self.parity["entities"]
        exported_entities = {
            name
            for name in sklegal_domain.__all__
            if inspect.isclass(entity_type := getattr(sklegal_domain, name, None))
            and issubclass(entity_type, DomainEntity)
            and entity_type is not DomainEntity
        }
        self.assertEqual(31, len(exported_entities))
        self.assertEqual(exported_entities, set(entities))
        for contract in entities.values():
            schema, table = contract["table"].split(".")
            output = self._psql(
                "postgres",
                f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = '{schema}' AND table_name = '{table}'
                ORDER BY ordinal_position;
                """,
            )
            columns = set(output.stdout.splitlines())
            self.assertTrue(set(contract["columns"]).issubset(columns), contract)
        rls = self._psql(
            "postgres",
            """
            SELECT count(*) FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname LIKE 'sklegal_%'
              AND namespace.nspname <> 'sklegal_migrations'
              AND relation.relkind = 'r'
              AND NOT (
                  namespace.nspname = 'sklegal_audit'
                  AND relation.relname = 'rollback_guard'
              )
              AND (NOT relation.relrowsecurity OR NOT relation.relforcerowsecurity);
            """,
        )
        self.assertEqual("0", rls.stdout.strip())
        rollback_guard = self._psql(
            "postgres",
            """
            SELECT relation.relrowsecurity || ':' ||
                   relation.relforcerowsecurity || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'SELECT'
                   ) || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'INSERT'
                   ) || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'UPDATE'
                   ) || ':' ||
                   has_table_privilege(
                       'sklegal_test_alpha_one', relation.oid, 'DELETE'
                   )
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'sklegal_audit'
              AND relation.relname = 'rollback_guard';
            """,
        )
        self.assertEqual(
            "false:false:false:false:false:false", rollback_guard.stdout.strip()
        )
        hidden_guard = self._psql(
            "sklegal_test_alpha_one",
            "SELECT * FROM sklegal_audit.rollback_guard;",
            check=False,
        )
        self.assertNotEqual(0, hidden_guard.returncode)
        self.assertIn("permission denied", hidden_guard.stderr)

    def test_02_unfiltered_scope_and_scoped_duplicate_ids(self) -> None:
        value = self.fixture
        self.assertEqual(
            value["tenant_alpha"],
            self._psql(
                "sklegal_test_alpha_one", "SELECT id FROM sklegal_identity.tenants;"
            ).stdout.strip(),
        )
        self.assertEqual(
            value["matter_alpha_one"],
            self._psql(
                "sklegal_test_alpha_one", "SELECT id FROM sklegal_legal.matters;"
            ).stdout.strip(),
        )
        shared_id = "30000000-0000-4000-8000-000000000001"
        for role, tenant, matter in (
            (
                "sklegal_test_alpha_one",
                value["tenant_alpha"],
                value["matter_alpha_one"],
            ),
            (
                "sklegal_test_alpha_two",
                value["tenant_alpha"],
                value["matter_alpha_two"],
            ),
            ("sklegal_test_beta_one", value["tenant_beta"], value["matter_beta_one"]),
        ):
            self._psql(
                role,
                f"""
                INSERT INTO sklegal_legal.forums
                    (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
                VALUES ('{shared_id}', '{tenant}', '{matter}',
                        'Synthetic Forum', 'Synthetic', 'other');
                """,
            )
        visible = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT count(*) FROM sklegal_legal.forums WHERE id = '{shared_id}';",
        )
        self.assertEqual("1", visible.stdout.strip())
        duplicate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{shared_id}', '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 'Duplicate', 'Synthetic', 'other');
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate.returncode)
        cross = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.proceedings
                (id, tenant_id, matter_id, title, forum_id)
            VALUES ('30000000-0000-4000-8000-000000000002',
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    'Synthetic cross-scope probe',
                    '30000000-0000-4000-8000-000000000099');
            """,
            check=False,
        )
        self.assertNotEqual(0, cross.returncode)
        self.assertIn("foreign key", cross.stderr)

        denied_updates = []
        denied_inserts = []
        for inaccessible_tenant, inaccessible_matter, suffix in (
            (value["tenant_alpha"], value["matter_alpha_two"], "same-tenant"),
            (value["tenant_beta"], value["matter_beta_one"], "cross-tenant"),
        ):
            denied_updates.append(
                self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    UPDATE sklegal_legal.forums
                    SET name = 'Unauthorized {suffix}', version = version + 1
                    WHERE tenant_id = '{inaccessible_tenant}'
                      AND matter_id = '{inaccessible_matter}'
                      AND id = '{shared_id}';
                    """,
                    check=False,
                )
            )
            denied_inserts.append(
                self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    INSERT INTO sklegal_legal.forums
                        (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
                    VALUES ('30000000-0000-4000-8000-0000000000{suffix == "cross-tenant" and "04" or "03"}',
                            '{inaccessible_tenant}', '{inaccessible_matter}',
                            'Unauthorized {suffix}', 'Synthetic', 'other');
                    """,
                    check=False,
                )
            )
        self.assertTrue(all(result.returncode != 0 for result in denied_updates))
        self.assertTrue(
            all("permission denied" in result.stderr for result in denied_updates)
        )
        self.assertTrue(all(result.returncode != 0 for result in denied_inserts))
        self.assertTrue(
            all("row-level security" in result.stderr for result in denied_inserts)
        )

        rollback_id = "30000000-0000-4000-8000-000000000005"
        ordinary_rollback = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{rollback_id}', '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 'Rollback probe',
                    'Synthetic', 'other');
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{rollback_id}', '{value["tenant_alpha"]}',
                    '{value["matter_alpha_one"]}', 'Duplicate rollback probe',
                    'Synthetic', 'other');
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, ordinary_rollback.returncode)
        rolled_back = self._psql(
            "postgres",
            f"SELECT count(*) FROM sklegal_legal.forums WHERE id = '{rollback_id}';",
        )
        self.assertEqual("0", rolled_back.stdout.strip())

    def test_03_least_privilege_grants_and_role_bypass_resistance(self) -> None:
        value = self.fixture
        dangerous = self._psql(
            "postgres",
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee LIKE 'sklegal_test_%'
              AND table_schema LIKE 'sklegal_%'
              AND privilege_type IN ('DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER');
            """,
        )
        self.assertEqual("0", dangerous.stdout.strip())
        evidence_writes = self._psql(
            "postgres",
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee IN (
                'sklegal_test_alpha_one', 'sklegal_test_alpha_two',
                'sklegal_test_beta_one'
            ) AND table_schema = 'sklegal_legal'
              AND table_name IN ('execution_events', 'execution_receipts')
              AND privilege_type <> 'SELECT';
            """,
        )
        self.assertEqual("0", evidence_writes.stdout.strip())
        audit_writes = self._psql(
            "postgres",
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee LIKE 'sklegal_test_%'
              AND table_schema = 'sklegal_audit' AND table_name = 'events'
              AND privilege_type <> 'SELECT';
            """,
        )
        self.assertEqual("0", audit_writes.stdout.strip())
        self.assertEqual(
            "0",
            self._psql(
                "sklegal_migrator", "SELECT count(*) FROM sklegal_legal.matters;"
            ).stdout.strip(),
        )
        unsafe = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_identity.database_role_bindings
                (database_role, tenant_id, principal_id)
            VALUES ('sklegal_test_bypass', '{value["tenant_alpha"]}',
                    '{value["principal_alpha_one"]}');
            """,
            check=False,
        )
        self.assertNotEqual(0, unsafe.returncode)
        self.assertIn("exact safe role profile", unsafe.stderr)
        self_enroll = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{value["tenant_alpha"]}', '{value["matter_alpha_two"]}',
                    '{value["principal_alpha_one"]}', 'member');
            """,
            check=False,
        )
        self.assertNotEqual(0, self_enroll.returncode)

        self._psql(
            "postgres",
            """
            CREATE ROLE sklegal_test_object_owner LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            CREATE FUNCTION sklegal_legal.synthetic_owned_function()
            RETURNS integer LANGUAGE sql IMMUTABLE RETURN 1;
            ALTER FUNCTION sklegal_legal.synthetic_owned_function()
                OWNER TO sklegal_test_object_owner;
            """,
        )
        owner_provision = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                self.container,
                "--runtime-role",
                "sklegal_test_object_owner",
                "--tenant-id",
                value["tenant_alpha"],
                "--principal-id",
                value["principal_alpha_one"],
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, owner_provision.returncode)
        self.assertIn("cannot own an SKLegal database object", owner_provision.stderr)
        self._psql(
            "postgres",
            """
            DROP FUNCTION sklegal_legal.synthetic_owned_function();
            DROP ROLE sklegal_test_object_owner;
            """,
        )

        self._psql(
            "postgres",
            """
            CREATE ROLE sklegal_test_role_graph NOLOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS;
            GRANT sklegal_test_role_graph TO sklegal_test_alpha_one;
            """,
        )
        self.assertEqual(
            "f",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_identity.runtime_role_is_safe();",
            ).stdout.strip(),
        )
        drifted = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                self.container,
                "--runtime-role",
                "sklegal_test_alpha_one",
                "--tenant-id",
                value["tenant_alpha"],
                "--principal-id",
                value["principal_alpha_one"],
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, drifted.returncode)
        self.assertIn("role graph", drifted.stderr)
        self._psql(
            "postgres",
            """
            REVOKE sklegal_test_role_graph FROM sklegal_test_alpha_one;
            DROP ROLE sklegal_test_role_graph;
            ALTER ROLE sklegal_test_alpha_one BYPASSRLS;
            """,
        )
        self.assertEqual(
            "f",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_identity.runtime_role_is_safe();",
            ).stdout.strip(),
        )
        bypass_drift = subprocess.run(
            [
                str(REPO_ROOT / ".tools" / "bin" / "uv"),
                "run",
                "--locked",
                "python",
                str(PROVISIONER),
                "--docker-container",
                self.container,
                "--runtime-role",
                "sklegal_test_alpha_one",
                "--tenant-id",
                value["tenant_alpha"],
                "--principal-id",
                value["principal_alpha_one"],
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, bypass_drift.returncode)
        self.assertIn("least-privilege profile", bypass_drift.stderr)
        self._psql("postgres", "ALTER ROLE sklegal_test_alpha_one NOBYPASSRLS;")
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_identity.runtime_role_is_safe();",
            ).stdout.strip(),
        )

    def test_03_live_tenant_and_principal_status_fail_closed_and_reactivate(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        role = "sklegal_test_alpha_one"
        baseline_forum = "33000000-0000-4000-8000-000000000001"
        first_work_product = "33000000-0000-4000-8000-000000000010"
        first_artifact = "33000000-0000-4000-8000-000000000011"
        second_work_product = "33000000-0000-4000-8000-000000000012"
        second_artifact = "33000000-0000-4000-8000-000000000013"
        digest = "e" * 64
        self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('{baseline_forum}', '{tenant}', '{matter}',
                    'Synthetic status baseline', 'Synthetic', 'other');
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{first_work_product}', '{tenant}', '{matter}',
                    'Synthetic tenant status work product', 'memo',
                    '{first_artifact}', 1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{first_artifact}', '{tenant}', '{matter}',
                    '{first_work_product}', 1, '{digest}',
                    '33000000-0000-4000-8000-000000000014');
            COMMIT;
            """,
        )

        def provision_result(
            target_role: str = role,
            target_tenant: str = tenant,
            target_principal: str = principal,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    str(REPO_ROOT / ".tools" / "bin" / "uv"),
                    "run",
                    "--locked",
                    "python",
                    str(PROVISIONER),
                    "--docker-container",
                    self.container,
                    "--runtime-role",
                    target_role,
                    "--tenant-id",
                    target_tenant,
                    "--principal-id",
                    target_principal,
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        def set_tenant_status(status: str) -> str:
            self._psql(
                "postgres",
                f"""
                UPDATE sklegal_identity.tenants
                SET status = '{status}', version = version + 1
                WHERE id = '{tenant}';
                """,
            )
            return self._psql(
                "postgres",
                f"""
                SELECT tenant.status || '|' || binding.tenant_active::int ||
                       '|' || binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                JOIN sklegal_identity.tenants AS tenant
                  ON tenant.id = binding.tenant_id
                WHERE binding.database_role = '{role}';
                """,
            ).stdout.strip()

        def set_principal_status(status: str) -> str:
            self._psql(
                "postgres",
                f"""
                UPDATE sklegal_identity.principals
                SET status = '{status}', version = version + 1
                WHERE tenant_id = '{tenant}' AND id = '{principal}';
                """,
            )
            return self._psql(
                "postgres",
                f"""
                SELECT principal.status || '|' || binding.tenant_active::int ||
                       '|' || binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                JOIN sklegal_identity.principals AS principal
                  ON principal.tenant_id = binding.tenant_id
                 AND principal.id = binding.principal_id
                WHERE binding.database_role = '{role}';
                """,
            ).stdout.strip()

        def assert_runtime_inactive(insert_id: int) -> None:
            self.assertEqual(
                "f",
                self._psql(
                    role, "SELECT sklegal_identity.runtime_role_is_safe();"
                ).stdout.strip(),
            )
            self.assertEqual(
                "0",
                self._psql(
                    role,
                    f"SELECT count(*) FROM sklegal_legal.forums "
                    f"WHERE id = '{baseline_forum}';",
                ).stdout.strip(),
            )
            denied = self._psql(
                role,
                f"""
                INSERT INTO sklegal_legal.forums
                    (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
                VALUES ('33000000-0000-4000-8000-{insert_id:012d}',
                        '{tenant}', '{matter}', 'Synthetic inactive insert',
                        'Synthetic', 'other');
                """,
                check=False,
            )
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("row-level security", denied.stderr)
            reprovision = provision_result()
            self.assertNotEqual(0, reprovision.returncode)
            self.assertIn(
                "active principal and tenant membership are required",
                reprovision.stderr,
            )

        for index, inactive_status in enumerate(("suspended",), start=2):
            with self.subTest(tenant_status=inactive_status):
                self.assertEqual(
                    f"{inactive_status}|0|1",
                    set_tenant_status(inactive_status),
                )
                assert_runtime_inactive(index)
                denied_transition = self._psql(
                    role,
                    f"""
                    SELECT status
                    FROM sklegal_legal.transition_work_product_version(
                        '{tenant}', '{matter}', '{first_artifact}', 1,
                        'frozen');
                    """,
                    check=False,
                )
                self.assertNotEqual(0, denied_transition.returncode)
                self.assertIn("not authorized", denied_transition.stderr)
                self.assertEqual("active|1|1", set_tenant_status("active"))
                self.assertEqual(
                    "t",
                    self._psql(
                        role, "SELECT sklegal_identity.runtime_role_is_safe();"
                    ).stdout.strip(),
                )
                self.assertEqual(
                    "1",
                    self._psql(
                        role,
                        f"SELECT count(*) FROM sklegal_legal.forums "
                        f"WHERE id = '{baseline_forum}';",
                    ).stdout.strip(),
                )
        self.assertEqual(
            "frozen",
            self._psql(
                role,
                f"""
                SELECT status
                FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{first_artifact}', 1, 'frozen');
                """,
            ).stdout.strip(),
        )

        self._psql(
            role,
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{second_work_product}', '{tenant}', '{matter}',
                    'Synthetic principal status work product', 'memo',
                    '{second_artifact}', 1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{second_artifact}', '{tenant}', '{matter}',
                    '{second_work_product}', 1, '{digest}',
                    '33000000-0000-4000-8000-000000000015');
            COMMIT;
            """,
        )

        proposed_tenant = "33100000-0000-4000-8000-000000000101"
        proposed_principal = "33100000-0000-4000-8000-000000000102"
        proposed_role = "sklegal_test_proposed_identity"
        closed_tenant = "33100000-0000-4000-8000-000000000111"
        closed_principal = "33100000-0000-4000-8000-000000000112"
        closed_role = "sklegal_test_closed_identity"
        revoked_tenant = "33100000-0000-4000-8000-000000000121"
        revoked_principal = "33100000-0000-4000-8000-000000000122"
        revoked_role = "sklegal_test_revoked_identity"
        self._psql(
            "postgres",
            f"""
            CREATE ROLE {proposed_role} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            CREATE ROLE {closed_role} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            CREATE ROLE {revoked_role} LOGIN NOSUPERUSER NOCREATEDB
                NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
            INSERT INTO sklegal_identity.tenants
                (id, tenant_id, slug, name, status)
            VALUES
                ('{proposed_tenant}', '{proposed_tenant}',
                 'synthetic-proposed-identity', 'Synthetic Proposed Identity',
                 'proposed'),
                ('{closed_tenant}', '{closed_tenant}',
                 'synthetic-closed-identity', 'Synthetic Closed Identity',
                 'active'),
                ('{revoked_tenant}', '{revoked_tenant}',
                 'synthetic-revoked-identity', 'Synthetic Revoked Identity',
                 'active');
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, status)
            VALUES
                ('{proposed_principal}', '{proposed_tenant}', 'human',
                 'Synthetic Proposed Principal', 'active'),
                ('{closed_principal}', '{closed_tenant}', 'human',
                 'Synthetic Closed Principal', 'active'),
                ('{revoked_principal}', '{revoked_tenant}', 'human',
                 'Synthetic Revoked Principal', 'active');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES
                ('{proposed_tenant}', '{proposed_principal}', 'administrator'),
                ('{closed_tenant}', '{closed_principal}', 'administrator'),
                ('{revoked_tenant}', '{revoked_principal}', 'administrator');
            """,
        )
        proposed_denied = provision_result(
            proposed_role, proposed_tenant, proposed_principal
        )
        self.assertNotEqual(0, proposed_denied.returncode)
        self.assertIn(
            "active principal and tenant membership are required",
            proposed_denied.stderr,
        )
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.tenants
            SET status = 'active', version = version + 1
            WHERE id = '{proposed_tenant}';
            """,
        )
        self._provision(proposed_role, proposed_tenant, proposed_principal)
        self.assertEqual(
            "t",
            self._psql(
                proposed_role, "SELECT sklegal_identity.runtime_role_is_safe();"
            ).stdout.strip(),
        )

        self._provision(closed_role, closed_tenant, closed_principal)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.tenants
            SET status = 'closed', version = version + 1
            WHERE id = '{closed_tenant}';
            """,
        )
        self.assertEqual(
            "0|1",
            self._psql(
                "postgres",
                f"""
                SELECT binding.tenant_active::int || '|' ||
                       binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                WHERE binding.database_role = '{closed_role}';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "f",
            self._psql(
                closed_role, "SELECT sklegal_identity.runtime_role_is_safe();"
            ).stdout.strip(),
        )
        closed_reprovision = provision_result(
            closed_role, closed_tenant, closed_principal
        )
        self.assertNotEqual(0, closed_reprovision.returncode)
        closed_reactivation = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.tenants
            SET status = 'active', version = version + 1
            WHERE id = '{closed_tenant}';
            """,
            check=False,
        )
        self.assertNotEqual(0, closed_reactivation.returncode)
        self.assertIn("invalid identity status transition", closed_reactivation.stderr)

        self._provision(revoked_role, revoked_tenant, revoked_principal)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.principals
            SET status = 'revoked', version = version + 1
            WHERE tenant_id = '{revoked_tenant}' AND id = '{revoked_principal}';
            """,
        )
        self.assertEqual(
            "1|0",
            self._psql(
                "postgres",
                f"""
                SELECT binding.tenant_active::int || '|' ||
                       binding.principal_active::int
                FROM sklegal_identity.database_role_bindings AS binding
                WHERE binding.database_role = '{revoked_role}';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "f",
            self._psql(
                revoked_role, "SELECT sklegal_identity.runtime_role_is_safe();"
            ).stdout.strip(),
        )
        revoked_reprovision = provision_result(
            revoked_role, revoked_tenant, revoked_principal
        )
        self.assertNotEqual(0, revoked_reprovision.returncode)
        revoked_reactivation = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_identity.principals
            SET status = 'active', version = version + 1
            WHERE tenant_id = '{revoked_tenant}' AND id = '{revoked_principal}';
            """,
            check=False,
        )
        self.assertNotEqual(0, revoked_reactivation.returncode)
        self.assertIn("invalid identity status transition", revoked_reactivation.stderr)
        for index, inactive_status in enumerate(("suspended",), start=5):
            with self.subTest(principal_status=inactive_status):
                self.assertEqual(
                    f"{inactive_status}|1|0",
                    set_principal_status(inactive_status),
                )
                assert_runtime_inactive(index)
                denied_transition = self._psql(
                    role,
                    f"""
                    SELECT status
                    FROM sklegal_legal.transition_work_product_version(
                        '{tenant}', '{matter}', '{second_artifact}', 1,
                        'frozen');
                    """,
                    check=False,
                )
                self.assertNotEqual(0, denied_transition.returncode)
                self.assertIn("not authorized", denied_transition.stderr)
                self.assertEqual("active|1|1", set_principal_status("active"))
                self.assertEqual(
                    "t",
                    self._psql(
                        role, "SELECT sklegal_identity.runtime_role_is_safe();"
                    ).stdout.strip(),
                )
        self.assertEqual(
            "frozen",
            self._psql(
                role,
                f"""
                SELECT status
                FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{second_artifact}', 1, 'frozen');
                """,
            ).stdout.strip(),
        )
        self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('33000000-0000-4000-8000-000000000007',
                    '{tenant}', '{matter}', 'Synthetic reactivated insert',
                    'Synthetic', 'other');
            """,
        )

    def test_07_matter_state_graph_including_closed_never_opened_round_trips(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        client = value["client_alpha"]
        engagement = value["engagement_alpha"]
        principal = value["principal_alpha_one"]
        role = "sklegal_test_alpha_one"
        created_at = "2026-08-20T04:00:00Z"
        updated_at = "2026-08-20T05:00:00Z"
        opened_at = "2026-08-20T04:30:00Z"
        closed_at = "2026-08-20T04:45:00Z"
        transition_closed_at = datetime.fromisoformat("2026-08-20T06:00:00+00:00")
        transition_archived_at = datetime.fromisoformat("2026-08-20T07:00:00+00:00")

        def matter_entity(
            suffix: int,
            status: str,
            *,
            opened: str | None = None,
            closed: str | None = None,
        ) -> DomainEntity:
            identifier = f"34000000-0000-4000-8000-{suffix:012d}"
            return sklegal_domain.Matter.model_validate_json(
                json.dumps(
                    {
                        "id": identifier,
                        "tenant_id": tenant,
                        "matter_id": identifier,
                        "client_id": client,
                        "engagement_id": engagement,
                        "title": f"Synthetic Matter State {status} {suffix}",
                        "summary": "Synthetic-only Matter state graph acceptance.",
                        "aliases": [],
                        "opened_at": opened,
                        "closed_at": closed,
                        "status": status,
                        "version": 1,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                    sort_keys=True,
                )
            )

        constructed = {
            "proposed": matter_entity(1, "proposed"),
            "open": matter_entity(2, "open", opened=opened_at),
            "closed_never_opened": matter_entity(3, "closed", closed=closed_at),
            "closed_after_opened": matter_entity(
                4, "closed", opened=opened_at, closed=closed_at
            ),
            "archived": matter_entity(
                5, "archived", opened=opened_at, closed=closed_at
            ),
        }
        proposed_transition_base = matter_entity(6, "proposed")
        opened_transition_base = matter_entity(7, "open", opened=opened_at)
        expected_closed_never_opened = proposed_transition_base.transition_to(
            sklegal_domain.MatterStatus.CLOSED,
            at=transition_closed_at,
            closed_at=transition_closed_at,
        )
        expected_archived = opened_transition_base.transition_to(
            sklegal_domain.MatterStatus.CLOSED,
            at=transition_closed_at,
            closed_at=transition_closed_at,
        ).transition_to(
            sklegal_domain.MatterStatus.ARCHIVED,
            at=transition_archived_at,
        )
        expected = {
            **constructed,
            "transitioned_closed_never_opened": expected_closed_never_opened,
            "transitioned_archived": expected_archived,
        }
        metadata = PersistenceMetadata(relations={"aliases": ()})
        decomposed = {
            name: decompose(entity, metadata) for name, entity in expected.items()
        }
        self.assertEqual(7, len(decomposed))
        insert_payloads = tuple(constructed.values()) + (
            proposed_transition_base,
            opened_transition_base,
        )
        inserts = "\n".join(
            insert_statement(
                decompose(entity, metadata).table,
                decompose(entity, metadata).row,
            )
            for entity in insert_payloads
        )
        memberships = "\n".join(
            "INSERT INTO sklegal_legal.matter_memberships "
            "(tenant_id, matter_id, principal_id, membership_role) VALUES "
            f"('{tenant}', '{entity.id}', '{principal}', 'administrator');"
            for entity in insert_payloads
        )
        self._psql(
            "postgres",
            "BEGIN;\n" + inserts + "\n" + memberships + "\nCOMMIT;",
        )
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.matters
            SET status = 'closed',
                closed_at = '{transition_closed_at.isoformat()}',
                version = version + 1
            WHERE tenant_id = '{tenant}'
              AND id = '{proposed_transition_base.id}';
            UPDATE sklegal_legal.matters
            SET status = 'closed',
                closed_at = '{transition_closed_at.isoformat()}',
                version = version + 1
            WHERE tenant_id = '{tenant}'
              AND id = '{opened_transition_base.id}';
            UPDATE sklegal_legal.matters
            SET status = 'archived', version = version + 1
            WHERE tenant_id = '{tenant}'
              AND id = '{opened_transition_base.id}';
            """,
        )
        observed_statuses: dict[str, str] = {}
        for name, expected_entity in expected.items():
            rows = self._json_rows(
                role,
                MAPPINGS["Matter"].table,
                f"id = '{expected_entity.id}'",
            )
            self.assertEqual(1, len(rows), name)
            reconstruction = reconstruct_with_metadata(
                "Matter", rows[0], {"aliases": []}
            )
            actual = reconstruction.entity.model_dump(mode="python")
            canonical_expected = expected_entity.model_dump(mode="python")
            self.assertEqual(
                self._normalize_controlled_entity_outputs(canonical_expected, actual),
                actual,
                name,
            )
            self.assertEqual(metadata, reconstruction.metadata, name)
            observed_statuses[name] = reconstruction.entity.status.value
        self.assertEqual(
            {
                "proposed": "proposed",
                "open": "open",
                "closed_never_opened": "closed",
                "closed_after_opened": "closed",
                "archived": "archived",
                "transitioned_closed_never_opened": "closed",
                "transitioned_archived": "archived",
            },
            observed_statuses,
        )
        self.assertIsNone(expected_closed_never_opened.opened_at)
        terminal_reopen = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.matters
            SET status = 'open', closed_at = NULL, version = version + 1
            WHERE tenant_id = '{tenant}' AND id = '{constructed["archived"].id}';
            """,
            check=False,
        )
        self.assertNotEqual(0, terminal_reopen.returncode)
        self.assertIn("invalid matter status transition", terminal_reopen.stderr)

    def test_04_encryption_completeness_is_total_and_fail_closed(self) -> None:
        output = self._psql(
            "postgres",
            """
            WITH combinations AS (
                SELECT mask,
                    CASE WHEN mask & 1 = 1 THEN decode(repeat('ab', 16), 'hex') END AS payload,
                    CASE WHEN mask & 2 = 2 THEN 'vault:test/key-one' END AS key_ref,
                    CASE WHEN mask & 4 = 4 THEN 'aes-256-gcm' END AS algorithm,
                    CASE WHEN mask & 8 = 8 THEN clock_timestamp() END AS encrypted_at
                FROM generate_series(0, 15) AS mask
            )
            SELECT string_agg(mask || ':' || result, ',' ORDER BY mask)
            FROM (
                SELECT mask,
                    sklegal_identity.encrypted_payload_is_complete(
                        payload, key_ref, algorithm, encrypted_at
                    ) AS result
                FROM combinations
            ) AS checked;
            """,
        )
        results = dict(
            item.split(":", maxsplit=1) for item in output.stdout.strip().split(",")
        )
        self.assertEqual(
            {"0", "15"},
            {key for key, value in results.items() if value == "true"},
        )
        self.assertNotIn("", results.values())
        partial = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, encrypted_profile)
            VALUES ('40000000-0000-4000-8000-000000000001',
                    '{self.fixture["tenant_alpha"]}', 'agent', 'Partial encryption',
                    decode(repeat('ab', 16), 'hex'));
            """,
            check=False,
        )
        self.assertNotEqual(0, partial.returncode)
        self.assertIn("check constraint", partial.stderr)

    def test_05_strict_legacy_alias_binding_and_metadata(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        source_id = "50000000-0000-4000-8000-000000000001"
        event_id = "50000000-0000-4000-8000-000000000002"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{source_id}', '{tenant}', '{matter}', 'synthetic', 'v1',
                    '{"1" * 64}', 'synthetic/source-one', clock_timestamp());
            INSERT INTO sklegal_legal.matter_events
                (id, tenant_id, matter_id, event_type, description, observed_at,
                 source_reference_id)
            VALUES ('{event_id}', '{tenant}', '{matter}', 'synthetic_event',
                    'Synthetic event only.', clock_timestamp(), '{source_id}');
            INSERT INTO sklegal_legal.legacy_aliases
                (id, tenant_id, matter_id, canonical_record_kind,
                 canonical_record_id, legacy_record_kind, legacy_id, legacy_slug,
                 legacy_path, source_version, content_sha256, observed_at,
                 import_batch_id)
            VALUES
                ('50000000-0000-4000-8000-000000000003', '{tenant}', '{matter}',
                 'matter', '{matter}', 'problem', 'PRB-2026-100', 'synthetic-prb',
                 'problems/PRB-2026-100', 'v1', '{"2" * 64}', clock_timestamp(),
                 '50000000-0000-4000-8000-000000000010'),
                ('50000000-0000-4000-8000-000000000004', '{tenant}', '{matter}',
                 'matter_event', '{event_id}', 'incident', 'INC-100',
                 'synthetic-inc', 'incidents/INC-100', 'v1', '{"3" * 64}',
                 clock_timestamp(), '50000000-0000-4000-8000-000000000010');
            """,
        )
        invalid_cases = (
            ("matter", matter, "incident", "INC-101"),
            ("matter_event", event_id, "problem", "PRB-2026-101"),
            ("matter", matter, "problem", "PRB-101"),
        )
        for index, (canonical_kind, canonical_id, legacy_kind, legacy_id) in enumerate(
            invalid_cases, start=20
        ):
            denied = self._psql(
                "sklegal_test_alpha_one",
                f"""
                INSERT INTO sklegal_legal.legacy_aliases
                    (id, tenant_id, matter_id, canonical_record_kind,
                     canonical_record_id, legacy_record_kind, legacy_id,
                     legacy_slug, legacy_path, source_version, content_sha256,
                     observed_at, import_batch_id)
                VALUES ('50000000-0000-4000-8000-0000000000{index}',
                        '{tenant}', '{matter}', '{canonical_kind}',
                        '{canonical_id}', '{legacy_kind}', '{legacy_id}',
                        'invalid-{index}', 'synthetic/invalid-{index}', 'v1',
                        '{"4" * 64}', clock_timestamp(),
                        '50000000-0000-4000-8000-000000000010');
                """,
                check=False,
            )
            self.assertNotEqual(0, denied.returncode)
        for index, legacy_path in enumerate(
            (
                "/absolute/path",
                "synthetic/../escape",
                "synthetic/./dot",
                "synthetic//empty",
                "synthetic\\windows",
                " synthetic/outer-space",
            ),
            start=30,
        ):
            denied_path = self._psql(
                "sklegal_test_alpha_one",
                f"""
                INSERT INTO sklegal_legal.legacy_aliases
                    (id, tenant_id, matter_id, canonical_record_kind,
                     canonical_record_id, legacy_record_kind, legacy_id,
                     legacy_slug, legacy_path, source_version, content_sha256,
                     observed_at, import_batch_id)
                VALUES ('50000000-0000-4000-8000-0000000000{index}',
                        '{tenant}', '{matter}', 'matter', '{matter}', 'problem',
                        'PRB-2026-{index:03d}', 'invalid-path-{index}',
                        '{legacy_path}', 'v1', '{"4" * 64}', clock_timestamp(),
                        '50000000-0000-4000-8000-000000000010');
                """,
                check=False,
            )
            self.assertNotEqual(0, denied_path.returncode)
        owner_audit = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT matter.version || ':' || event.version || ':' ||
                   (matter.updated_at >= matter_alias.observed_at) || ':' ||
                   (event.updated_at >= event_alias.observed_at)
            FROM sklegal_legal.matters AS matter
            JOIN sklegal_legal.matter_events AS event
              ON event.tenant_id = matter.tenant_id
             AND event.matter_id = matter.matter_id
             AND event.id = '{event_id}'
            JOIN sklegal_legal.legacy_aliases AS matter_alias
              ON matter_alias.tenant_id = matter.tenant_id
             AND matter_alias.matter_id = matter.matter_id
             AND matter_alias.canonical_record_kind = 'matter'
            JOIN sklegal_legal.legacy_aliases AS event_alias
              ON event_alias.tenant_id = event.tenant_id
             AND event_alias.matter_id = event.matter_id
             AND event_alias.canonical_record_kind = 'matter_event'
            WHERE matter.id = '{matter}';
            """,
        )
        self.assertEqual("2:2:true:true", owner_audit.stdout.strip())

    def test_06_append_only_tensions_and_database_authority_history(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        source_id = "60000000-0000-4000-8000-000000000001"
        fact_one = "60000000-0000-4000-8000-000000000002"
        fact_two = "60000000-0000-4000-8000-000000000003"
        tension = "60000000-0000-4000-8000-000000000004"
        authority = "60000000-0000-4000-8000-000000000005"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{source_id}', '{tenant}', '{matter}', 'synthetic', 'v1',
                    '{"5" * 64}', 'synthetic/source-two', clock_timestamp());
            INSERT INTO sklegal_legal.fact_assertions
                (id, tenant_id, matter_id, subject_ref, predicate, value_type,
                 asserted_value, source_reference_id, source_locator, observed_at)
            VALUES
                ('{fact_one}', '{tenant}', '{matter}', '{matter}', 'timing',
                 'string', '"first"'::jsonb, '{source_id}', 'line:1',
                 clock_timestamp()),
                ('{fact_two}', '{tenant}', '{matter}', '{matter}', 'timing',
                 'string', '"second"'::jsonb, '{source_id}', 'line:2',
                 clock_timestamp());
            BEGIN;
            INSERT INTO sklegal_legal.tension_groups
                (id, tenant_id, matter_id, title)
            VALUES ('{tension}', '{tenant}', '{matter}', 'Synthetic tension');
            INSERT INTO sklegal_legal.tension_assertions
                (tenant_id, matter_id, tension_group_id, assertion_id)
            VALUES
                ('{tenant}', '{matter}', '{tension}', '{fact_one}'),
                ('{tenant}', '{matter}', '{tension}', '{fact_two}');
            COMMIT;
            INSERT INTO sklegal_legal.authority_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}', '{authority}');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version, system_from)
            VALUES ('{authority}', '{tenant}', '{matter}', 'Synthetic Authority',
                    'Synthetic 1', 'Synthetic', 'case', '{source_id}', 'proposed',
                    1, '2000-01-01T00:00:00Z');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version, system_from)
            VALUES ('{authority}', '{tenant}', '{matter}', 'Synthetic Authority',
                    'Synthetic 1', 'Synthetic', 'case', '{source_id}', 'challenged',
                    2, '2000-01-01T00:00:00Z');
            """,
        )
        history = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT count(*) || ':' || count(system_to) || ':' || max(version)
            FROM sklegal_legal.authority_history WHERE id = '{authority}';
            """,
        )
        self.assertEqual("2:1:2", history.stdout.strip())
        current = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT version FROM sklegal_legal.authority_current WHERE id = '{authority}';",
        )
        self.assertEqual("2", current.stdout.strip())
        controlled_time = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT bool_and(system_from > '2026-01-01T00:00:00Z')
            FROM sklegal_legal.authorities WHERE id = '{authority}';
            """,
        )
        self.assertEqual("t", controlled_time.stdout.strip())
        denied_link_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.tension_assertions
            SET assertion_id = '{fact_two}'
            WHERE tension_group_id = '{tension}' AND assertion_id = '{fact_one}';
            """,
            check=False,
        )
        denied_authority_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.authorities SET title = 'Rewritten'
            WHERE id = '{authority}' AND version = 1;
            """,
            check=False,
        )
        self.assertNotEqual(0, denied_link_update.returncode)
        self.assertNotEqual(0, denied_authority_update.returncode)

    def test_07_representative_domain_state_constraints(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.evidence_items
                (id, tenant_id, matter_id, title, media_type, content_sha256,
                 source_reference_id)
            VALUES ('70000000-0000-4000-8000-000000000005', '{tenant}',
                    '{matter}', 'Synthetic state evidence', 'text/plain',
                    '{"6" * 64}', '60000000-0000-4000-8000-000000000001');
            """,
        )
        bad_statements = (
            f"""
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, status)
            VALUES ('70000000-0000-4000-8000-000000000001', '{tenant}',
                    '{value["client_alpha"]}', 'Bad active engagement', 'Synthetic',
                    'active');
            """,
            f"""
            INSERT INTO sklegal_legal.proceedings
                (id, tenant_id, matter_id, title, status)
            VALUES ('70000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', 'Bad active proceeding', 'active');
            """,
            f"""
            INSERT INTO sklegal_legal.tasks
                (id, tenant_id, matter_id, title, description, status)
            VALUES ('70000000-0000-4000-8000-000000000003', '{tenant}',
                    '{matter}', 'Bad ready task', 'Synthetic', 'ready');
            """,
            f"""
            INSERT INTO sklegal_legal.custody_events
                (id, tenant_id, matter_id, evidence_item_id, action, custodian_id,
                 occurred_at, source_reference_id)
            VALUES ('70000000-0000-4000-8000-000000000004', '{tenant}',
                    '{matter}', '70000000-0000-4000-8000-000000000005',
                    'rewritten', '{value["principal_alpha_one"]}',
                    clock_timestamp(), '60000000-0000-4000-8000-000000000001');
            """,
        )
        for statement in bad_statements:
            denied = self._psql("sklegal_test_alpha_one", statement, check=False)
            self.assertNotEqual(0, denied.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind,
                 source_reference_id)
            VALUES ('70000000-0000-4000-8000-000000000010', '{tenant}',
                    '{matter}', 'Optional source forum', 'Synthetic', 'other', NULL);
            INSERT INTO sklegal_legal.deadlines
                (id, tenant_id, matter_id, title, candidate_due_at, status)
            VALUES ('70000000-0000-4000-8000-000000000011', '{tenant}',
                    '{matter}', 'Unknown candidate date', NULL, 'candidate');
            """,
        )
        bad_authority_kind = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.authority_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}',
                    '70000000-0000-4000-8000-000000000012');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version)
            VALUES ('70000000-0000-4000-8000-000000000012', '{tenant}',
                    '{matter}', 'Bad kind', 'Synthetic', 'Synthetic',
                    'constitutional', '60000000-0000-4000-8000-000000000001',
                    'proposed', 1);
            """,
            check=False,
        )
        self.assertNotEqual(0, bad_authority_kind.returncode)
        missing_applicability = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version)
            VALUES ('70000000-0000-4000-8000-000000000012', '{tenant}',
                    '{matter}', 'Missing applicability', 'Synthetic', 'Synthetic',
                    'constitution', '60000000-0000-4000-8000-000000000001',
                    'not_applicable', 1);
            """,
            check=False,
        )
        self.assertNotEqual(0, missing_applicability.returncode)

    def test_07_typed_validation_rejects_failed_unrelated_and_stale_evidence(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        source = "71000000-0000-4000-8000-000000000001"
        party = "71000000-0000-4000-8000-000000000002"
        unrelated_party = "71000000-0000-4000-8000-000000000003"
        failed = "71000000-0000-4000-8000-000000000004"
        unrelated = "71000000-0000-4000-8000-000000000005"
        stale = "71000000-0000-4000-8000-000000000006"
        current = "71000000-0000-4000-8000-000000000007"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                    '{"7" * 64}', 'synthetic/typed-validation',
                    clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{party}', '{tenant}', '{matter}', 'Validation subject',
                 'person', '{source}'),
                ('{unrelated_party}', '{tenant}', '{matter}',
                 'Unrelated subject', 'person', '{source}');
            BEGIN;
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, outcome, validator_principal_id,
                 validated_at, rationale)
            VALUES
                ('{failed}', '{tenant}', '{matter}', 'party', '{party}', 1,
                 'failed', '{principal}', clock_timestamp(),
                 'Synthetic failed check.'),
                ('{unrelated}', '{tenant}', '{matter}', 'party',
                 '{unrelated_party}', 1, 'passed', '{principal}',
                 clock_timestamp(), 'Synthetic unrelated passed check.'),
                ('{stale}', '{tenant}', '{matter}', 'party', '{party}', 1,
                 'passed', '{principal}', clock_timestamp(),
                 'Synthetic version-one passed check.');
            INSERT INTO sklegal_legal.validation_checks
                (tenant_id, matter_id, validation_id, check_id)
            VALUES
                ('{tenant}', '{matter}', '{failed}', 'synthetic.failed'),
                ('{tenant}', '{matter}', '{unrelated}', 'synthetic.unrelated'),
                ('{tenant}', '{matter}', '{stale}', 'synthetic.stale');
            COMMIT;
            """,
        )
        for reference in (failed, unrelated):
            denied = self._psql(
                "postgres",
                f"""
                UPDATE sklegal_legal.parties
                SET status = 'verified', verification_reference_id = '{reference}',
                    version = version + 1
                WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
                  AND id = '{party}';
                """,
                check=False,
            )
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("exact typed prior version", denied.stderr)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.parties
            SET display_name = 'Validation subject revised', version = version + 1
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND id = '{party}';
            """,
        )
        stale_denied = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.parties
            SET status = 'verified', verification_reference_id = '{stale}',
                version = version + 1
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND id = '{party}';
            """,
            check=False,
        )
        self.assertNotEqual(0, stale_denied.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, outcome, validator_principal_id,
                 validated_at, rationale)
            VALUES ('{current}', '{tenant}', '{matter}', 'party', '{party}', 2,
                    'passed', '{principal}', clock_timestamp(),
                    'Synthetic current exact check.');
            INSERT INTO sklegal_legal.validation_checks
                (tenant_id, matter_id, validation_id, check_id)
            VALUES ('{tenant}', '{matter}', '{current}', 'synthetic.current');
            COMMIT;
            """,
        )
        actor_spoof = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, outcome, validator_principal_id,
                 validated_at, rationale)
            VALUES ('71000000-0000-4000-8000-000000000008', '{tenant}',
                    '{matter}', 'party', '{party}', 2, 'passed',
                    '{value["principal_alpha_two"]}', clock_timestamp(),
                    'Actor spoof attempt.');
            """,
            check=False,
        )
        self.assertNotEqual(0, actor_spoof.returncode)
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.parties
            SET status = 'verified', verification_reference_id = '{current}',
                version = version + 1
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND id = '{party}';
            """,
        )
        restored = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT status || ':' || version FROM sklegal_legal.parties "
            f"WHERE id = '{party}';",
        )
        self.assertEqual("verified:3", restored.stdout.strip())

    def test_07_claim_and_defense_cardinality_never_cross_satisfies(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]

        def exercise(
            suffix: int,
            *,
            element_kind: str,
            target_kind: str,
            should_pass: bool,
        ) -> subprocess.CompletedProcess[str]:
            shared = f"73000000-0000-4000-8000-{suffix:012d}"
            issue = f"73000000-0000-4000-8001-{suffix:012d}"
            element = f"73000000-0000-4000-8002-{suffix:012d}"
            validation = f"73000000-0000-4000-8003-{suffix:012d}"
            target_table = "claims" if target_kind == "claim" else "defenses"
            self._psql(
                "postgres",
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.issues
                    (id, tenant_id, matter_id, question)
                VALUES ('{issue}', '{tenant}', '{matter}',
                        'Synthetic theory cardinality {suffix}?');
                INSERT INTO sklegal_legal.claims
                    (id, tenant_id, matter_id, issue_id, label, statement)
                VALUES ('{shared}', '{tenant}', '{matter}', '{issue}',
                        'Synthetic claim {suffix}', 'Synthetic claim statement.');
                INSERT INTO sklegal_legal.defenses
                    (id, tenant_id, matter_id, issue_id, label, statement)
                VALUES ('{shared}', '{tenant}', '{matter}', '{issue}',
                        'Synthetic defense {suffix}', 'Synthetic defense statement.');
                INSERT INTO sklegal_legal.elements
                    (id, tenant_id, matter_id, theory_kind, claim_id, description)
                VALUES ('{element}', '{tenant}', '{matter}', '{element_kind}',
                        '{shared}', 'Synthetic {element_kind} element.');
                COMMIT;
                """,
            )
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.validations
                    (id, tenant_id, matter_id, subject_kind,
                     subject_artifact_id, subject_artifact_version, outcome,
                     validator_principal_id, validated_at, rationale)
                VALUES ('{validation}', '{tenant}', '{matter}', '{target_kind}',
                        '{shared}', 1, 'passed', '{principal}',
                        clock_timestamp(), 'Synthetic exact theory validation.');
                INSERT INTO sklegal_legal.validation_checks
                    (tenant_id, matter_id, validation_id, check_id)
                VALUES ('{tenant}', '{matter}', '{validation}',
                        'synthetic.theory.{suffix}');
                COMMIT;
                """,
            )
            result = self._psql(
                "postgres",
                f"""
                BEGIN;
                UPDATE sklegal_legal.{target_table}
                SET status = 'accepted', acceptance_validation_id = '{validation}',
                    version = version + 1
                WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
                  AND id = '{shared}';
                COMMIT;
                """,
                check=False,
            )
            if should_pass:
                self.assertEqual(0, result.returncode, result.stderr)
                restored = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT target.status || ':' || count(element.id)
                    FROM sklegal_legal.{target_table} AS target
                    LEFT JOIN sklegal_legal.elements AS element
                      ON element.tenant_id = target.tenant_id
                     AND element.matter_id = target.matter_id
                     AND element.claim_id = target.id
                     AND element.theory_kind = '{target_kind}'
                    WHERE target.id = '{shared}'
                    GROUP BY target.status;
                    """,
                )
                self.assertEqual("accepted:1", restored.stdout.strip())
            else:
                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "accepted claim or defense requires an element", result.stderr
                )
            return result

        exercise(1, element_kind="claim", target_kind="defense", should_pass=False)
        exercise(2, element_kind="defense", target_kind="claim", should_pass=False)
        exercise(3, element_kind="claim", target_kind="claim", should_pass=True)
        exercise(4, element_kind="defense", target_kind="defense", should_pass=True)

    def test_07_uuid_scalar_and_effective_interval_boundaries(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        missing_uuid_triggers = self._psql(
            "postgres",
            """
            SELECT count(*)
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname IN (
                'sklegal_identity', 'sklegal_legal', 'sklegal_integrations',
                'sklegal_workflow', 'sklegal_audit'
            ) AND relation.relkind = 'r'
              AND EXISTS (
                  SELECT 1 FROM pg_attribute AS attribute
                  WHERE attribute.attrelid = relation.oid
                    AND attribute.atttypid = 'uuid'::regtype
                    AND attribute.attnum > 0 AND NOT attribute.attisdropped
              )
              AND NOT EXISTS (
                  SELECT 1 FROM pg_trigger AS trigger_record
                  WHERE trigger_record.tgrelid = relation.oid
                    AND trigger_record.tgname = 'domain_id_non_nil'
                    AND NOT trigger_record.tgisinternal
              );
            """,
        )
        self.assertEqual("0", missing_uuid_triggers.stdout.strip())
        for statement in (
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('00000000-0000-0000-0000-000000000000', '{tenant}',
                    '{matter}', 'Nil forum', 'Synthetic', 'other');
            """,
            f"""
            INSERT INTO sklegal_legal.tasks
                (id, tenant_id, matter_id, title, description,
                 assigned_principal_id)
            VALUES ('72000000-0000-4000-8000-000000000001', '{tenant}',
                    '{matter}', 'Nil assignee', 'Synthetic',
                    '00000000-0000-0000-0000-000000000000');
            """,
        ):
            denied_nil = self._psql("sklegal_test_alpha_one", statement, check=False)
            self.assertNotEqual(0, denied_nil.returncode)
            self.assertIn("nil UUID", denied_nil.stderr)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('72000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', '{"x" * 512}', 'Synthetic', 'other');
            """,
        )
        too_long = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind)
            VALUES ('72000000-0000-4000-8000-000000000003', '{tenant}',
                    '{matter}', '{"x" * 513}', 'Synthetic', 'other');
            """,
            check=False,
        )
        self.assertNotEqual(0, too_long.returncode)
        self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, valid_to)
            VALUES ('72000000-0000-4000-8000-000000000004', '{tenant}',
                    '{value["client_alpha"]}', 'End-only interval', 'Synthetic',
                    '2026-12-01T00:00:00Z');
            """,
        )
        empty_interval = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.engagements
                (id, tenant_id, client_id, title, scope, valid_from, valid_to)
            VALUES ('72000000-0000-4000-8000-000000000005', '{tenant}',
                    '{value["client_alpha"]}', 'Empty interval', 'Synthetic',
                    '2026-12-01T00:00:00Z', '2026-12-01T00:00:00Z');
            """,
            check=False,
        )
        self.assertNotEqual(0, empty_interval.returncode)
        boundary_at = datetime.fromisoformat("2026-08-20T12:00:00+00:00")
        domain_forum = {
            "id": UUID("72000000-0000-4000-8000-000000000002"),
            "tenant_id": UUID(tenant),
            "matter_id": UUID(matter),
            "created_at": boundary_at,
            "updated_at": boundary_at,
            "name": "x" * 512,
            "jurisdiction": "Synthetic",
            "forum_kind": "other",
        }
        Forum.model_validate(domain_forum, strict=True)
        with self.assertRaises(ValidationError):
            Forum.model_validate({**domain_forum, "name": "x" * 513}, strict=True)
        EffectiveInterval(valid_to=boundary_at)
        with self.assertRaises(ValidationError):
            EffectiveInterval(valid_from=boundary_at, valid_to=boundary_at)

    def test_08_full_synthetic_legal_record_round_trip(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        digest = "8" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('80000000-0000-4000-8000-000000000001', '{tenant}',
                    '{matter}', 'synthetic', 'v1', '{digest}',
                    'synthetic/round-trip', clock_timestamp());
            INSERT INTO sklegal_legal.forums
                (id, tenant_id, matter_id, name, jurisdiction, forum_kind,
                 source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000002', '{tenant}',
                    '{matter}', 'Round Trip Forum', 'Synthetic', 'court',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.proceedings
                (id, tenant_id, matter_id, title, forum_id, status)
            VALUES ('80000000-0000-4000-8000-000000000003', '{tenant}',
                    '{matter}', 'Round Trip Proceeding',
                    '80000000-0000-4000-8000-000000000002', 'proposed');
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000004', '{tenant}',
                    '{matter}', 'Round Trip Party', 'person',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.party_roles
                (id, tenant_id, matter_id, party_id, proceeding_id, role,
                 source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000005', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000004',
                    '80000000-0000-4000-8000-000000000003', 'client',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.matter_events
                (id, tenant_id, matter_id, event_type, description, occurred_at,
                 observed_at, source_reference_id, status)
            VALUES ('80000000-0000-4000-8000-000000000006', '{tenant}',
                    '{matter}', 'round_trip', 'Synthetic event',
                    clock_timestamp(), clock_timestamp(),
                    '80000000-0000-4000-8000-000000000001', 'proposed');
            INSERT INTO sklegal_legal.legal_transactions
                (id, tenant_id, matter_id, title, description)
            VALUES ('80000000-0000-4000-8000-000000000007', '{tenant}',
                    '{matter}', 'Round Trip Transaction', 'Synthetic transaction');
            INSERT INTO sklegal_legal.transaction_party_roles
                (tenant_id, matter_id, transaction_id, party_role_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000007',
                    '80000000-0000-4000-8000-000000000005');
            INSERT INTO sklegal_legal.transaction_source_references
                (tenant_id, matter_id, transaction_id, source_reference_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000007',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.fact_assertions
                (id, tenant_id, matter_id, subject_ref, predicate, value_type,
                 asserted_value, source_reference_id, source_locator, observed_at)
            VALUES ('80000000-0000-4000-8000-000000000008', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000007',
                    'amount', 'integer', '7'::jsonb,
                    '80000000-0000-4000-8000-000000000001', 'line:7',
                    clock_timestamp());
            INSERT INTO sklegal_legal.evidence_items
                (id, tenant_id, matter_id, title, media_type, content_sha256,
                 source_reference_id, status)
            VALUES ('80000000-0000-4000-8000-000000000009', '{tenant}',
                    '{matter}', 'Round Trip Evidence', 'text/plain', '{digest}',
                    '80000000-0000-4000-8000-000000000001', 'proposed');
            INSERT INTO sklegal_legal.custody_events
                (id, tenant_id, matter_id, evidence_item_id, action, custodian_id,
                 occurred_at, source_reference_id)
            VALUES ('80000000-0000-4000-8000-000000000010', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000009',
                    'acquired', '{principal}', clock_timestamp(),
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.authority_identities
                (tenant_id, matter_id, id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000011');
            INSERT INTO sklegal_legal.authorities
                (id, tenant_id, matter_id, title, citation, jurisdiction,
                 authority_kind, source_reference_id, status, version)
            VALUES ('80000000-0000-4000-8000-000000000011', '{tenant}',
                    '{matter}', 'Round Trip Authority', 'Synthetic 8', 'Synthetic',
                    'administrative_material',
                    '80000000-0000-4000-8000-000000000001', 'proposed', 1);
            INSERT INTO sklegal_legal.issues
                (id, tenant_id, matter_id, question)
            VALUES ('80000000-0000-4000-8000-000000000012', '{tenant}',
                    '{matter}', 'What is the synthetic issue?');
            INSERT INTO sklegal_legal.claims
                (id, tenant_id, matter_id, issue_id, label, statement)
            VALUES ('80000000-0000-4000-8000-000000000013', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000012',
                    'Synthetic claim', 'Synthetic claim statement.');
            INSERT INTO sklegal_legal.defenses
                (id, tenant_id, matter_id, issue_id, label, statement)
            VALUES ('80000000-0000-4000-8000-000000000014', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000012',
                    'Synthetic defense', 'Synthetic defense statement.');
            INSERT INTO sklegal_legal.elements
                (id, tenant_id, matter_id, theory_kind, claim_id, description)
            VALUES
                ('80000000-0000-4000-8000-000000000015', '{tenant}', '{matter}',
                 'claim', '80000000-0000-4000-8000-000000000013',
                 'Synthetic claim element.'),
                ('80000000-0000-4000-8000-000000000016', '{tenant}', '{matter}',
                 'defense', '80000000-0000-4000-8000-000000000014',
                 'Synthetic defense element.');
            INSERT INTO sklegal_legal.element_evidence
                (tenant_id, matter_id, element_id, evidence_item_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000015',
                    '80000000-0000-4000-8000-000000000009');
            INSERT INTO sklegal_legal.theory_evidence
                (tenant_id, matter_id, theory_kind, theory_id, evidence_item_id)
            VALUES ('{tenant}', '{matter}', 'defense',
                    '80000000-0000-4000-8000-000000000014',
                    '80000000-0000-4000-8000-000000000009');
            INSERT INTO sklegal_legal.theory_authorities
                (tenant_id, matter_id, theory_kind, theory_id, authority_id)
            VALUES ('{tenant}', '{matter}', 'claim',
                    '80000000-0000-4000-8000-000000000013',
                    '80000000-0000-4000-8000-000000000011');
            INSERT INTO sklegal_legal.remedies
                (id, tenant_id, matter_id, claim_id, description)
            VALUES ('80000000-0000-4000-8000-000000000017', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000013',
                    'Synthetic remedy.');
            INSERT INTO sklegal_legal.remedy_authorities
                (tenant_id, matter_id, remedy_id, authority_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000017',
                    '80000000-0000-4000-8000-000000000011');
            INSERT INTO sklegal_legal.deadline_calculations
                (id, tenant_id, matter_id, trigger_fact_id, calculation_rule,
                 candidate_due_at, calculated_at, calculation_version)
            VALUES ('80000000-0000-4000-8000-000000000018', '{tenant}',
                    '{matter}', '80000000-0000-4000-8000-000000000008',
                    'Synthetic plus 7 days', clock_timestamp() + interval '7 days',
                    clock_timestamp(), 'v1');
            INSERT INTO sklegal_legal.deadline_calculation_sources
                (tenant_id, matter_id, calculation_id, source_reference_id)
            VALUES ('{tenant}', '{matter}',
                    '80000000-0000-4000-8000-000000000018',
                    '80000000-0000-4000-8000-000000000001');
            INSERT INTO sklegal_legal.deadlines
                (id, tenant_id, matter_id, title, candidate_due_at)
            VALUES ('80000000-0000-4000-8000-000000000019', '{tenant}',
                    '{matter}', 'Synthetic candidate deadline', NULL);
            INSERT INTO sklegal_legal.tasks
                (id, tenant_id, matter_id, title, description)
            VALUES ('80000000-0000-4000-8000-000000000020', '{tenant}',
                    '{matter}', 'Synthetic task', 'Synthetic task description.');
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}',
                '80000000-0000-4000-8000-000000000021',
                'internal', 'calendar', 'Synthetic communication',
                ARRAY['80000000-0000-4000-8000-000000000004'::uuid]);
            COMMIT;
            """,
        )
        restored = self._psql(
            "sklegal_test_alpha_one",
            """
            SELECT jsonb_build_object(
                'defense', (SELECT label FROM sklegal_legal.defenses
                    WHERE id = '80000000-0000-4000-8000-000000000014'),
                'element_count', (SELECT count(*) FROM sklegal_legal.elements
                    WHERE id IN ('80000000-0000-4000-8000-000000000015',
                                 '80000000-0000-4000-8000-000000000016')),
                'remedy', (SELECT description FROM sklegal_legal.remedies
                    WHERE id = '80000000-0000-4000-8000-000000000017'),
                'deadline_candidate_unknown', (SELECT candidate_due_at IS NULL
                    FROM sklegal_legal.deadlines
                    WHERE id = '80000000-0000-4000-8000-000000000019'),
                'communication', (SELECT direction || ':' || channel
                    FROM sklegal_legal.communications
                    WHERE id = '80000000-0000-4000-8000-000000000021')
            );
            """,
        )
        payload = json.loads(restored.stdout)
        self.assertEqual("Synthetic defense", payload["defense"])
        self.assertEqual(2, payload["element_count"])
        self.assertTrue(payload["deadline_candidate_unknown"])
        self.assertEqual("internal:calendar", payload["communication"])

    def test_09_optimistic_conflict_and_monotonic_long_transaction_time(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        work_product_id = "89000000-0000-4000-8000-000000000001"
        version_id = "89000000-0000-4000-8000-000000000002"
        digest = "9" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{work_product_id}', '{tenant}', '{matter}',
                    'Synthetic concurrency work product', 'memo', '{version_id}',
                    1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{version_id}', '{tenant}', '{matter}', '{work_product_id}',
                    1, '{digest}',
                    '89000000-0000-4000-8000-000000000003');
            COMMIT;
            """,
        )
        first_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-first-writer';
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{version_id}', 1, 'frozen');
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        first = subprocess.Popen(
            [*self._psql_command("sklegal_test_alpha_one"), "--command", first_sql],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(50):
            waiting = self._psql(
                "postgres",
                """
                SELECT count(*) FROM pg_stat_activity
                WHERE application_name = 'sklegal-s102-first-writer'
                  AND wait_event = 'PgSleep';
                """,
            )
            if waiting.stdout.strip() == "1":
                break
            time.sleep(0.02)
        else:
            first.kill()
            self.fail("first writer did not reach deterministic sleep checkpoint")
        second = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{version_id}', 1, 'frozen');
            """,
            check=False,
        )
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(0, first.returncode, stderr)
        self.assertIn("frozen", stdout)
        self.assertNotEqual(0, second.returncode)
        self.assertIn("version conflict", second.stderr)
        monotonic = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            SELECT pg_sleep(0.05);
            SELECT (transitioned).updated_at > transaction_timestamp()
            FROM (
                SELECT sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{version_id}', 2, 'superseded'
                ) AS transitioned
            ) AS result;
            COMMIT;
            """,
        )
        self.assertIn("t", monotonic.stdout.splitlines())

    def test_10_controlled_work_product_approval_and_execution_paths(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        work_product = "90000000-0000-4000-8000-000000000001"
        artifact = "90000000-0000-4000-8000-000000000002"
        validation = "90000000-0000-4000-8000-000000000003"
        approval = "90000000-0000-4000-8000-000000000004"
        participant = "90000000-0000-4000-8000-000000000050"
        participant_source = "90000000-0000-4000-8000-000000000051"
        artifact_sha = "a" * 64
        destination_sha = "b" * 64
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES ('{participant_source}', '{tenant}', '{matter}',
                    'synthetic', 'v1', '{"e" * 64}', 'synthetic:participant',
                    clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES ('{participant}', '{tenant}', '{matter}',
                    'Synthetic communication participant', 'person',
                    '{participant_source}');
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{work_product}', '{tenant}', '{matter}',
                    'Synthetic governed work product', 'memo', '{artifact}', 1,
                    '{artifact_sha}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}', 1,
                    '{artifact_sha}',
                    '90000000-0000-4000-8000-000000000099');
            COMMIT;
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
            BEGIN;
            INSERT INTO sklegal_legal.validations
                (id, tenant_id, matter_id, subject_kind, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256, outcome,
                 validator_principal_id, validated_at, rationale)
            VALUES ('{validation}', '{tenant}', '{matter}',
                    'work_product_version', '{artifact}', 1,
                    '{artifact_sha}', 'passed', '{principal}', clock_timestamp(),
                    'Synthetic validation passed.');
            INSERT INTO sklegal_legal.validation_checks
                (tenant_id, matter_id, validation_id, check_id)
            VALUES ('{tenant}', '{matter}', '{validation}',
                    'synthetic-artifact-check');
            COMMIT;
            INSERT INTO sklegal_legal.approvals
                (id, tenant_id, matter_id, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256)
            VALUES ('{approval}', '{tenant}', '{matter}', '{artifact}', 1,
                    '{artifact_sha}');
            SELECT status FROM sklegal_legal.transition_approval(
                '{tenant}', '{matter}', '{approval}', 1, 'approved',
                'Synthetic exact-version approval.');
            """,
        )
        frozen_payload_change = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.work_product_versions
            SET content_sha256 = '{"c" * 64}', version = version + 1
            WHERE id = '{artifact}';
            """,
            check=False,
        )
        terminal_approval = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.approvals
                (id, tenant_id, matter_id, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256, status,
                 reviewer_principal_id, decided_at, rationale)
            VALUES ('90000000-0000-4000-8000-000000000005', '{tenant}',
                    '{matter}', '{artifact}', 1, '{artifact_sha}', 'approved',
                    '{principal}', clock_timestamp(), 'Bypass attempt');
            """,
            check=False,
        )
        self.assertNotEqual(0, frozen_payload_change.returncode)
        self.assertNotEqual(0, terminal_approval.returncode)

        rejected_approval = "90000000-0000-4000-8000-000000000006"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.approvals
                (id, tenant_id, matter_id, subject_artifact_id,
                 subject_artifact_version, subject_content_sha256)
            VALUES ('{rejected_approval}', '{tenant}', '{matter}', '{artifact}',
                    1, '{artifact_sha}');
            SELECT status FROM sklegal_legal.transition_approval(
                '{tenant}', '{matter}', '{rejected_approval}', 1, 'rejected',
                'Synthetic rejection evidence.');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 1, 'in_review');
            """,
        )
        failed_work_product_gate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 2, 'validated',
                '90000000-0000-4000-8000-000000000098', NULL);
            """,
            check=False,
        )
        self.assertNotEqual(0, failed_work_product_gate.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 2, 'validated',
                '{validation}', NULL);
            """,
        )
        rejected_work_product_gate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 3, 'approved', NULL,
                '{rejected_approval}');
            """,
            check=False,
        )
        self.assertNotEqual(0, rejected_work_product_gate.returncode)
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 3, 'approved', NULL,
                '{approval}');
            SELECT status FROM sklegal_legal.revise_work_product(
                '{tenant}', '{matter}', '{work_product}', 4,
                'Synthetic governed work product revision', 'memo',
                '{artifact}', 1, '{artifact_sha}');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 5, 'validated',
                '{validation}', NULL);
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{work_product}', 6, 'approved', NULL,
                '{approval}');
            """,
        )
        terminal_work_product = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256, validation_result_id, approval_id,
                 status)
            VALUES ('90000000-0000-4000-8000-000000000007', '{tenant}',
                    '{matter}', 'Terminal bypass', 'memo', '{artifact}', 1,
                    '{artifact_sha}', '{validation}', '{approval}', 'approved');
            """,
            check=False,
        )
        self.assertNotEqual(0, terminal_work_product.returncode)

        def insert_execution(suffix: int) -> str:
            execution_id = f"90000000-0000-4000-8000-{suffix:012d}"
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                INSERT INTO sklegal_legal.executions
                    (id, tenant_id, matter_id, subject_artifact_id,
                     subject_artifact_version, subject_content_sha256,
                     destination_sha256, idempotency_key)
                VALUES ('{execution_id}', '{tenant}', '{matter}', '{artifact}', 1,
                        '{artifact_sha}', '{destination_sha}',
                        'synthetic-execution-{suffix}');
                """,
            )
            return execution_id

        def transition(
            execution_id: str,
            version: int,
            status: str,
            *,
            validation_id: str | None = None,
            approval_id: str | None = None,
            receipt: bool = False,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            receipt_arguments = (
                ", '90000000-0000-4000-8000-000000000090', "
                "'synthetic-connector', 'synthetic-receipt-90', "
                "clock_timestamp(), clock_timestamp()"
                if receipt
                else ""
            )
            return self._psql(
                "sklegal_test_alpha_one",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{execution_id}', {version},
                    '{status}', 'corr-{execution_id[-8:]}-{version}',
                    {f"'{validation_id}'" if validation_id else "NULL"},
                    {f"'{approval_id}'" if approval_id else "NULL"}
                    {receipt_arguments});
                """,
                check=check,
            )

        happy = insert_execution(10)
        communication = "90000000-0000-4000-8000-000000000040"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{communication}', 'outbound',
                'email', 'Synthetic governed communication',
                ARRAY['{participant}'::uuid], '{artifact}', 1,
                '{artifact_sha}');
            """,
        )
        terminal_communication = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.communications
                (id, tenant_id, matter_id, direction, channel, subject, status)
            VALUES ('90000000-0000-4000-8000-000000000041', '{tenant}',
                    '{matter}', 'internal', 'calendar', 'Terminal bypass',
                    'cancelled');
            """,
            check=False,
        )
        no_participants = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}',
                '90000000-0000-4000-8000-000000000042',
                'internal', 'calendar', 'Missing participant', ARRAY[]::uuid[]);
            """,
            check=False,
        )
        self.assertNotEqual(0, terminal_communication.returncode)
        self.assertNotEqual(0, no_participants.returncode)

        def transition_communication(
            version: int,
            status: str,
            *,
            validation_id: str | None = None,
            approval_id: str | None = None,
            execution_id: str | None = None,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            return self._psql(
                "sklegal_test_alpha_one",
                f"""
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{communication}', {version},
                    '{status}',
                    {f"'{validation_id}'" if validation_id else "NULL"},
                    {f"'{approval_id}'" if approval_id else "NULL"},
                    {f"'{destination_sha}'" if execution_id else "NULL"},
                    {f"'{execution_id}'" if execution_id else "NULL"});
                """,
                check=check,
            )

        wrong_communication_gate = transition_communication(
            1,
            "validated",
            validation_id="90000000-0000-4000-8000-000000000098",
            check=False,
        )
        self.assertNotEqual(0, wrong_communication_gate.returncode)
        transition_communication(1, "validated", validation_id=validation)
        transition_communication(2, "approved", approval_id=approval)
        communication_reset = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status || ':' || version || ':' || subject
            FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 3, 'outbound',
                'email', 'Synthetic revised governed communication',
                '{artifact}', 1, '{artifact_sha}',
                ARRAY['{participant}'::uuid]);
            """,
        )
        self.assertEqual(
            "draft:4:Synthetic revised governed communication",
            communication_reset.stdout.strip(),
        )
        transition_communication(4, "validated", validation_id=validation)
        transition_communication(5, "approved", approval_id=approval)
        skipped = transition(happy, 1, "approved", approval_id=approval, check=False)
        self.assertNotEqual(0, skipped.returncode)
        transition(happy, 1, "validated", validation_id=validation)
        changed_gate = transition(
            happy,
            2,
            "approved",
            validation_id="90000000-0000-4000-8000-000000000098",
            approval_id=approval,
            check=False,
        )
        self.assertNotEqual(0, changed_gate.returncode)
        transition(happy, 2, "approved", approval_id=approval)
        transition(happy, 3, "queued")
        transition_communication(6, "queued", execution_id=happy)
        transition(happy, 4, "dispatched")
        transition_communication(7, "dispatched")
        missing_receipt = transition(happy, 5, "receipt_verified", check=False)
        self.assertNotEqual(0, missing_receipt.returncode)
        transition(happy, 5, "receipt_verified", receipt=True)
        transition_communication(8, "receipt_verified")

        cancelled_draft = insert_execution(20)
        transition(cancelled_draft, 1, "cancelled")
        cancelled_validated = insert_execution(21)
        transition(cancelled_validated, 1, "validated", validation_id=validation)
        transition(cancelled_validated, 2, "cancelled")
        cancelled_approved = insert_execution(22)
        transition(cancelled_approved, 1, "validated", validation_id=validation)
        transition(cancelled_approved, 2, "approved", approval_id=approval)
        transition(cancelled_approved, 3, "cancelled")

        retry = insert_execution(30)
        transition(retry, 1, "validated", validation_id=validation)
        transition(retry, 2, "approved", approval_id=approval)
        transition(retry, 3, "queued")
        transition(retry, 4, "failed")
        transition(retry, 5, "queued")
        transition(retry, 6, "dispatched")
        wrong_receipt = self._psql(
            "postgres",
            f"""
            INSERT INTO sklegal_legal.execution_receipts
                (id, tenant_id, matter_id, execution_id, connector,
                 external_receipt_id, artifact_content_sha256,
                 destination_sha256, received_at, verified_at)
            VALUES ('90000000-0000-4000-8000-000000000091', '{tenant}',
                    '{matter}', '{retry}', 'synthetic', 'wrong-hash',
                    '{"c" * 64}', '{destination_sha}', clock_timestamp(),
                    clock_timestamp());
            """,
            check=False,
        )
        self.assertNotEqual(0, wrong_receipt.returncode)
        transition(retry, 7, "failed")

        direct_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.executions
            SET status = 'failed', version = version + 1 WHERE id = '{happy}';
            """,
            check=False,
        )
        direct_event = self._psql(
            "sklegal_test_alpha_one",
            f"""
            INSERT INTO sklegal_legal.execution_events
                (id, tenant_id, matter_id, execution_id, sequence_no, step,
                 occurred_at, correlation_id, actor_principal_id)
            VALUES ('90000000-0000-4000-8000-000000000092', '{tenant}',
                    '{matter}', '{happy}', 6, 'failed', clock_timestamp(),
                    'corr-direct-write', '{principal}');
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_update.returncode)
        self.assertNotEqual(0, direct_event.returncode)
        direct_communication_update = self._psql(
            "sklegal_test_alpha_one",
            f"""
            UPDATE sklegal_legal.communications
            SET status = 'failed', version = version + 1
            WHERE id = '{communication}';
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_communication_update.returncode)
        evidence = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT execution.status || ':' || execution.version || ':' ||
                   count(event.id) || ':' || count(DISTINCT receipt.id) || ':' ||
                   bool_and(event.updated_at <= execution.updated_at) || ':' ||
                   bool_and(receipt.updated_at <= execution.updated_at)
            FROM sklegal_legal.executions AS execution
            JOIN sklegal_legal.execution_events AS event
              ON event.tenant_id = execution.tenant_id
             AND event.matter_id = execution.matter_id
             AND event.execution_id = execution.id
            LEFT JOIN sklegal_legal.execution_receipts AS receipt
              ON receipt.tenant_id = execution.tenant_id
             AND receipt.matter_id = execution.matter_id
             AND receipt.execution_id = execution.id
            WHERE execution.id = '{happy}'
            GROUP BY execution.status, execution.version;
            """,
        )
        self.assertEqual("receipt_verified:6:5:1:true:true", evidence.stdout.strip())

    def test_10_work_product_gate_race_and_draft_revision_boundaries(self) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        digest = "7" * 64

        def setup_gated_candidate(suffix: int) -> tuple[str, str, str]:
            work_product = f"91000000-0000-4000-8000-{suffix:012d}"
            artifact = f"91000000-0000-4000-8001-{suffix:012d}"
            validation = f"91000000-0000-4000-8002-{suffix:012d}"
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.work_products
                    (id, tenant_id, matter_id, title, work_product_kind,
                     current_version_id, current_version_number,
                     current_content_sha256)
                VALUES ('{work_product}', '{tenant}', '{matter}',
                        'Synthetic race work product {suffix}', 'memo',
                        '{artifact}', 1, '{digest}');
                INSERT INTO sklegal_legal.work_product_versions
                    (id, tenant_id, matter_id, work_product_id, version_number,
                     content_sha256, source_artifact_id)
                VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}',
                        1, '{digest}',
                        '91000000-0000-4000-8003-{suffix:012d}');
                COMMIT;
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
                BEGIN;
                INSERT INTO sklegal_legal.validations
                    (id, tenant_id, matter_id, subject_kind,
                     subject_artifact_id, subject_artifact_version,
                     subject_content_sha256, outcome, validator_principal_id,
                     validated_at, rationale)
                VALUES ('{validation}', '{tenant}', '{matter}',
                        'work_product_version', '{artifact}', 1, '{digest}',
                        'passed', '{principal}', clock_timestamp(),
                        'Synthetic race validation.');
                INSERT INTO sklegal_legal.validation_checks
                    (tenant_id, matter_id, validation_id, check_id)
                VALUES ('{tenant}', '{matter}', '{validation}',
                        'synthetic-race-check-{suffix}');
                COMMIT;
                SELECT status FROM sklegal_legal.transition_work_product(
                    '{tenant}', '{matter}', '{work_product}', 1, 'in_review');
                """,
            )
            return work_product, artifact, validation

        draft_work_product = "91000000-0000-4000-8000-000000000021"
        draft_artifact = "91000000-0000-4000-8001-000000000021"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.work_products
                (id, tenant_id, matter_id, title, work_product_kind,
                 current_version_id, current_version_number,
                 current_content_sha256)
            VALUES ('{draft_work_product}', '{tenant}', '{matter}',
                    'Synthetic draft before edit', 'memo', '{draft_artifact}',
                    1, '{digest}');
            INSERT INTO sklegal_legal.work_product_versions
                (id, tenant_id, matter_id, work_product_id, version_number,
                 content_sha256, source_artifact_id)
            VALUES ('{draft_artifact}', '{tenant}', '{matter}',
                    '{draft_work_product}', 1, '{digest}',
                    '91000000-0000-4000-8003-000000000021');
            COMMIT;
            SELECT status || ':' || version || ':' || title
            FROM sklegal_legal.revise_work_product(
                '{tenant}', '{matter}', '{draft_work_product}', 1,
                'Synthetic draft after edit', 'memo', '{draft_artifact}', 1,
                '{digest}');
            """,
        )
        edited = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status || ':' || version || ':' || title
            FROM sklegal_legal.work_products
            WHERE id = '{draft_work_product}';
            """,
        )
        self.assertEqual("draft:2:Synthetic draft after edit", edited.stdout.strip())

        first_party = "91000000-0000-4000-8004-000000000001"
        second_party = "91000000-0000-4000-8004-000000000002"
        first_source = "91000000-0000-4000-8005-000000000001"
        second_source = "91000000-0000-4000-8005-000000000002"
        communication = "91000000-0000-4000-8006-000000000001"
        self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES
                ('{first_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"8" * 64}', 'synthetic:first-party', clock_timestamp()),
                ('{second_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"9" * 64}', 'synthetic:second-party', clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{first_party}', '{tenant}', '{matter}', 'Synthetic first',
                 'person', '{first_source}'),
                ('{second_party}', '{tenant}', '{matter}', 'Synthetic second',
                 'person', '{second_source}');
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{communication}', 'internal',
                'calendar', 'Synthetic draft communication',
                ARRAY['{first_party}'::uuid], '{draft_artifact}', 1,
                '{digest}');
            COMMIT;
            SELECT status || ':' || version || ':' || subject
            FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 1, 'outbound',
                'email', 'Synthetic edited draft communication',
                '{draft_artifact}', 1, '{digest}',
                ARRAY['{second_party}'::uuid]);
            """,
        )
        communication_evidence = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT communication.status || ':' || communication.version || ':' ||
                   communication.subject || ':' || participant.party_id
            FROM sklegal_legal.communications AS communication
            JOIN sklegal_legal.communication_participants AS participant
              ON participant.tenant_id = communication.tenant_id
             AND participant.matter_id = communication.matter_id
             AND participant.communication_id = communication.id
            WHERE communication.id = '{communication}';
            """,
        )
        self.assertEqual(
            f"draft:2:Synthetic edited draft communication:{second_party}",
            communication_evidence.stdout.strip(),
        )

        gate_first_work_product, gate_first_artifact, gate_first_validation = (
            setup_gated_candidate(101)
        )
        gate_first_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-gate-first';
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{gate_first_work_product}', 2,
                'validated', '{gate_first_validation}', NULL);
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        gate_first = subprocess.Popen(
            [
                *self._psql_command("sklegal_test_alpha_one"),
                "--command",
                gate_first_sql,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep("sklegal-s102-gate-first", gate_first)
        stale_supersede = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{gate_first_artifact}', 2,
                'superseded');
            """,
            check=False,
        )
        gate_stdout, gate_stderr = gate_first.communicate(timeout=10)
        self.assertEqual(0, gate_first.returncode, gate_stderr)
        self.assertIn("validated", gate_stdout)
        self.assertNotEqual(0, stale_supersede.returncode)
        self.assertIn("gated work product", stale_supersede.stderr)

        supersede_first_work_product, supersede_first_artifact, supersede_validation = (
            setup_gated_candidate(102)
        )
        supersede_first_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-supersede-first';
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{supersede_first_artifact}', 2,
                'superseded');
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        supersede_first = subprocess.Popen(
            [
                *self._psql_command("sklegal_test_alpha_one"),
                "--command",
                supersede_first_sql,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep("sklegal-s102-supersede-first", supersede_first)
        stale_gate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{supersede_first_work_product}', 2,
                'validated', '{supersede_validation}', NULL);
            """,
            check=False,
        )
        supersede_stdout, supersede_stderr = supersede_first.communicate(timeout=10)
        self.assertEqual(0, supersede_first.returncode, supersede_stderr)
        self.assertIn("superseded", supersede_stdout)
        self.assertNotEqual(0, stale_gate.returncode)
        self.assertIn("remain frozen", stale_gate.stderr)

    def test_10_controlled_communication_participants_are_atomic_and_sealed(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        role = "sklegal_test_alpha_one"
        first_party = "92000000-0000-4000-8000-000000000001"
        second_party = "92000000-0000-4000-8000-000000000002"
        first_source = "92000000-0000-4000-8001-000000000001"
        second_source = "92000000-0000-4000-8001-000000000002"
        communication = "92000000-0000-4000-8002-000000000001"
        rolled_back_communication = "92000000-0000-4000-8002-000000000002"
        self._psql(
            role,
            f"""
            BEGIN;
            INSERT INTO sklegal_legal.source_references
                (id, tenant_id, matter_id, source_system, source_version,
                 content_sha256, locator, observed_at)
            VALUES
                ('{first_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"a" * 64}', 'synthetic:sealed-first', clock_timestamp()),
                ('{second_source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                 '{"b" * 64}', 'synthetic:sealed-second', clock_timestamp());
            INSERT INTO sklegal_legal.parties
                (id, tenant_id, matter_id, display_name, party_kind,
                 source_reference_id)
            VALUES
                ('{first_party}', '{tenant}', '{matter}', 'Synthetic sealed first',
                 'person', '{first_source}'),
                ('{second_party}', '{tenant}', '{matter}', 'Synthetic sealed second',
                 'person', '{second_source}');
            COMMIT;
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{communication}', 'internal',
                'calendar', 'Synthetic sealed communication',
                ARRAY['{first_party}'::uuid]);
            """,
        )
        self.assertEqual(
            "f|f|t|f|t|t",
            self._psql(
                "postgres",
                f"""
                SELECT has_table_privilege('{role}', 'sklegal_legal.communications', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.communication_participants', 'INSERT'),
                       has_function_privilege('{role}',
                           'sklegal_legal.create_communication(uuid,uuid,uuid,text,text,text,uuid[],uuid,bigint,text,sklegal_legal.data_classification,sklegal_legal.record_completeness,timestamptz,timestamptz)',
                           'EXECUTE'),
                       has_function_privilege('{role}',
                           'sklegal_legal.lock_exact_work_product_version(uuid,uuid,uuid,bigint,text)',
                           'EXECUTE'),
                       to_regprocedure('sklegal_legal.advance_new_communication_relation_owner()') IS NULL,
                       to_regprocedure('sklegal_legal.seal_communication_participant_creation()') IS NULL;
                """,
            ).stdout.strip(),
        )

        direct_parent = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.communications
                (id, tenant_id, matter_id, direction, channel, subject)
            VALUES ('92000000-0000-4000-8002-000000000099', '{tenant}',
                    '{matter}', 'internal', 'calendar', 'Direct denied');
            """,
            check=False,
        )
        direct_participant = self._psql(
            role,
            f"""
            INSERT INTO sklegal_legal.communication_participants
                (tenant_id, matter_id, communication_id, party_id)
            VALUES ('{tenant}', '{matter}', '{communication}', '{second_party}');
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_parent.returncode)
        self.assertNotEqual(0, direct_participant.returncode)

        revised = self._psql(
            role,
            f"""
            SELECT status || ':' || version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 1, 'outbound',
                'email', 'Synthetic sealed revision', NULL, NULL, NULL,
                ARRAY['{second_party}'::uuid]);
            """,
        )
        self.assertEqual("draft:2", revised.stdout.strip())

        same_transaction_bypass = self._psql(
            role,
            f"""
            BEGIN;
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'internal',
                'calendar', 'Synthetic bypass attempt', NULL, NULL, NULL,
                ARRAY['{first_party}'::uuid]);
            INSERT INTO sklegal_legal.communication_participants
                (tenant_id, matter_id, communication_id, party_id)
            VALUES ('{tenant}', '{matter}', '{communication}', '{second_party}');
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, same_transaction_bypass.returncode)

        invalid_replacement = self._psql(
            role,
            f"""
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'internal',
                'calendar', 'Synthetic rollback attempt', NULL, NULL, NULL,
                ARRAY['92000000-0000-4000-8009-000000000099'::uuid]);
            """,
            check=False,
        )
        self.assertNotEqual(0, invalid_replacement.returncode)
        unchanged = self._psql(
            role,
            f"""
            SELECT communication.version || ':' || communication.subject || ':' ||
                   string_agg(participant.party_id::text, ',' ORDER BY participant.party_id)
            FROM sklegal_legal.communications AS communication
            JOIN sklegal_legal.communication_participants AS participant
              ON participant.tenant_id = communication.tenant_id
             AND participant.matter_id = communication.matter_id
             AND participant.communication_id = communication.id
            WHERE communication.id = '{communication}'
            GROUP BY communication.version, communication.subject;
            """,
        )
        self.assertEqual(
            f"2:Synthetic sealed revision:{second_party}", unchanged.stdout.strip()
        )

        duplicate_create = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.create_communication(
                '{tenant}', '{matter}', '{rolled_back_communication}', 'internal',
                'calendar', 'Synthetic duplicate rollback',
                ARRAY['{first_party}'::uuid, '{first_party}'::uuid]);
            """,
            check=False,
        )
        self.assertNotEqual(0, duplicate_create.returncode)
        self.assertEqual(
            "0",
            self._psql(
                role,
                f"SELECT count(*) FROM sklegal_legal.communications "
                f"WHERE id = '{rolled_back_communication}';",
            ).stdout.strip(),
        )

        first_revision_sql = f"""
            BEGIN;
            SET LOCAL application_name = 'sklegal-s102-communication-revision-first';
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'internal',
                'calendar', 'Synthetic concurrent winner', NULL, NULL, NULL,
                ARRAY['{first_party}'::uuid]);
            SELECT pg_sleep(1.0);
            COMMIT;
        """
        first_revision = subprocess.Popen(
            [*self._psql_command(role), "--command", first_revision_sql],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep(
            "sklegal-s102-communication-revision-first", first_revision
        )
        stale_revision = self._psql(
            role,
            f"""
            SELECT version FROM sklegal_legal.revise_communication(
                '{tenant}', '{matter}', '{communication}', 2, 'outbound',
                'email', 'Synthetic concurrent loser', NULL, NULL, NULL,
                ARRAY['{second_party}'::uuid]);
            """,
            check=False,
        )
        winner_stdout, winner_stderr = first_revision.communicate(timeout=10)
        self.assertEqual(0, first_revision.returncode, winner_stderr)
        self.assertIn("3", winner_stdout)
        self.assertNotEqual(0, stale_revision.returncode)
        self.assertIn("version conflict", stale_revision.stderr)
        final = self._psql(
            role,
            f"""
            SELECT communication.version || ':' || communication.subject || ':' ||
                   participant.party_id
            FROM sklegal_legal.communications AS communication
            JOIN sklegal_legal.communication_participants AS participant
              ON participant.tenant_id = communication.tenant_id
             AND participant.matter_id = communication.matter_id
             AND participant.communication_id = communication.id
            WHERE communication.id = '{communication}';
            """,
        )
        self.assertEqual(
            f"3:Synthetic concurrent winner:{first_party}", final.stdout.strip()
        )

    def test_10_artifact_consumer_lifecycle_serializes_and_completes(
        self,
    ) -> None:
        value = self.fixture
        tenant = value["tenant_alpha"]
        matter = value["matter_alpha_one"]
        principal = value["principal_alpha_one"]
        role = "sklegal_test_alpha_one"
        digest = "c" * 64
        destination = "d" * 64

        def setup_artifact(
            suffix: int,
        ) -> tuple[str, str, str, str, str]:
            work_product = f"93000000-0000-4000-8000-{suffix:012d}"
            artifact = f"93000000-0000-4000-8001-{suffix:012d}"
            validation = f"93000000-0000-4000-8002-{suffix:012d}"
            approval = f"93000000-0000-4000-8003-{suffix:012d}"
            participant = f"93000000-0000-4000-8004-{suffix:012d}"
            source = f"93000000-0000-4000-8005-{suffix:012d}"
            self._psql(
                role,
                f"""
                BEGIN;
                INSERT INTO sklegal_legal.source_references
                    (id, tenant_id, matter_id, source_system, source_version,
                     content_sha256, locator, observed_at)
                VALUES ('{source}', '{tenant}', '{matter}', 'synthetic', 'v1',
                        '{"e" * 64}', 'synthetic:consumer-{suffix}',
                        clock_timestamp());
                INSERT INTO sklegal_legal.parties
                    (id, tenant_id, matter_id, display_name, party_kind,
                     source_reference_id)
                VALUES ('{participant}', '{tenant}', '{matter}',
                        'Synthetic consumer {suffix}', 'person', '{source}');
                INSERT INTO sklegal_legal.work_products
                    (id, tenant_id, matter_id, title, work_product_kind,
                     current_version_id, current_version_number,
                     current_content_sha256)
                VALUES ('{work_product}', '{tenant}', '{matter}',
                        'Synthetic consumer work product {suffix}', 'memo',
                        '{artifact}', 1, '{digest}');
                INSERT INTO sklegal_legal.work_product_versions
                    (id, tenant_id, matter_id, work_product_id, version_number,
                     content_sha256, source_artifact_id)
                VALUES ('{artifact}', '{tenant}', '{matter}', '{work_product}',
                        1, '{digest}',
                        '93000000-0000-4000-8006-{suffix:012d}');
                COMMIT;
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{artifact}', 1, 'frozen');
                BEGIN;
                INSERT INTO sklegal_legal.validations
                    (id, tenant_id, matter_id, subject_kind,
                     subject_artifact_id, subject_artifact_version,
                     subject_content_sha256, outcome, validator_principal_id,
                     validated_at, rationale)
                VALUES ('{validation}', '{tenant}', '{matter}',
                        'work_product_version', '{artifact}', 1, '{digest}',
                        'passed', '{principal}', clock_timestamp(),
                        'Synthetic exact consumer validation.');
                INSERT INTO sklegal_legal.validation_checks
                    (tenant_id, matter_id, validation_id, check_id)
                VALUES ('{tenant}', '{matter}', '{validation}',
                        'synthetic.consumer.{suffix}');
                COMMIT;
                INSERT INTO sklegal_legal.approvals
                    (id, tenant_id, matter_id, subject_artifact_id,
                     subject_artifact_version, subject_content_sha256)
                VALUES ('{approval}', '{tenant}', '{matter}', '{artifact}', 1,
                        '{digest}');
                SELECT status FROM sklegal_legal.transition_approval(
                    '{tenant}', '{matter}', '{approval}', 1, 'approved',
                    'Synthetic exact consumer approval.');
                """,
            )
            return work_product, artifact, validation, approval, participant

        def insert_execution(suffix: int, artifact: str) -> str:
            execution = f"93000000-0000-4000-8007-{suffix:012d}"
            self._psql(
                role,
                f"""
                INSERT INTO sklegal_legal.executions
                    (id, tenant_id, matter_id, subject_artifact_id,
                     subject_artifact_version, subject_content_sha256,
                     destination_sha256, idempotency_key)
                VALUES ('{execution}', '{tenant}', '{matter}', '{artifact}', 1,
                        '{digest}', '{destination}',
                        'synthetic-consumer-{suffix}');
                """,
            )
            return execution

        def create_communication(suffix: int, artifact: str, participant: str) -> str:
            communication = f"93000000-0000-4000-8008-{suffix:012d}"
            self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.create_communication(
                    '{tenant}', '{matter}', '{communication}', 'outbound',
                    'email', 'Synthetic consumer communication {suffix}',
                    ARRAY['{participant}'::uuid], '{artifact}', 1, '{digest}');
                """,
            )
            return communication

        def transition_execution(
            execution: str,
            version: int,
            status: str,
            *,
            validation: str | None = None,
            approval: str | None = None,
            receipt_suffix: int | None = None,
        ) -> subprocess.CompletedProcess[str]:
            receipt_arguments = ""
            if receipt_suffix is not None:
                receipt_id = f"93000000-0000-4000-8009-{receipt_suffix:012d}"
                receipt_arguments = (
                    f", '{receipt_id}', 'synthetic-connector', "
                    f"'synthetic-consumer-receipt-{receipt_suffix}', "
                    "clock_timestamp(), clock_timestamp()"
                )
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{execution}', {version},
                    '{status}', 'consumer-{execution[-8:]}-{version}',
                    {f"'{validation}'" if validation else "NULL"},
                    {f"'{approval}'" if approval else "NULL"}
                    {receipt_arguments});
                """,
            )

        def transition_communication(
            communication: str,
            version: int,
            status: str,
            *,
            validation: str | None = None,
            approval: str | None = None,
            execution: str | None = None,
        ) -> subprocess.CompletedProcess[str]:
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{communication}', {version},
                    '{status}',
                    {f"'{validation}'" if validation else "NULL"},
                    {f"'{approval}'" if approval else "NULL"},
                    {f"'{destination}'" if execution else "NULL"},
                    {f"'{execution}'" if execution else "NULL"});
                """,
            )

        def supersede(artifact: str) -> subprocess.CompletedProcess[str]:
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{artifact}', 2, 'superseded');
                """,
                check=False,
            )

        def revoke_approval(
            approval: str, *, check: bool = False
        ) -> subprocess.CompletedProcess[str]:
            return self._psql(
                role,
                f"""
                SELECT status FROM sklegal_legal.transition_approval(
                    '{tenant}', '{matter}', '{approval}', 2, 'revoked',
                    'Synthetic prospective revocation.');
                """,
                check=check,
            )

        _, artifact, validation, approval, participant = setup_artifact(1)
        execution = insert_execution(1, artifact)
        communication = create_communication(1, artifact, participant)
        transition_execution(execution, 1, "validated", validation=validation)
        transition_communication(communication, 1, "validated", validation=validation)
        transition_execution(execution, 2, "approved", approval=approval)
        transition_communication(communication, 2, "approved", approval=approval)
        approved_revoke_denial = revoke_approval(approval)
        self.assertNotEqual(0, approved_revoke_denial.returncode)
        approved_denial = supersede(artifact)
        self.assertNotEqual(0, approved_denial.returncode)
        transition_execution(execution, 3, "queued")
        transition_communication(communication, 3, "queued", execution=execution)
        queued_revoke_denial = revoke_approval(approval)
        self.assertNotEqual(0, queued_revoke_denial.returncode)
        queued_denial = supersede(artifact)
        self.assertNotEqual(0, queued_denial.returncode)
        transition_execution(execution, 4, "dispatched")
        transition_communication(communication, 4, "dispatched")
        dispatched_revoke_denial = revoke_approval(approval)
        self.assertNotEqual(0, dispatched_revoke_denial.returncode)
        dispatched_denial = supersede(artifact)
        self.assertNotEqual(0, dispatched_denial.returncode)
        transition_execution(execution, 5, "receipt_verified", receipt_suffix=1)
        transition_communication(communication, 5, "receipt_verified")
        terminal_revoke = revoke_approval(approval, check=True)
        self.assertEqual("revoked", terminal_revoke.stdout.strip())
        historical_execution_row = self._json_rows(
            role, "sklegal_legal.executions", f"id = '{execution}'"
        )[0]
        historical_execution = reconstruct_with_metadata(
            "Execution",
            historical_execution_row,
            self._normalized_relations(role, "Execution", historical_execution_row),
        )
        self.assertEqual("approved", historical_execution.entity.approval.status)
        self.assertEqual(2, historical_execution.entity.approval.version)
        self.assertEqual(
            "revoked:approved:1",
            self._psql(
                role,
                f"""
                SELECT current.status || ':' || history.status || ':' || count(*)
                FROM sklegal_legal.approvals AS current
                JOIN sklegal_legal.approval_history AS history
                  ON history.tenant_id = current.tenant_id
                 AND history.matter_id = current.matter_id
                 AND history.approval_id = current.id
                WHERE current.id = '{approval}'
                GROUP BY current.status, history.status;
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "f|f|f",
            self._psql(
                "postgres",
                f"""
                SELECT has_table_privilege(
                           '{role}', 'sklegal_legal.approval_history', 'INSERT'),
                       has_table_privilege(
                           '{role}', 'sklegal_legal.approval_history', 'UPDATE'),
                       has_table_privilege(
                           '{role}', 'sklegal_legal.approval_history', 'DELETE');
                """,
            ).stdout.strip(),
        )
        direct_history_change = self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.approval_history
            SET rationale = 'Synthetic forbidden rewrite'
            WHERE tenant_id = '{tenant}' AND matter_id = '{matter}'
              AND approval_id = '{approval}' AND approval_version = 2;
            """,
            check=False,
        )
        self.assertNotEqual(0, direct_history_change.returncode)
        self.assertIn("append-only", direct_history_change.stderr)
        revoked_execution = insert_execution(90, artifact)
        revoked_communication = create_communication(90, artifact, participant)
        transition_execution(revoked_execution, 1, "validated", validation=validation)
        transition_communication(
            revoked_communication, 1, "validated", validation=validation
        )
        revoked_execution_progress = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{revoked_execution}', 2, 'approved',
                'revoked-approval-execution', NULL, '{approval}');
            """,
            check=False,
        )
        revoked_communication_progress = self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_communication(
                '{tenant}', '{matter}', '{revoked_communication}', 2,
                'approved', NULL, '{approval}', NULL, NULL);
            """,
            check=False,
        )
        self.assertNotEqual(0, revoked_execution_progress.returncode)
        self.assertNotEqual(0, revoked_communication_progress.returncode)
        transition_execution(revoked_execution, 2, "cancelled")
        transition_communication(revoked_communication, 2, "cancelled")
        terminal_supersede = supersede(artifact)
        self.assertEqual(0, terminal_supersede.returncode, terminal_supersede.stderr)

        _, retry_artifact, retry_validation, retry_approval, retry_party = (
            setup_artifact(2)
        )
        retry_execution = insert_execution(2, retry_artifact)
        retry_communication = create_communication(2, retry_artifact, retry_party)
        transition_execution(
            retry_execution, 1, "validated", validation=retry_validation
        )
        transition_communication(
            retry_communication, 1, "validated", validation=retry_validation
        )
        transition_execution(retry_execution, 2, "approved", approval=retry_approval)
        transition_communication(
            retry_communication, 2, "approved", approval=retry_approval
        )
        transition_execution(retry_execution, 3, "queued")
        transition_communication(
            retry_communication, 3, "queued", execution=retry_execution
        )
        transition_execution(retry_execution, 4, "dispatched")
        transition_communication(retry_communication, 4, "dispatched")
        transition_execution(retry_execution, 5, "failed")
        transition_communication(retry_communication, 5, "failed")
        failed_revoke_denial = revoke_approval(retry_approval)
        self.assertNotEqual(0, failed_revoke_denial.returncode)
        retryable_denial = supersede(retry_artifact)
        self.assertNotEqual(0, retryable_denial.returncode)
        transition_execution(retry_execution, 6, "queued")
        transition_communication(retry_communication, 6, "queued")
        transition_execution(retry_execution, 7, "dispatched")
        transition_communication(retry_communication, 7, "dispatched")
        transition_execution(retry_execution, 8, "receipt_verified", receipt_suffix=2)
        transition_communication(retry_communication, 8, "receipt_verified")
        retry_terminal_revoke = revoke_approval(retry_approval, check=True)
        self.assertEqual("revoked", retry_terminal_revoke.stdout.strip())
        retry_terminal = supersede(retry_artifact)
        self.assertEqual(0, retry_terminal.returncode, retry_terminal.stderr)

        (
            reset_work_product,
            reset_artifact,
            reset_validation,
            reset_approval,
            _,
        ) = setup_artifact(7)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 1, 'in_review');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 2, 'validated',
                '{reset_validation}', NULL);
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 3, 'approved',
                NULL, '{reset_approval}');
            """,
        )
        work_product_revoke_denial = revoke_approval(reset_approval)
        self.assertNotEqual(0, work_product_revoke_denial.returncode)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.revise_work_product(
                '{tenant}', '{matter}', '{reset_work_product}', 4,
                'Synthetic consumer work product 7 reset', 'memo',
                '{reset_artifact}', 1, '{digest}');
            """,
        )
        reset_revoke = revoke_approval(reset_approval, check=True)
        self.assertEqual("revoked", reset_revoke.stdout.strip())

        for consumer_kind, suffix in (("execution", 3), ("communication", 4)):
            _, race_artifact, race_validation, _, race_party = setup_artifact(suffix)
            consumer = (
                insert_execution(suffix, race_artifact)
                if consumer_kind == "execution"
                else create_communication(suffix, race_artifact, race_party)
            )
            gate_statement = (
                f"SELECT status FROM sklegal_legal.transition_execution("
                f"'{tenant}', '{matter}', '{consumer}', 1, 'validated', "
                f"'race-{consumer_kind}-{suffix}', '{race_validation}', NULL);"
                if consumer_kind == "execution"
                else f"SELECT status FROM sklegal_legal.transition_communication("
                f"'{tenant}', '{matter}', '{consumer}', 1, 'validated', "
                f"'{race_validation}', NULL, NULL, NULL);"
            )
            application_name = f"sklegal-s102-{consumer_kind}-gate-first"
            gate_first_sql = f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                {gate_statement}
                SELECT pg_sleep(1.0);
                COMMIT;
            """
            gate_first = subprocess.Popen(
                [*self._psql_command(role), "--command", gate_first_sql],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, gate_first)
            blocked_supersede = supersede(race_artifact)
            gate_stdout, gate_stderr = gate_first.communicate(timeout=10)
            self.assertEqual(0, gate_first.returncode, gate_stderr)
            self.assertIn("validated", gate_stdout)
            self.assertNotEqual(0, blocked_supersede.returncode)

        for consumer_kind, suffix in (("execution", 5), ("communication", 6)):
            _, race_artifact, race_validation, _, race_party = setup_artifact(suffix)
            application_name = f"sklegal-s102-{consumer_kind}-supersede-first"
            supersede_first_sql = f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                SELECT status FROM sklegal_legal.transition_work_product_version(
                    '{tenant}', '{matter}', '{race_artifact}', 2, 'superseded');
                SELECT pg_sleep(1.0);
                COMMIT;
            """
            supersede_first = subprocess.Popen(
                [*self._psql_command(role), "--command", supersede_first_sql],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, supersede_first)
            if consumer_kind == "execution":
                blocked_consumer = self._psql(
                    role,
                    f"""
                    BEGIN;
                    INSERT INTO sklegal_legal.executions
                        (id, tenant_id, matter_id, subject_artifact_id,
                         subject_artifact_version, subject_content_sha256,
                         destination_sha256, idempotency_key)
                    VALUES ('93000000-0000-4000-8007-{suffix:012d}',
                            '{tenant}', '{matter}', '{race_artifact}', 1,
                            '{digest}', '{destination}',
                            'synthetic-supersede-first-{suffix}');
                    SELECT status FROM sklegal_legal.transition_execution(
                        '{tenant}', '{matter}',
                        '93000000-0000-4000-8007-{suffix:012d}', 1,
                        'validated', 'supersede-first-execution',
                        '{race_validation}', NULL);
                    COMMIT;
                    """,
                    check=False,
                )
            else:
                blocked_consumer = self._psql(
                    role,
                    f"""
                    BEGIN;
                    SELECT status FROM sklegal_legal.create_communication(
                        '{tenant}', '{matter}',
                        '93000000-0000-4000-8008-{suffix:012d}', 'outbound',
                        'email', 'Synthetic supersede-first communication',
                        ARRAY['{race_party}'::uuid], '{race_artifact}', 1,
                        '{digest}');
                    SELECT status FROM sklegal_legal.transition_communication(
                        '{tenant}', '{matter}',
                        '93000000-0000-4000-8008-{suffix:012d}', 1,
                        'validated', '{race_validation}', NULL, NULL, NULL);
                    COMMIT;
                    """,
                    check=False,
                )
            supersede_stdout, supersede_stderr = supersede_first.communicate(timeout=10)
            self.assertEqual(0, supersede_first.returncode, supersede_stderr)
            self.assertIn("superseded", supersede_stdout)
            self.assertNotEqual(0, blocked_consumer.returncode)
            self.assertIn("superseded", blocked_consumer.stderr)

        for consumer_kind, suffix in (
            ("work_product", 8),
            ("execution", 9),
            ("communication", 10),
        ):
            (
                race_work_product,
                race_artifact,
                race_validation,
                race_approval,
                race_party,
            ) = setup_artifact(suffix)
            if consumer_kind == "work_product":
                self._psql(
                    role,
                    f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 1,
                        'in_review');
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 2,
                        'validated', '{race_validation}', NULL);
                    """,
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 3,
                        'approved', NULL, '{race_approval}');
                """
            elif consumer_kind == "execution":
                race_consumer = insert_execution(suffix, race_artifact)
                transition_execution(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_execution(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', 'approval-race-{suffix}', NULL,
                        '{race_approval}');
                """
            else:
                race_consumer = create_communication(suffix, race_artifact, race_party)
                transition_communication(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_communication(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', NULL, '{race_approval}', NULL, NULL);
                """
            application_name = f"sklegal-s102-{consumer_kind}-approval-first"
            gate_first = subprocess.Popen(
                [
                    *self._psql_command(role),
                    "--command",
                    f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    {gate_statement}
                    SELECT pg_sleep(1.0);
                    COMMIT;
                    """,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, gate_first)
            blocked_revoke = revoke_approval(race_approval)
            gate_stdout, gate_stderr = gate_first.communicate(timeout=10)
            self.assertEqual(0, gate_first.returncode, gate_stderr)
            self.assertIn("approved", gate_stdout)
            self.assertNotEqual(0, blocked_revoke.returncode)

        for consumer_kind, suffix in (
            ("work_product", 11),
            ("execution", 12),
            ("communication", 13),
        ):
            (
                race_work_product,
                race_artifact,
                race_validation,
                race_approval,
                race_party,
            ) = setup_artifact(suffix)
            if consumer_kind == "work_product":
                self._psql(
                    role,
                    f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 1,
                        'in_review');
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 2,
                        'validated', '{race_validation}', NULL);
                    """,
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_work_product(
                        '{tenant}', '{matter}', '{race_work_product}', 3,
                        'approved', NULL, '{race_approval}');
                """
            elif consumer_kind == "execution":
                race_consumer = insert_execution(suffix, race_artifact)
                transition_execution(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_execution(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', 'revocation-race-{suffix}', NULL,
                        '{race_approval}');
                """
            else:
                race_consumer = create_communication(suffix, race_artifact, race_party)
                transition_communication(
                    race_consumer, 1, "validated", validation=race_validation
                )
                gate_statement = f"""
                    SELECT status FROM sklegal_legal.transition_communication(
                        '{tenant}', '{matter}', '{race_consumer}', 2,
                        'approved', NULL, '{race_approval}', NULL, NULL);
                """
            application_name = f"sklegal-s102-{consumer_kind}-revocation-first"
            revoke_first = subprocess.Popen(
                [
                    *self._psql_command(role),
                    "--command",
                    f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    SELECT status FROM sklegal_legal.transition_approval(
                        '{tenant}', '{matter}', '{race_approval}', 2,
                        'revoked', 'Synthetic revocation-first race.');
                    SELECT pg_sleep(1.0);
                    COMMIT;
                    """,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, revoke_first)
            blocked_gate = self._psql(role, gate_statement, check=False)
            revoke_stdout, revoke_stderr = revoke_first.communicate(timeout=10)
            self.assertEqual(0, revoke_first.returncode, revoke_stderr)
            self.assertIn("revoked", revoke_stdout)
            self.assertNotEqual(0, blocked_gate.returncode)

        (
            _,
            terminal_artifact,
            terminal_validation,
            terminal_approval,
            terminal_party,
        ) = setup_artifact(14)
        terminal_execution = insert_execution(14, terminal_artifact)
        terminal_communication = create_communication(
            14, terminal_artifact, terminal_party
        )
        transition_execution(
            terminal_execution, 1, "validated", validation=terminal_validation
        )
        transition_communication(
            terminal_communication, 1, "validated", validation=terminal_validation
        )
        transition_execution(
            terminal_execution, 2, "approved", approval=terminal_approval
        )
        transition_communication(
            terminal_communication, 2, "approved", approval=terminal_approval
        )
        transition_execution(terminal_execution, 3, "queued")
        transition_communication(
            terminal_communication, 3, "queued", execution=terminal_execution
        )
        transition_execution(terminal_execution, 4, "dispatched")
        transition_communication(terminal_communication, 4, "dispatched")
        application_name = "sklegal-s102-terminal-first-revocation"
        terminal_first = subprocess.Popen(
            [
                *self._psql_command(role),
                "--command",
                f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{terminal_execution}', 5,
                    'receipt_verified', 'terminal-first-receipt', NULL, NULL,
                    '93000000-0000-4000-8009-000000000014',
                    'synthetic-connector', 'terminal-first-receipt',
                    clock_timestamp(), clock_timestamp());
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{terminal_communication}', 5,
                    'receipt_verified', NULL, NULL, NULL, NULL);
                SELECT pg_sleep(1.0);
                COMMIT;
                """,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep(application_name, terminal_first)
        terminal_revoke = revoke_approval(terminal_approval, check=True)
        terminal_stdout, terminal_stderr = terminal_first.communicate(timeout=10)
        self.assertEqual(0, terminal_first.returncode, terminal_stderr)
        self.assertIn("receipt_verified", terminal_stdout)
        self.assertEqual("revoked", terminal_revoke.stdout.strip())

        (
            _,
            reverse_artifact,
            reverse_validation,
            reverse_approval,
            reverse_party,
        ) = setup_artifact(15)
        reverse_execution = insert_execution(15, reverse_artifact)
        reverse_communication = create_communication(
            15, reverse_artifact, reverse_party
        )
        transition_execution(
            reverse_execution, 1, "validated", validation=reverse_validation
        )
        transition_communication(
            reverse_communication, 1, "validated", validation=reverse_validation
        )
        transition_execution(
            reverse_execution, 2, "approved", approval=reverse_approval
        )
        transition_communication(
            reverse_communication, 2, "approved", approval=reverse_approval
        )
        transition_execution(reverse_execution, 3, "queued")
        transition_communication(
            reverse_communication, 3, "queued", execution=reverse_execution
        )
        transition_execution(reverse_execution, 4, "dispatched")
        transition_communication(reverse_communication, 4, "dispatched")
        reverse_revoke_denial = revoke_approval(reverse_approval)
        self.assertNotEqual(0, reverse_revoke_denial.returncode)
        transition_execution(
            reverse_execution, 5, "receipt_verified", receipt_suffix=15
        )
        transition_communication(reverse_communication, 5, "receipt_verified")
        reverse_terminal_revoke = revoke_approval(reverse_approval, check=True)
        self.assertEqual("revoked", reverse_terminal_revoke.stdout.strip())

        (
            reset_race_work_product,
            reset_race_artifact,
            reset_race_validation,
            reset_race_approval,
            _,
        ) = setup_artifact(16)
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_race_work_product}', 1,
                'in_review');
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_race_work_product}', 2,
                'validated', '{reset_race_validation}', NULL);
            SELECT status FROM sklegal_legal.transition_work_product(
                '{tenant}', '{matter}', '{reset_race_work_product}', 3,
                'approved', NULL, '{reset_race_approval}');
            """,
        )
        application_name = "sklegal-s102-work-product-reset-first"
        reset_first = subprocess.Popen(
            [
                *self._psql_command(role),
                "--command",
                f"""
                BEGIN;
                SET LOCAL application_name = '{application_name}';
                SELECT status FROM sklegal_legal.revise_work_product(
                    '{tenant}', '{matter}', '{reset_race_work_product}', 4,
                    'Synthetic reset-first work product', 'memo',
                    '{reset_race_artifact}', 1, '{digest}');
                SELECT pg_sleep(1.0);
                COMMIT;
                """,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_for_sleep(application_name, reset_first)
        reset_race_revoke = revoke_approval(reset_race_approval, check=True)
        reset_stdout, reset_stderr = reset_first.communicate(timeout=10)
        self.assertEqual(0, reset_first.returncode, reset_stderr)
        self.assertIn("in_review", reset_stdout)
        self.assertEqual("revoked", reset_race_revoke.stdout.strip())

        (
            _,
            progression_artifact,
            progression_validation,
            progression_approval,
            progression_party,
        ) = setup_artifact(17)
        progression_execution = insert_execution(17, progression_artifact)
        progression_communication = create_communication(
            17, progression_artifact, progression_party
        )
        transition_execution(
            progression_execution,
            1,
            "validated",
            validation=progression_validation,
        )
        transition_communication(
            progression_communication,
            1,
            "validated",
            validation=progression_validation,
        )
        transition_execution(
            progression_execution, 2, "approved", approval=progression_approval
        )
        transition_communication(
            progression_communication,
            2,
            "approved",
            approval=progression_approval,
        )
        progression_stages = (
            (
                "queue",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 3,
                    'queued', 'progression-queue');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 3,
                    'queued', NULL, NULL, '{destination}',
                    '{progression_execution}');
                """,
            ),
            (
                "dispatch",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 4,
                    'dispatched', 'progression-dispatch');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 4,
                    'dispatched', NULL, NULL, NULL, NULL);
                """,
            ),
            (
                "fail",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 5,
                    'failed', 'progression-fail');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 5,
                    'failed', NULL, NULL, NULL, NULL);
                """,
            ),
            (
                "retry",
                f"""
                SELECT status FROM sklegal_legal.transition_execution(
                    '{tenant}', '{matter}', '{progression_execution}', 6,
                    'queued', 'progression-retry');
                SELECT status FROM sklegal_legal.transition_communication(
                    '{tenant}', '{matter}', '{progression_communication}', 6,
                    'queued', NULL, NULL, NULL, NULL);
                """,
            ),
        )
        for stage, statements in progression_stages:
            application_name = f"sklegal-s102-{stage}-first-revocation"
            stage_first = subprocess.Popen(
                [
                    *self._psql_command(role),
                    "--command",
                    f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    {statements}
                    SELECT pg_sleep(1.0);
                    COMMIT;
                    """,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_sleep(application_name, stage_first)
            progression_revoke = revoke_approval(progression_approval)
            stage_stdout, stage_stderr = stage_first.communicate(timeout=10)
            self.assertEqual(0, stage_first.returncode, stage_stderr)
            self.assertIn(
                "queued" if stage in {"queue", "retry"} else stage + "ed",
                stage_stdout,
            )
            self.assertNotEqual(0, progression_revoke.returncode)
        transition_execution(progression_execution, 7, "dispatched")
        transition_communication(progression_communication, 7, "dispatched")
        transition_execution(
            progression_execution, 8, "receipt_verified", receipt_suffix=17
        )
        transition_communication(progression_communication, 8, "receipt_verified")
        progression_terminal_revoke = revoke_approval(progression_approval, check=True)
        self.assertEqual("revoked", progression_terminal_revoke.stdout.strip())

    def test_11_canonical_entities_write_first_through_declared_authorities(
        self,
    ) -> None:
        self.maxDiff = None
        contract = build_fresh_contract()
        expected_entities = dict(contract.entities)
        payloads = {
            name: decompose(entity, contract.metadata[name])
            for name, entity in contract.entities.items()
            if name not in {"Execution", "ExecutionReceipt"}
        }
        final_execution_template = contract.entities["Execution"]
        draft_execution_data = final_execution_template.model_dump(mode="python")
        draft_execution_data.update(
            {
                "version": 1,
                "updated_at": draft_execution_data["created_at"],
                "validation_result": None,
                "approval": None,
                "events": (),
                "receipt": None,
                "status": sklegal_domain.ExecutionStatus.DRAFT,
            }
        )
        draft_execution = type(final_execution_template).model_validate(
            draft_execution_data, strict=True
        )
        execution_insert = decompose(
            draft_execution,
            PersistenceMetadata(
                scalar={
                    "validation_result_id": None,
                    "approval_id": None,
                    "approval_version": None,
                },
                relations={
                    "validation_result": (),
                    "approval": (),
                    "events": (),
                    "receipt": (),
                },
            ),
        )
        payloads["Execution"] = execution_insert
        auxiliary = tuple(
            decompose(entity, metadata)
            for entity, metadata in zip(
                contract.auxiliary_entities,
                contract.auxiliary_metadata,
                strict=True,
            )
        )
        self.assertEqual(set(MAPPINGS) - {"ExecutionReceipt"}, set(payloads))
        self.assertEqual(30, len(payloads))

        tenant = str(contract.tenant_id)
        principal = str(contract.principal_id)
        matter = str(contract.matter_id)
        role = contract.runtime_role
        administrative_entities = ("Tenant", "Client", "Engagement", "Matter")
        administrative_sql = "\n".join(
            insert_statement(payloads[name].table, payloads[name].row)
            for name in administrative_entities
        )
        self._psql(
            "postgres",
            f"""
            BEGIN;
            {administrative_sql}
            INSERT INTO sklegal_identity.principals
                (id, tenant_id, principal_kind, display_name, status)
            VALUES ('{principal}', '{tenant}', 'human',
                    'Synthetic Fresh Principal', 'active');
            INSERT INTO sklegal_identity.tenant_memberships
                (tenant_id, principal_id, membership_role)
            VALUES ('{tenant}', '{principal}', 'administrator');
            INSERT INTO sklegal_legal.matter_memberships
                (tenant_id, matter_id, principal_id, membership_role)
            VALUES ('{tenant}', '{matter}', '{principal}', 'administrator');
            CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOINHERIT NOBYPASSRLS NOREPLICATION;
            COMMIT;
            """,
        )
        self._provision(role, tenant, principal)

        runtime_base_order = (
            "Forum",
            "Proceeding",
            "Party",
            "PartyRole",
            "MatterEvent",
            "Transaction",
            "FactAssertion",
            "TensionGroup",
            "EvidenceItem",
            "CustodyEvent",
            "Authority",
            "Issue",
            "Claim",
            "Defense",
            "Element",
            "Remedy",
            "DeadlineCalculation",
            "Deadline",
            "Task",
            "Communication",
            "WorkProduct",
            "WorkProductVersion",
        )
        base_payloads = [payloads[name] for name in runtime_base_order]
        base_payloads.extend(auxiliary)
        relation_sql = tuple(
            statement
            for payload in base_payloads
            if payload.entity_name != "Communication"
            for statement in relation_statements(payload)
        )
        source_sql = tuple(
            dict.fromkeys(
                statement
                for statement in relation_sql
                if statement.startswith("INSERT INTO sklegal_legal.source_references")
            )
        )
        link_sql = tuple(
            statement
            for statement in relation_sql
            if not statement.startswith("INSERT INTO sklegal_legal.source_references")
        )
        base_sql: list[str] = list(source_sql)
        for name in runtime_base_order:
            payload = payloads[name]
            base_sql.extend(auxiliary_statements(payload))
            if name == "Communication":
                base_sql.append(create_communication_statement(payload))
                continue
            overrides = {"status": "draft"} if name == "WorkProductVersion" else None
            base_sql.append(
                insert_statement(payload.table, payload.row, overrides=overrides)
            )
            if name == "FactAssertion":
                base_sql.append(insert_statement(auxiliary[0].table, auxiliary[0].row))
            if name == "Element":
                base_sql.append(insert_statement(auxiliary[1].table, auxiliary[1].row))
        self._psql(
            role,
            "BEGIN;\n" + "\n".join(base_sql) + "\n" + "\n".join(link_sql) + "\nCOMMIT;",
        )

        artifact = payloads["WorkProductVersion"]
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_work_product_version(
                '{tenant}', '{matter}', '{artifact.row["id"]}', 1, 'frozen');
            """,
        )

        validation = payloads["ValidationResult"]
        self._psql(
            role,
            "BEGIN;\n"
            + insert_statement(validation.table, validation.row)
            + "\n"
            + "\n".join(relation_statements(validation))
            + "\nCOMMIT;",
        )

        approval = payloads["Approval"]
        approval_reason = contract.entities["Approval"].rationale or ""
        self._psql(
            role,
            insert_statement(
                approval.table,
                approval.row,
                overrides={
                    "status": "pending",
                    "reviewer_principal_id": None,
                    "decided_at": None,
                    "rationale": None,
                    "revoker_principal_id": None,
                    "revocation_rationale": None,
                    "revoked_at": None,
                },
            )
            + f"""
            SELECT status FROM sklegal_legal.transition_approval(
                '{tenant}', '{matter}', '{approval.row["id"]}', 1,
                'approved', '{approval_reason.replace("'", "''")}');
            """,
        )

        execution = payloads["Execution"]
        execution_id = str(execution.row["id"])
        self._psql(
            role,
            insert_statement(
                execution.table,
                execution.row,
                overrides={
                    "status": "draft",
                    "validation_result_id": None,
                    "approval_id": None,
                },
            )
            + f"""
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 1, 'validated',
                'synthetic-fresh-validated', '{validation.row["id"]}', NULL);
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 2, 'approved',
                'synthetic-fresh-approved', NULL, '{approval.row["id"]}');
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 3, 'queued',
                'synthetic-fresh-queued');
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 4, 'dispatched',
                'synthetic-fresh-dispatched');
            """,
        )

        receipt_evidence_at = datetime.fromisoformat(
            self._psql(
                role,
                f"""
                SELECT GREATEST(
                    clock_timestamp(), event.occurred_at + interval '1 microsecond'
                )
                FROM sklegal_legal.execution_events AS event
                WHERE event.tenant_id = '{tenant}'
                  AND event.matter_id = '{matter}'
                  AND event.execution_id = '{execution_id}'
                  AND event.step = 'dispatched';
                """,
            ).stdout.strip()
        )
        phase_receipt_data = contract.entities["ExecutionReceipt"].model_dump(
            mode="python"
        )
        phase_receipt_data.update(
            {
                "created_at": receipt_evidence_at,
                "updated_at": receipt_evidence_at,
                "received_at": receipt_evidence_at,
                "verified_at": receipt_evidence_at,
            }
        )
        phase_receipt = type(contract.entities["ExecutionReceipt"]).model_validate(
            phase_receipt_data, strict=True
        )
        receipt_payload = decompose(
            phase_receipt, contract.metadata["ExecutionReceipt"]
        )
        payloads["ExecutionReceipt"] = receipt_payload

        final_execution_data = final_execution_template.model_dump(mode="python")
        final_events = list(final_execution_data["events"])
        final_events[-1].update(
            {
                "created_at": receipt_evidence_at,
                "updated_at": receipt_evidence_at,
                "occurred_at": receipt_evidence_at,
            }
        )
        final_execution_data.update(
            {
                "events": tuple(final_events),
                "receipt": phase_receipt,
                "updated_at": receipt_evidence_at,
            }
        )
        final_execution = type(final_execution_template).model_validate(
            final_execution_data, strict=True
        )
        payloads["Execution"] = decompose(
            final_execution, contract.metadata["Execution"]
        )
        expected_entities["Execution"] = final_execution
        expected_entities["ExecutionReceipt"] = phase_receipt
        receipt = receipt_payload.row
        self._psql(
            role,
            f"""
            SELECT status FROM sklegal_legal.transition_execution(
                '{tenant}', '{matter}', '{execution_id}', 5,
                'receipt_verified', 'synthetic-fresh-receipt_verified',
                NULL, NULL, '{receipt["id"]}',
                '{receipt["connector"]}', '{receipt["external_receipt_id"]}',
                '{receipt["received_at"].isoformat()}',
                '{receipt["verified_at"].isoformat()}');
            """,
        )
        self.assertEqual(set(MAPPINGS), set(payloads))
        self.assertEqual(31, len(payloads))

        write_authority = {
            name: payload.write_contract.authority for name, payload in payloads.items()
        }
        self.assertEqual(31, len(write_authority))
        self.assertEqual("administrative_bootstrap", write_authority["Tenant"])
        self.assertEqual("controlled_writer", write_authority["ExecutionEvent"])
        self.assertEqual("controlled_writer", write_authority["ExecutionReceipt"])
        receipt_inputs = set(receipt_payload.write_contract.canonical_input_paths)
        receipt_outputs = set(receipt_payload.write_contract.database_output_paths)
        self.assertTrue({"row.received_at", "row.verified_at"} <= receipt_inputs)
        self.assertFalse({"row.received_at", "row.verified_at"} & receipt_outputs)
        for entity_name, payload in payloads.items():
            with self.subTest(entity=entity_name, contract="write-disposition"):
                canonical_inputs = set(payload.write_contract.canonical_input_paths)
                database_outputs = set(payload.write_contract.database_output_paths)
                self.assertTrue(canonical_inputs)
                self.assertFalse(canonical_inputs & database_outputs)
        self.assertEqual(
            "f|f|f|f|f|f|f|f|t",
            self._psql(
                "postgres",
                f"""
                SELECT has_table_privilege('{role}', 'sklegal_identity.tenants', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.clients', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.engagements', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.matters', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.execution_events', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.execution_receipts', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.communications', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.communication_participants', 'INSERT'),
                       has_table_privilege('{role}', 'sklegal_legal.executions', 'INSERT');
                """,
            ).stdout.strip(),
        )

        restored: dict[str, DomainEntity] = {}
        retained: dict[str, PersistenceMetadata] = {}
        for entity_name, expected_entity in expected_entities.items():
            mapping = MAPPINGS[entity_name]
            read_relation = mapping.read_relation or mapping.table
            if entity_name == "ExecutionEvent":
                where = f"execution_id = '{execution_id}' AND sequence_no = 1"
            else:
                where = f"id = '{expected_entity.id}'"
            rows = self._json_rows(role, read_relation, where)
            self.assertEqual(1, len(rows), entity_name)
            row = rows[0]
            relations = self._normalized_relations(role, entity_name, row)
            reconstruction = reconstruct_with_metadata(entity_name, row, relations)
            actual = reconstruction.entity.model_dump(mode="python")
            expected = expected_entity.model_dump(mode="python")
            normalized_expected = self._normalize_controlled_entity_outputs(
                expected, actual
            )
            self.assertEqual(normalized_expected, actual, entity_name)
            normalized_metadata = self._normalize_controlled_metadata_outputs(
                contract.metadata[entity_name], reconstruction.metadata
            )
            self.assertEqual(
                dict(normalized_metadata.scalar),
                dict(reconstruction.metadata.scalar),
                f"{entity_name} scalar persistence metadata",
            )
            self.assertEqual(
                set(normalized_metadata.relations),
                set(reconstruction.metadata.relations),
                f"{entity_name} relation metadata names",
            )
            for (
                relation_name,
                expected_relation_rows,
            ) in normalized_metadata.relations.items():
                self.assertEqual(
                    tuple(dict(item) for item in expected_relation_rows),
                    tuple(
                        dict(item)
                        for item in reconstruction.metadata.relations[relation_name]
                    ),
                    f"{entity_name}.{relation_name} persistence metadata",
                )
            restored[entity_name] = reconstruction.entity
            retained[entity_name] = reconstruction.metadata
        self.assertEqual(set(MAPPINGS), set(restored))
        self.assertEqual(31, len(retained))
        restored_execution = restored["Execution"]
        self.assertEqual(
            tuple(range(1, 6)),
            tuple(
                retained["Execution"]
                .relations["events"][index]["nested_metadata"]
                .scalar["sequence_no"]
                for index in range(len(restored_execution.events))
            ),
        )

    def test_11_every_domain_entity_round_trips_through_rls(self) -> None:
        role = "sklegal_test_alpha_one"
        restored: dict[str, DomainEntity] = {}
        retained_metadata: dict[str, PersistenceMetadata] = {}
        decomposed_count = 0
        for entity_name, mapping in MAPPINGS.items():
            read_relation = mapping.read_relation or mapping.table
            rows = self._json_rows(role, read_relation, order_by="id")
            self.assertTrue(rows, entity_name)
            row = rows[0]
            relations = self._normalized_relations(role, entity_name, row)
            reconstruction = reconstruct_with_metadata(entity_name, row, relations)
            entity = reconstruction.entity
            canonical = entity.model_dump(mode="python")
            self.assertEqual(
                canonical,
                type(entity)
                .model_validate(canonical, strict=True)
                .model_dump(mode="python"),
                entity_name,
            )
            write_payload = decompose(entity, reconstruction.metadata)
            self.assertEqual(mapping.table, write_payload.table, entity_name)
            self.assertEqual(row, write_payload.row, entity_name)
            self.assertEqual(set(relations), set(write_payload.relations), entity_name)
            for relation_name, relation_rows in relations.items():
                self.assertEqual(
                    self._canonical_container(relation_rows),
                    self._canonical_container(write_payload.relations[relation_name]),
                    f"{entity_name}.{relation_name}",
                )
            decomposed_count += 1
            restored[entity_name] = entity
            retained_metadata[entity_name] = reconstruction.metadata
        self.assertEqual(set(MAPPINGS), set(restored))
        self.assertEqual(31, decomposed_count)

        self.assertIn(
            "import_batch_id", retained_metadata["Matter"].relations["aliases"][0]
        )
        self.assertIn("current_version_number", retained_metadata["WorkProduct"].scalar)
        self.assertIn(
            "work_product_version_number",
            retained_metadata["Communication"].scalar,
        )
        self.assertIn("sequence_no", retained_metadata["ExecutionEvent"].scalar)

        authority_mapping = MAPPINGS["Authority"]
        self.assertIsNotNone(authority_mapping.history_relation)
        authority_history = self._json_rows(
            role, authority_mapping.history_relation or "", order_by="id, version"
        )
        grouped_history: dict[UUID, list[dict[str, Any]]] = {}
        for history_row in authority_history:
            grouped_history.setdefault(history_row["id"], []).append(history_row)
        versioned_history = max(grouped_history.values(), key=len)
        self.assertGreaterEqual(len(versioned_history), 2)
        self.assertIsNotNone(versioned_history[-2]["system_to"])
        self.assertIsNone(versioned_history[-1]["system_to"])
        for history_row in versioned_history:
            authority_relations = self._normalized_relations(
                role, "Authority", history_row
            )
            history_reconstruction = reconstruct_with_metadata(
                "Authority", history_row, authority_relations
            )
            history_write = decompose(
                history_reconstruction.entity, history_reconstruction.metadata
            )
            self.assertEqual(history_row, history_write.row)
        current_authority = self._json_rows(
            role,
            authority_mapping.read_relation or "",
            f"id = '{versioned_history[-1]['id']}'",
        )
        self.assertEqual(1, len(current_authority))
        self.assertEqual(versioned_history[-1], current_authority[0])

        matter_mapping = MAPPINGS["Matter"]
        matter_row = self._json_rows(role, matter_mapping.table)[0]
        with self.assertRaisesRegex(
            MappingContractError, "missing normalized relation"
        ):
            reconstruct("Matter", matter_row, {})
        with self.assertRaisesRegex(MappingContractError, "missing columns"):
            reconstruct("Tenant", {"id": matter_row["id"]}, {})

    def test_12_audit_chain_is_scoped_append_only_and_suppresses_content(self) -> None:
        value = self.fixture
        insert_sql = f"""
            INSERT INTO sklegal_audit.events
                (id, tenant_id, matter_id, principal_id, action, resource_kind,
                 correlation_id, event_sha256, occurred_at)
            VALUES ('a0000000-0000-4000-8000-000000000001',
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    '{value["principal_alpha_one"]}', 'spoof', 'matter',
                    'a0000000-0000-4000-8000-000000000002',
                    '{"d" * 64}', clock_timestamp());
        """
        runtime = self._psql("sklegal_test_alpha_one", insert_sql, check=False)
        administrator = self._psql("postgres", insert_sql, check=False)
        owner = self._psql("sklegal_migrator", insert_sql, check=False)
        self.assertNotEqual(0, runtime.returncode)
        self.assertNotEqual(0, administrator.returncode)
        self.assertIn("controlled audit chain writer", administrator.stderr)
        self.assertNotEqual(0, owner.returncode)

        run_id = "a6100000-0000-4000-8000-000000000001"
        first = json.loads(
            self._append_audit_event(
                event_id="a6100000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6100000-0000-4000-8000-000000000021",
                span_id="0000000000000001",
                boundary="api",
                action="audit.synthetic.api",
                attributes='{"event_schema":"sklegal-audit-event/v1"}',
            ).stdout
        )
        second = json.loads(
            self._append_audit_event(
                event_id="a6100000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6100000-0000-4000-8000-000000000021",
                span_id="0000000000000002",
                boundary="workflow",
                action="audit.synthetic.workflow",
                attributes=(
                    '{"operation":"read","resource_version":1,'
                    '"status_code":200,"retry_count":0}'
                ),
            ).stdout
        )
        self.assertEqual(first["event_sequence"] + 1, second["event_sequence"])
        self.assertEqual(first["event_sha256"], second["previous_event_sha256"])
        self.assertEqual(first["event_id"], first["outbox_id"])
        self.assertEqual(
            "api,workflow",
            self._psql(
                "sklegal_test_alpha_one",
                f"""
                SELECT string_agg(boundary, ',' ORDER BY event_sequence)
                FROM sklegal_audit.events WHERE run_id = '{run_id}';
                """,
            ).stdout.strip(),
        )
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_audit.verify_current_tenant_chain();",
            ).stdout.strip(),
        )
        for other_role in ("sklegal_test_alpha_two", "sklegal_test_beta_one"):
            with self.subTest(other_role=other_role):
                self.assertEqual(
                    "0",
                    self._psql(
                        other_role,
                        f"SELECT count(*) FROM sklegal_audit.events "
                        f"WHERE run_id = '{run_id}';",
                    ).stdout.strip(),
                )

        protected = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000013",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000003",
            boundary="model",
            action="audit.synthetic.model",
            attributes='{"prompt":"synthetic protected content"}',
            check=False,
        )
        self.assertNotEqual(0, protected.returncode)
        self.assertIn("audit attributes are not allowlisted", protected.stderr)
        self.assertNotIn("synthetic protected content", protected.stderr)

        invalid_attributes = (
            '{"capability":123}',
            '{"operation":"READ"}',
            '{"resource_version":"1"}',
            '{"resource_version":0}',
            '{"resource_version":1.5}',
            '{"status_code":true}',
            '{"status_code":99}',
            '{"status_code":600}',
            '{"retry_count":-1}',
            '{"retry_count":1001}',
            '{"retry_count":1.5}',
        )
        for attributes in invalid_attributes:
            with self.subTest(attributes=attributes):
                malformed = self._append_audit_event(
                    event_id="a6100000-0000-4000-8000-000000000015",
                    run_id=run_id,
                    correlation_id="a6100000-0000-4000-8000-000000000021",
                    span_id="0000000000000005",
                    boundary="tool",
                    action="audit.synthetic.invalid",
                    attributes=attributes,
                    check=False,
                )
                self.assertNotEqual(0, malformed.returncode)
                self.assertIn("audit attributes are not allowlisted", malformed.stderr)

        exact_resource_kind = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000016",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000006",
            boundary="tool",
            action="audit.synthetic.resource-kind-limit",
            resource_kind="r" * 100,
        )
        self.assertEqual(
            100, len(json.loads(exact_resource_kind.stdout)["resource_kind"])
        )
        excessive_resource_kind = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000017",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000007",
            boundary="tool",
            action="audit.synthetic.resource-kind-excess",
            resource_kind="r" * 101,
            check=False,
        )
        self.assertNotEqual(0, excessive_resource_kind.returncode)
        self.assertIn("audit event metadata is invalid", excessive_resource_kind.stderr)

        wrong_scope = self._append_audit_event(
            event_id="a6100000-0000-4000-8000-000000000014",
            run_id=run_id,
            correlation_id="a6100000-0000-4000-8000-000000000021",
            span_id="0000000000000004",
            boundary="human",
            action="audit.synthetic.human",
            principal_id=value["principal_alpha_two"],
            check=False,
        )
        self.assertNotEqual(0, wrong_scope.returncode)
        self.assertIn("audit scope is unauthorized", wrong_scope.stderr)

        for mutation in (
            f"UPDATE sklegal_audit.events SET action = 'tamper' "
            f"WHERE id = '{first['event_id']}';",
            f"DELETE FROM sklegal_audit.events WHERE id = '{first['event_id']}';",
        ):
            with self.subTest(mutation=mutation.split()[0]):
                result = self._psql("postgres", mutation, check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("append-only", result.stderr)

        policies = self._psql(
            "postgres",
            """
            SELECT count(*) FROM pg_policies
            WHERE schemaname = 'sklegal_audit' AND tablename = 'events'
              AND cmd = 'INSERT';
            """,
        )
        self.assertEqual("1", policies.stdout.strip())

    def test_12_audit_and_outbox_share_rollback_and_delivery_receipt(self) -> None:
        run_id = "a6200000-0000-4000-8000-000000000001"
        event_id = "a6200000-0000-4000-8000-000000000011"
        event = json.loads(
            self._append_audit_event(
                event_id=event_id,
                run_id=run_id,
                correlation_id="a6200000-0000-4000-8000-000000000021",
                span_id="0000000000000011",
                boundary="connector",
                action="audit.synthetic.connector",
            ).stdout
        )
        before = self._psql(
            "postgres",
            f"""
            SELECT (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox),
                   last_event_sequence, last_event_sha256
            FROM sklegal_audit.chain_heads
            WHERE tenant_id = '{self.fixture["tenant_alpha"]}';
            """,
        ).stdout.strip()
        rollback = self._psql(
            "sklegal_test_alpha_one",
            f"""
            BEGIN;
            SELECT sklegal_audit.append_event(
                'a6200000-0000-4000-8000-000000000012',
                '{self.fixture["tenant_alpha"]}',
                '{self.fixture["matter_alpha_one"]}',
                '{self.fixture["principal_alpha_one"]}',
                '{run_id}', 'a6200000-0000-4000-8000-000000000021',
                '{"1" * 32}', '0000000000000012', '01', 'tool',
                'audit.synthetic.rollback', 'matter',
                '{self.fixture["matter_alpha_one"]}',
                'a6000000-0000-4000-8000-000000000001',
                'a6000000-0000-4000-8000-000000000002',
                'allow', 'allow', '2026-08-20T12:00:00Z', '{{}}'::jsonb
            );
            SELECT 1 / 0;
            COMMIT;
            """,
            check=False,
        )
        self.assertNotEqual(0, rollback.returncode)
        after = self._psql(
            "postgres",
            f"""
            SELECT (SELECT count(*) FROM sklegal_audit.events),
                   (SELECT count(*) FROM sklegal_audit.outbox),
                   last_event_sequence, last_event_sha256
            FROM sklegal_audit.chain_heads
            WHERE tenant_id = '{self.fixture["tenant_alpha"]}';
            """,
        ).stdout.strip()
        self.assertEqual(before, after)

        delivery_id = "a6200000-0000-4000-8000-000000000031"
        first = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT sklegal_audit.record_outbox_delivery("
            f"'{event['outbox_id']}', 'audit.local', '{delivery_id}');",
        )
        duplicate = self._psql(
            "sklegal_test_alpha_one",
            f"SELECT sklegal_audit.record_outbox_delivery("
            f"'{event['outbox_id']}', 'audit.local', "
            "'a6200000-0000-4000-8000-000000000032');",
        )
        self.assertEqual("t", first.stdout.strip())
        self.assertEqual("f", duplicate.stdout.strip())
        receipt = self._psql(
            "postgres",
            f"""
            SELECT count(*) || ':' || min(delivery_id::text) || ':' ||
                   bool_and(event_sha256 = '{event["event_sha256"]}')
            FROM sklegal_audit.outbox_deliveries
            WHERE outbox_id = '{event["outbox_id"]}';
            """,
        )
        self.assertEqual(f"1:{delivery_id}:true", receipt.stdout.strip())
        tamper = self._psql(
            "postgres",
            f"UPDATE sklegal_audit.outbox SET delivered_at = NULL "
            f"WHERE id = '{event['outbox_id']}';",
            check=False,
        )
        self.assertNotEqual(0, tamper.returncode)
        self.assertIn("controlled outbox writer", tamper.stderr)

    def test_12_projection_watermark_is_exact_monotonic_and_idempotent(self) -> None:
        run_id = "a6300000-0000-4000-8000-000000000001"
        first = json.loads(
            self._append_audit_event(
                event_id="a6300000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6300000-0000-4000-8000-000000000021",
                span_id="0000000000000021",
                boundary="api",
                action="audit.synthetic.projection.first",
            ).stdout
        )
        second = json.loads(
            self._append_audit_event(
                event_id="a6300000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6300000-0000-4000-8000-000000000021",
                span_id="0000000000000022",
                boundary="workflow",
                action="audit.synthetic.projection.second",
            ).stdout
        )
        advanced = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.projection', 0, NULL,
                {second["event_sequence"]}, '{second["event_sha256"]}'
            );
            """,
        )
        duplicate = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.projection', {second["event_sequence"]},
                '{second["event_sha256"]}', {second["event_sequence"]},
                '{second["event_sha256"]}'
            );
            """,
        )
        self.assertEqual("t", advanced.stdout.strip())
        self.assertEqual("f", duplicate.stdout.strip())
        stale = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.projection', 0, NULL,
                {first["event_sequence"]}, '{first["event_sha256"]}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("projection watermark is stale", stale.stderr)
        unknown = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SELECT sklegal_audit.advance_projection_watermark(
                'synthetic.unknown', 0, NULL,
                {second["event_sequence"]}, '{"f" * 64}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(0, unknown.returncode)
        self.assertIn("target audit event is unavailable", unknown.stderr)
        tamper = self._psql(
            "postgres",
            """
            UPDATE sklegal_audit.projection_watermarks
            SET event_sequence = event_sequence + 1
            WHERE projection = 'synthetic.projection';
            """,
            check=False,
        )
        self.assertNotEqual(0, tamper.returncode)
        self.assertIn("controlled projection writer", tamper.stderr)

    def test_12_projection_watermark_initialization_is_serialized(self) -> None:
        run_id = "a6400000-0000-4000-8000-000000000001"
        first = json.loads(
            self._append_audit_event(
                event_id="a6400000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6400000-0000-4000-8000-000000000021",
                span_id="0000000000000031",
                boundary="workflow",
                action="audit.synthetic.race.first",
            ).stdout
        )
        second = json.loads(
            self._append_audit_event(
                event_id="a6400000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6400000-0000-4000-8000-000000000021",
                span_id="0000000000000032",
                boundary="workflow",
                action="audit.synthetic.race.second",
            ).stdout
        )
        for label, initial, competing in (
            ("forward", first, second),
            ("reverse", second, first),
        ):
            with self.subTest(order=label):
                projection = f"audit.race.{label}"
                application_name = f"sklegal-s105-watermark-{label}"
                initializer_sql = f"""
                    BEGIN;
                    SET LOCAL application_name = '{application_name}';
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', 0, NULL,
                        {initial["event_sequence"]}, '{initial["event_sha256"]}'
                    );
                    SELECT pg_sleep(1.0);
                    COMMIT;
                """
                initializer = subprocess.Popen(
                    [
                        *self._psql_command("sklegal_test_alpha_one"),
                        "--command",
                        initializer_sql,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._wait_for_sleep(application_name, initializer)
                raced = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', 0, NULL,
                        {competing["event_sequence"]},
                        '{competing["event_sha256"]}'
                    );
                    """,
                    check=False,
                )
                stdout, stderr = initializer.communicate(timeout=10)
                self.assertEqual(0, initializer.returncode, stderr)
                self.assertIn("t", stdout.splitlines())
                self.assertNotEqual(0, raced.returncode)
                self.assertIn("projection watermark is stale", raced.stderr)
                current = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT event_sequence || ':' || event_sha256
                    FROM sklegal_audit.projection_watermarks
                    WHERE projection = '{projection}';
                    """,
                ).stdout.strip()
                self.assertEqual(
                    f"{initial['event_sequence']}:{initial['event_sha256']}",
                    current,
                )
                duplicate = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', {initial["event_sequence"]},
                        '{initial["event_sha256"]}',
                        {initial["event_sequence"]}, '{initial["event_sha256"]}'
                    );
                    """,
                )
                self.assertEqual("f", duplicate.stdout.strip())
                next_move = self._psql(
                    "sklegal_test_alpha_one",
                    f"""
                    SELECT sklegal_audit.advance_projection_watermark(
                        '{projection}', {initial["event_sequence"]},
                        '{initial["event_sha256"]}',
                        {competing["event_sequence"]},
                        '{competing["event_sha256"]}'
                    );
                    """,
                    check=label == "forward",
                )
                if label == "forward":
                    self.assertEqual("t", next_move.stdout.strip())
                else:
                    self.assertNotEqual(0, next_move.returncode)
                    self.assertIn("target audit event is unavailable", next_move.stderr)

    def test_12_canonical_audit_payload_is_timezone_independent(self) -> None:
        value = self.fixture
        timezone_probe = self._psql(
            "postgres",
            f"""
            BEGIN;
            SET LOCAL TIME ZONE 'UTC';
            CREATE TEMP TABLE audit_timezone_probe ON COMMIT DROP AS
            SELECT payload,
                   sklegal_audit.payload_sha256(payload) AS digest
            FROM (
                SELECT sklegal_audit.canonical_event_payload(
                    'a6600000-0000-4000-8000-000000000011',
                    '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                    '{value["principal_alpha_one"]}',
                    'a6600000-0000-4000-8000-000000000001',
                    'a6600000-0000-4000-8000-000000000021',
                    '{"1" * 32}', '0000000000000051', '01', 'api',
                    'audit.synthetic.timezone', 'matter',
                    '{value["matter_alpha_one"]}', NULL, NULL,
                    'success', 'allow', '2026-08-20T12:34:56.123456Z',
                    '{{"operation":"read"}}'::jsonb, 1, NULL,
                    '2026-08-20T12:34:57.654321Z'
                ) AS payload
            ) AS canonical;
            SET LOCAL TIME ZONE 'Pacific/Chatham';
            SELECT payload = sklegal_audit.canonical_event_payload(
                       'a6600000-0000-4000-8000-000000000011',
                       '{value["tenant_alpha"]}', '{value["matter_alpha_one"]}',
                       '{value["principal_alpha_one"]}',
                       'a6600000-0000-4000-8000-000000000001',
                       'a6600000-0000-4000-8000-000000000021',
                       '{"1" * 32}', '0000000000000051', '01', 'api',
                       'audit.synthetic.timezone', 'matter',
                       '{value["matter_alpha_one"]}', NULL, NULL,
                       'success', 'allow',
                       '2026-08-20T12:34:56.123456Z',
                       '{{"operation":"read"}}'::jsonb, 1, NULL,
                       '2026-08-20T12:34:57.654321Z'
                   )
                   AND digest = sklegal_audit.payload_sha256(
                       sklegal_audit.canonical_event_payload(
                           'a6600000-0000-4000-8000-000000000011',
                           '{value["tenant_alpha"]}',
                           '{value["matter_alpha_one"]}',
                           '{value["principal_alpha_one"]}',
                           'a6600000-0000-4000-8000-000000000001',
                           'a6600000-0000-4000-8000-000000000021',
                           '{"1" * 32}', '0000000000000051', '01', 'api',
                           'audit.synthetic.timezone', 'matter',
                           '{value["matter_alpha_one"]}', NULL, NULL,
                           'success', 'allow',
                           '2026-08-20T12:34:56.123456Z',
                           '{{"operation":"read"}}'::jsonb, 1, NULL,
                           '2026-08-20T12:34:57.654321Z'
                       )
                   )
            FROM audit_timezone_probe;
            ROLLBACK;
            """,
        )
        self.assertIn("t", timezone_probe.stdout.splitlines())
        appended = self._append_audit_event(
            event_id="a6600000-0000-4000-8000-000000000012",
            run_id="a6600000-0000-4000-8000-000000000002",
            correlation_id="a6600000-0000-4000-8000-000000000022",
            span_id="0000000000000052",
            boundary="model",
            action="audit.synthetic.timezone-append",
            time_zone="Pacific/Chatham",
        )
        payload = next(
            line for line in appended.stdout.splitlines() if line.startswith("{")
        )
        reconstructed = DurableAuditEvent.model_validate_json(payload)
        self.assertEqual(0, reconstructed.occurred_at.utcoffset().total_seconds())
        self.assertEqual(0, reconstructed.recorded_at.utcoffset().total_seconds())
        verification = self._psql(
            "sklegal_test_alpha_one",
            """
            SET TIME ZONE 'America/Los_Angeles';
            SELECT sklegal_audit.verify_current_tenant_chain();
            """,
        )
        self.assertEqual("t", verification.stdout.splitlines()[-1])

    def test_12_postgres_events_match_python_canonical_digest_contract(self) -> None:
        value = self.fixture
        canonical_probe = {
            "z": None,
            "a": {"b": 2, "a": 1},
            "m": [True, "synthetic/value"],
        }
        expected_text = json.dumps(
            canonical_probe,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        sql_text = self._psql(
            "postgres",
            """
            SELECT sklegal_audit.canonical_json_text(
                '{"z":null,"a":{"b":2,"a":1},'
                '"m":[true,"synthetic/value"]}'::jsonb
            );
            """,
        ).stdout.strip()
        self.assertEqual(expected_text, sql_text)
        self.assertEqual(expected_text.encode("utf-8").hex(), sql_text.encode().hex())
        current_year = datetime.now(UTC).year
        canonical_years = (1, 9, 99, 999, 1000, current_year, 9999)
        timestamp_expressions = ",\n".join(
            f"('{year:04d}-01-02 03:04:05.123456+00'::timestamptz, {index})"
            for index, year in enumerate(canonical_years, start=1)
        )
        sql_timestamps = self._psql(
            "postgres",
            f"""
            SELECT sklegal_audit.canonical_timestamp(sample.value)
            FROM (VALUES {timestamp_expressions}) AS sample(value, position)
            ORDER BY sample.position;
            """,
        ).stdout.splitlines()
        python_timestamps = [
            _canonical_timestamp(datetime(year, 1, 2, 3, 4, 5, 123456, tzinfo=UTC))
            for year in canonical_years
        ]
        self.assertEqual(python_timestamps, sql_timestamps)

        for index, year in enumerate((1, 9, 99, 999, 1000, current_year), start=1):
            self._append_audit_event(
                event_id=f"a6810000-0000-4000-8000-{index:012d}",
                run_id="a6810000-0000-4000-8000-000000000010",
                correlation_id="a6810000-0000-4000-8000-000000000020",
                span_id=f"{128 + index:016x}",
                boundary="api",
                action=f"audit.synthetic.canonical-year-{year}",
                occurred_at=f"{year:04d}-01-01T00:00:00.000000Z",
                time_zone="Pacific/Chatham",
            )
        self._append_audit_event(
            event_id="a6800000-0000-4000-8000-000000000011",
            run_id="a6800000-0000-4000-8000-000000000001",
            correlation_id="a6800000-0000-4000-8000-000000000021",
            span_id="0000000000000071",
            boundary="api",
            action="audit.synthetic.canonical-zero",
            resource_kind="tenant",
            occurred_at="2026-08-20T12:34:56Z",
            tenant_scoped=True,
            include_decision_references=False,
            attributes=('{"error_code":null,"event_schema":"sklegal-audit-event/v1"}'),
            time_zone="Pacific/Chatham",
        )
        self._append_audit_event(
            event_id="a6800000-0000-4000-8000-000000000012",
            run_id="a6800000-0000-4000-8000-000000000001",
            correlation_id="a6800000-0000-4000-8000-000000000021",
            span_id="0000000000000072",
            boundary="connector",
            action="audit.synthetic.canonical-fraction",
            occurred_at="2026-08-20T12:34:56.123456Z",
            attributes=(
                '{"operation":"read","resource_version":7,'
                '"status_code":200,"retry_count":0}'
            ),
            time_zone="America/Los_Angeles",
        )
        raw_events = json.loads(
            self._psql(
                "postgres",
                f"""
                SET TIME ZONE 'Asia/Kathmandu';
                SELECT COALESCE(
                    jsonb_agg(
                        event.canonical_payload || jsonb_build_object(
                            'event_sha256', event.event_sha256,
                            'outbox_id', event.id
                        ) ORDER BY event.event_sequence
                    ),
                    '[]'::jsonb
                )::text
                FROM sklegal_audit.events AS event
                WHERE event.tenant_id = '{value["tenant_alpha"]}';
                """,
            ).stdout.splitlines()[-1]
        )
        reconstructed: list[DurableAuditEvent] = []
        for payload in raw_events:
            wire = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            source_event = DurableAuditEvent.model_validate_json(wire)
            draft = AuditEventDraft.model_validate(
                {
                    field: getattr(source_event, field)
                    for field in AuditEventDraft.model_fields
                }
            )

            def executor(
                sql: str,
                parameters: tuple[object, ...],
                returned_wire: str = wire,
            ) -> dict[str, object]:
                self.assertTrue(sql)
                self.assertEqual(19, len(parameters))
                return {"event": returned_wire}

            rebuilt = PostgresAuditRepository(executor).append(draft)
            self.assertEqual(source_event, rebuilt)
            self.assertEqual(source_event.event_sha256, recompute_event_sha256(rebuilt))
            reconstructed.append(rebuilt)

        self.assertGreaterEqual(len(reconstructed), 2)
        self.assertTrue(verify_event_chain(reconstructed))

    def test_12_integrity_mode_cannot_be_laundered_through_definers(self) -> None:
        value = self.fixture
        event = json.loads(
            self._append_audit_event(
                event_id="a6900000-0000-4000-8000-000000000011",
                run_id="a6900000-0000-4000-8000-000000000001",
                correlation_id="a6900000-0000-4000-8000-000000000021",
                span_id="0000000000000081",
                boundary="workflow",
                action="audit.synthetic.lost-matter-access",
            ).stdout
        )
        self._psql(
            "postgres",
            f"""
            UPDATE sklegal_legal.matter_memberships
            SET active = false,
                version = version + 1,
                updated_at = clock_timestamp()
            WHERE tenant_id = '{value["tenant_alpha"]}'
              AND matter_id = '{value["matter_alpha_one"]}'
              AND principal_id = '{value["principal_alpha_one"]}';
            """,
        )
        try:
            direct = self._psql(
                "sklegal_test_alpha_one",
                f"""
                SET sklegal.audit_integrity_verification = 'on';
                SELECT count(*) FROM sklegal_audit.events
                WHERE id = '{event["event_id"]}';
                """,
            )
            self.assertEqual("0", direct.stdout.splitlines()[-1])
            for projection, setup in (
                ("audit.lost-access.plain", ""),
                (
                    "audit.lost-access.caller-guc",
                    "SET sklegal.audit_integrity_verification = 'on';",
                ),
            ):
                with self.subTest(projection=projection):
                    denied = self._psql(
                        "sklegal_test_alpha_one",
                        f"""
                        {setup}
                        SELECT sklegal_audit.advance_projection_watermark(
                            '{projection}', 0, NULL,
                            {event["event_sequence"]}, '{event["event_sha256"]}'
                        );
                        """,
                        check=False,
                    )
                    self.assertNotEqual(0, denied.returncode)
                    self.assertIn("target audit event is unavailable", denied.stderr)
            restored = self._psql(
                "sklegal_test_alpha_one",
                """
                BEGIN;
                SET LOCAL sklegal.audit_integrity_verification = 'caller';
                SELECT sklegal_audit.verify_current_tenant_chain();
                SELECT current_setting(
                    'sklegal.audit_integrity_verification', true
                );
                ROLLBACK;
                """,
            )
            self.assertEqual(
                ["t", "caller"],
                [
                    line
                    for line in restored.stdout.splitlines()
                    if line in {"t", "caller"}
                ],
            )
        finally:
            self._psql(
                "postgres",
                f"""
                UPDATE sklegal_legal.matter_memberships
                SET active = true,
                    version = version + 1,
                    updated_at = clock_timestamp()
                WHERE tenant_id = '{value["tenant_alpha"]}'
                  AND matter_id = '{value["matter_alpha_one"]}'
                  AND principal_id = '{value["principal_alpha_one"]}';
                """,
            )

    def test_12_tenant_chain_verification_sees_hidden_matter_rows(self) -> None:
        value = self.fixture
        run_id = "a6500000-0000-4000-8000-000000000001"
        prefix = json.loads(
            self._append_audit_event(
                event_id="a6500000-0000-4000-8000-000000000011",
                run_id=run_id,
                correlation_id="a6500000-0000-4000-8000-000000000021",
                span_id="0000000000000041",
                boundary="api",
                action="audit.synthetic.visible-prefix",
            ).stdout
        )
        hidden = json.loads(
            self._append_audit_event(
                event_id="a6500000-0000-4000-8000-000000000012",
                run_id=run_id,
                correlation_id="a6500000-0000-4000-8000-000000000021",
                span_id="0000000000000042",
                boundary="human",
                action="audit.synthetic.hidden",
                role="sklegal_test_alpha_two",
                tenant_id=value["tenant_alpha"],
                matter_id=value["matter_alpha_two"],
                principal_id=value["principal_alpha_two"],
            ).stdout
        )
        visible = self._psql(
            "sklegal_test_alpha_one",
            f"""
            SET sklegal.audit_integrity_verification = 'on';
            SELECT count(*) || ':' || max(event_sequence)
            FROM sklegal_audit.events WHERE run_id = '{run_id}';
            """,
        ).stdout.strip()
        self.assertEqual(f"1:{prefix['event_sequence']}", visible.splitlines()[-1])
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_audit.verify_current_tenant_chain();",
            ).stdout.strip(),
        )
        try:
            self._psql(
                "postgres",
                f"""
                ALTER TABLE sklegal_audit.events
                    DISABLE TRIGGER audit_events_append_only;
                UPDATE sklegal_audit.events
                SET action = 'audit.synthetic.hidden-tamper'
                WHERE tenant_id = '{value["tenant_alpha"]}'
                  AND id = '{hidden["event_id"]}';
                ALTER TABLE sklegal_audit.events
                    ENABLE TRIGGER audit_events_append_only;
                """,
            )
            self.assertEqual(
                "f",
                self._psql(
                    "sklegal_test_alpha_one",
                    "SELECT sklegal_audit.verify_current_tenant_chain();",
                ).stdout.strip(),
            )
        finally:
            self._psql(
                "postgres",
                f"""
                ALTER TABLE sklegal_audit.events
                    DISABLE TRIGGER audit_events_append_only;
                UPDATE sklegal_audit.events
                SET action = 'audit.synthetic.hidden'
                WHERE tenant_id = '{value["tenant_alpha"]}'
                  AND id = '{hidden["event_id"]}';
                ALTER TABLE sklegal_audit.events
                    ENABLE TRIGGER audit_events_append_only;
                """,
            )
        after_gap = json.loads(
            self._append_audit_event(
                event_id="a6500000-0000-4000-8000-000000000013",
                run_id=run_id,
                correlation_id="a6500000-0000-4000-8000-000000000021",
                span_id="0000000000000043",
                boundary="connector",
                action="audit.synthetic.visible-after-gap",
            ).stdout
        )
        self.assertEqual(prefix["event_sequence"] + 2, after_gap["event_sequence"])
        self.assertEqual(
            "t",
            self._psql(
                "sklegal_test_alpha_one",
                "SELECT sklegal_audit.verify_current_tenant_chain();",
            ).stdout.strip(),
        )

    def test_13_schema_isolation_and_disposable_runtime(self) -> None:
        schemas = self._psql(
            "postgres",
            """
            SELECT string_agg(schema_name, ',' ORDER BY schema_name)
            FROM information_schema.schemata WHERE schema_name LIKE 'sklegal_%';
            """,
        )
        self.assertEqual(
            "sklegal_audit,sklegal_identity,sklegal_integrations,sklegal_legal,"
            "sklegal_migrations,sklegal_workflow",
            schemas.stdout.strip(),
        )
        inspection = subprocess.run(
            ["docker", "inspect", self.container],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(inspection.stdout)[0]
        self.assertIn(payload["HostConfig"].get("PortBindings"), (None, {}))
        self.assertEqual("none", payload["HostConfig"]["NetworkMode"])
        self.assertEqual(
            "rw,noexec,nosuid,size=512m",
            payload["HostConfig"]["Tmpfs"]["/var/lib/postgresql/data"],
        )
        self.assertFalse(
            any(mount["Type"] == "volume" for mount in payload.get("Mounts", []))
        )
        if self.skmemory_container_id:
            skmemory = subprocess.run(
                ["docker", "ps", "--quiet", "--filter", "name=^skmem-pg$"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(self.skmemory_container_id, skmemory.stdout.strip())

    def test_14_data_bearing_audit_migration_refuses_down(self) -> None:
        event = self._append_audit_event(
            event_id="a6700000-0000-4000-8000-000000000011",
            run_id="a6700000-0000-4000-8000-000000000001",
            correlation_id="a6700000-0000-4000-8000-000000000021",
            span_id="0000000000000061",
            boundary="api",
            action="audit.synthetic.rollback-guard",
        )
        self.assertTrue(event.stdout.strip())

        def state() -> str:
            return self._psql(
                "postgres",
                """
                SELECT (SELECT count(*) FROM sklegal_audit.events) || ':' ||
                       (SELECT count(*) FROM sklegal_audit.outbox) || ':' ||
                       (SELECT count(*) FROM sklegal_audit.rollback_guard);
                """,
            ).stdout.strip()

        def assert_down_denied(before: str) -> None:
            down = self._migrate(
                "down", "--steps", str(AUDIT_MIGRATION_STEPS), check=False
            )
            self.assertNotEqual(0, down.returncode)
            self.assertIn("audit chain cannot be rolled back", down.stderr)
            self.assertEqual(before, state())
            self.assertEqual(
                "1:true:true",
                self._psql(
                    "postgres",
                    """
                    SELECT count(*) || ':' ||
                           (to_regclass('sklegal_audit.outbox') IS NOT NULL) || ':' ||
                           (to_regprocedure(
                               'sklegal_audit.append_event(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,text,text,text,uuid,uuid,uuid,text,text,timestamptz,jsonb)'
                           ) IS NOT NULL)
                    FROM sklegal_migrations.schema_migrations
                    WHERE file = '0007_append_only_audit_outbox.sql';
                    """,
                ).stdout.strip(),
            )

        both = state()
        both_parts = [int(part) for part in both.split(":")]
        self.assertGreater(both_parts[0], 0)
        self.assertGreater(both_parts[1], 0)
        self.assertEqual(1, both_parts[2])
        assert_down_denied(both)

        self._psql(
            "postgres",
            """
            SET session_replication_role = replica;
            DELETE FROM sklegal_audit.outbox_deliveries;
            DELETE FROM sklegal_audit.outbox;
            SET session_replication_role = origin;
            """,
        )
        event_only = state()
        event_only_parts = [int(part) for part in event_only.split(":")]
        self.assertGreater(event_only_parts[0], 0)
        self.assertEqual([0, 1], event_only_parts[1:])
        assert_down_denied(event_only)

        self._psql(
            "postgres",
            """
            SET session_replication_role = replica;
            INSERT INTO sklegal_audit.outbox (
                id, tenant_id, matter_id, event_id, run_id, correlation_id,
                event_sequence, event_sha256, available_at
            )
            SELECT id, tenant_id, matter_id, id, run_id, correlation_id,
                   event_sequence, event_sha256, recorded_at
            FROM sklegal_audit.events
            ORDER BY tenant_id, event_sequence
            LIMIT 1;
            DELETE FROM sklegal_audit.events;
            SET session_replication_role = origin;
            """,
        )
        outbox_only = state()
        self.assertEqual("0:1:1", outbox_only)
        assert_down_denied(outbox_only)


if __name__ == "__main__":
    unittest.main()
