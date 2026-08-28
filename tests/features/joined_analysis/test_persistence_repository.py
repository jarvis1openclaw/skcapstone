"""Driver-neutral repository, migration, and adapter parity tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sklegal_api.features.joined_analysis.contract import (
    JoinedAnalysisSnapshotProjection,
)
from sklegal_api.features.joined_analysis.service import projection_sha256
from sklegal_api.features.joined_analysis.stores import (
    PostgresJoinedAnalysisStore,
    seal_public_synthetic_projection,
)
from sklegal_persistence.features.joined_analysis import (
    CURRENT_PROJECTION_SQL,
    MEMBERSHIP_SQL,
    JoinedAnalysisPersistenceUnavailable,
    PostgresJoinedAnalysisRepository,
    canonical_projection_sha256,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/mvp/fragments/joined_analysis/public-synthetic-v1.json"
MIGRATION = ROOT / "migrations/0022_joined_analysis_snapshots.sql"
TENANT_ID = UUID("91000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("91000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("91000000-0000-4000-8000-000000000004")


def payload() -> dict[str, object]:
    sealed = seal_public_synthetic_projection(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    return sealed.model_dump(mode="json", by_alias=False)


class Executor:
    def __init__(self, projection: dict[str, object] | None = None) -> None:
        self.projection = projection
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.available = True

    def __call__(self, sql: str, parameters: tuple[object, ...]):
        self.calls.append((sql, parameters))
        if not self.available:
            raise OSError("private socket path")
        if sql == MEMBERSHIP_SQL:
            return [{"member": parameters == (TENANT_ID, MATTER_ID, PRINCIPAL_ID)}]
        if sql == CURRENT_PROJECTION_SQL:
            if self.projection is None:
                return []
            return [
                {
                    "projection": self.projection,
                    "projection_sha256": self.projection["snapshot"][
                        "projection_sha256"
                    ],
                }
            ]
        raise AssertionError("unexpected SQL")


def test_repository_uses_exact_parameterized_scope_and_hashes() -> None:
    fixture = payload()
    executor = Executor(fixture)
    repository = PostgresJoinedAnalysisRepository(executor)
    assert repository.is_matter_member(TENANT_ID, MATTER_ID, PRINCIPAL_ID) is True
    stored = repository.load_current(TENANT_ID, MATTER_ID)
    assert stored is not None
    digest = fixture["snapshot"]["projection_sha256"]
    assert stored.projection_sha256 == digest
    assert canonical_projection_sha256(fixture) == digest
    assert (
        projection_sha256(JoinedAnalysisSnapshotProjection.model_validate(fixture))
        == digest
    )
    assert executor.calls == [
        (MEMBERSHIP_SQL, (TENANT_ID, MATTER_ID, PRINCIPAL_ID)),
        (CURRENT_PROJECTION_SQL, (TENANT_ID, MATTER_ID)),
    ]
    assert MEMBERSHIP_SQL.count("%s") == 3
    assert CURRENT_PROJECTION_SQL.count("%s") == 2
    assert str(TENANT_ID) not in MEMBERSHIP_SQL + CURRENT_PROJECTION_SQL


def test_public_and_durable_adapters_parse_identical_projection() -> None:
    repository = PostgresJoinedAnalysisRepository(Executor(payload()))
    store = PostgresJoinedAnalysisStore(repository)
    projection = store.projection(TENANT_ID, MATTER_ID)
    assert projection is not None
    assert projection.model_dump(mode="json", by_alias=False) == payload()


def test_durable_adapter_recomputes_provenance_partitions() -> None:
    drifted = payload()
    drifted["snapshot"]["authority_snapshot"] = "8" * 64
    drifted["snapshot"]["projection_sha256"] = canonical_projection_sha256(drifted)
    store = PostgresJoinedAnalysisStore(
        PostgresJoinedAnalysisRepository(Executor(drifted))
    )
    with pytest.raises(Exception, match="provenance digest drift"):
        store.projection(TENANT_ID, MATTER_ID)


@pytest.mark.parametrize(
    "mutation",
    [
        "payload",
        "metadata",
        "tenant",
        "matter",
        "snapshot_digest",
    ],
)
def test_repository_fails_closed_on_drift(mutation: str) -> None:
    fixture = deepcopy(payload())
    executor = Executor(fixture)
    if mutation == "payload":
        fixture["theories"][0]["statement"] = "mutated"  # type: ignore[index]
    elif mutation == "tenant":
        fixture["tenant_id"] = str(uuid4())
    elif mutation == "matter":
        fixture["matter_id"] = str(uuid4())
    elif mutation == "snapshot_digest":
        fixture["snapshot"]["projection_sha256"] = "9" * 64  # type: ignore[index]
    repository = PostgresJoinedAnalysisRepository(executor)
    if mutation == "metadata":
        original = executor.__call__

        def metadata_drift(sql: str, parameters: tuple[object, ...]):
            rows = original(sql, parameters)
            if sql == CURRENT_PROJECTION_SQL:
                rows[0]["projection_sha256"] = "8" * 64
            return rows

        repository = PostgresJoinedAnalysisRepository(metadata_drift)
    with pytest.raises(JoinedAnalysisPersistenceUnavailable):
        repository.load_current(TENANT_ID, MATTER_ID)


def test_repository_outage_and_malformed_rows_are_sanitized() -> None:
    executor = Executor(payload())
    executor.available = False
    repository = PostgresJoinedAnalysisRepository(executor)
    with pytest.raises(
        JoinedAnalysisPersistenceUnavailable,
        match="joined analysis persistence unavailable",
    ):
        repository.load_current(TENANT_ID, MATTER_ID)

    for rows in (
        "wrong",
        [{"member": "yes"}],
        [{"projection": {}, "unexpected": "value"}],
    ):
        malformed = PostgresJoinedAnalysisRepository(lambda _sql, _params: rows)  # type: ignore[arg-type]
        with pytest.raises(JoinedAnalysisPersistenceUnavailable):
            if isinstance(rows, list) and rows and "member" in rows[0]:
                malformed.is_matter_member(TENANT_ID, MATTER_ID, PRINCIPAL_ID)
            else:
                malformed.load_current(TENANT_ID, MATTER_ID)


def test_migration_is_additive_append_only_rls_and_reversible() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    up, down = sql.split("-- sklegal:down", 1)
    assert "CREATE TABLE sklegal_legal.joined_analysis_snapshots" in up
    assert "FOREIGN KEY (tenant_id, matter_id)" in up
    assert "ENABLE ROW LEVEL SECURITY" in up
    assert "FORCE ROW LEVEL SECURITY" in up
    assert "sklegal_identity.record_is_authorized(tenant_id, matter_id)" in up
    assert "sklegal_legal.reject_record_change()" in up
    assert "security_invoker = true" in up
    assert "projection#>>'{snapshot,projection_sha256}'" in up
    assert "DROP TABLE sklegal_legal.joined_analysis_snapshots" in down
    assert "ALTER TABLE sklegal_legal.claims" not in up
    assert "ALTER TABLE sklegal_legal.ledger_claims" not in up
    assert "migrations/manifest.json" not in sql
