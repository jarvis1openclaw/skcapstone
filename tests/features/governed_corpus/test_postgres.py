from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pytest
from sklegal_api.features.governed_corpus.contracts import CorpusSearchCommand
from sklegal_persistence.features.governed_corpus.postgres import (
    IDEMPOTENCY_SQL,
    INSERT_AUDIT_SQL,
    INSERT_IDEMPOTENCY_SQL,
    INSERT_OUTBOX_SQL,
    INSERT_PROJECTION_COMMAND_SQL,
    INSERT_SOURCE_SQL,
    MEMBERSHIP_SQL,
    SEARCH_SQL,
    UPSERT_SOURCE_PROJECTION_SQL,
    PostgresGovernedCorpusCoreRepository,
    PostgresGovernedCorpusRetrievalRepository,
)
from sklegal_persistence.features.governed_corpus.repository import (
    GovernedCorpusIdempotencyConflict,
    GovernedCorpusRepositoryUnavailable,
)

from .helpers import (
    MATTER,
    NOW,
    PRINCIPAL,
    TENANT,
    access,
    composition,
    fixture,
    source_command,
)


class RecordingTransaction:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.one: dict[str, Mapping[str, object] | None] = {}
        self.all: dict[str, Sequence[Mapping[str, object]]] = {}

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.executed.append((sql, parameters))

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        self.executed.append((sql, parameters))
        return self.one.get(sql)

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        self.executed.append((sql, parameters))
        return self.all.get(sql, ())


def write_evidence():
    service, repository, _ = composition(seed=False)
    _, sources = fixture()
    receipt = service.record_source(
        context=access("corpus.ingest.submit", "corpus_ingestion"),
        matter_id=MATTER,
        idempotency_key="postgres-source",
        command=source_command(sources[0]),
    )
    return receipt, repository.audit_events[0], repository.outbox_records[0]


def test_postgres_write_is_one_transaction_with_audit_and_outbox() -> None:
    transaction = RecordingTransaction()
    calls = 0

    def runner(callback: Callable):
        nonlocal calls
        calls += 1
        return callback(transaction)

    receipt, audit, outbox = write_evidence()
    repository = PostgresGovernedCorpusCoreRepository(runner)
    committed = repository.commit_source(
        idempotency_key_sha256=receipt.idempotency_key_sha256,
        request_sha256=receipt.request_sha256,
        source=receipt.source,
        audit=audit,
        outbox=outbox,
    )
    assert committed == receipt.source
    assert calls == 1
    statements = [statement for statement, _ in transaction.executed]
    assert statements.index(INSERT_SOURCE_SQL) < statements.index(INSERT_AUDIT_SQL)
    assert statements.index(INSERT_AUDIT_SQL) < statements.index(INSERT_OUTBOX_SQL)
    assert INSERT_IDEMPOTENCY_SQL in statements


def test_postgres_idempotency_conflict_precedes_every_insert() -> None:
    transaction = RecordingTransaction()
    receipt, audit, outbox = write_evidence()
    transaction.one[IDEMPOTENCY_SQL] = {
        "request_sha256": "f" * 64,
        "response_record": receipt.source.model_dump(mode="json", by_alias=True),
    }
    repository = PostgresGovernedCorpusCoreRepository(
        lambda callback: callback(transaction)
    )
    with pytest.raises(GovernedCorpusIdempotencyConflict):
        repository.commit_source(
            idempotency_key_sha256=receipt.idempotency_key_sha256,
            request_sha256=receipt.request_sha256,
            source=receipt.source,
            audit=audit,
            outbox=outbox,
        )
    assert INSERT_SOURCE_SQL not in [item[0] for item in transaction.executed]


def test_postgres_search_uses_only_the_fixed_bound_function() -> None:
    transaction = RecordingTransaction()
    transaction.all[SEARCH_SQL] = ()
    repository = PostgresGovernedCorpusRetrievalRepository(
        lambda callback: callback(transaction)
    )
    command = CorpusSearchCommand(
        query="public synthetic",
        mode="full_text",
        expected_release_id="synthetic-release-1",
        expected_projection_generation=3,
        required_core_watermark=42,
    )
    from sklegal_persistence.features.governed_corpus.models import RetrievalQuery

    repository.retrieve(
        tenant_id=TENANT,
        matter_id=MATTER,
        principal_id=PRINCIPAL,
        query=RetrievalQuery(
            **command.model_dump(exclude={"schema_version", "continuation_cursor"}),
            classification_ceiling=0,
            rights_revision="1" * 64,
            snapshot_at=NOW,
        ),
    )
    assert transaction.executed == [
        (
            SEARCH_SQL,
            (
                TENANT,
                MATTER,
                PRINCIPAL,
                "public synthetic",
                None,
                "full_text",
                "synthetic-release-1",
                3,
                0,
                "1" * 64,
                NOW,
                None,
                None,
                11,
            ),
        )
    ]


def test_postgres_vector_parameters_are_native_vector_literals() -> None:
    transaction = RecordingTransaction()
    transaction.all[SEARCH_SQL] = ()
    repository = PostgresGovernedCorpusRetrievalRepository(
        lambda callback: callback(transaction)
    )
    from sklegal_persistence.features.governed_corpus.models import RetrievalQuery

    repository.retrieve(
        tenant_id=TENANT,
        matter_id=MATTER,
        principal_id=PRINCIPAL,
        query=RetrievalQuery(
            query="public synthetic",
            query_embedding=(1.0, 0.0, 0.0),
            mode="vector_exact",
            expected_release_id="synthetic-release-1",
            expected_projection_generation=3,
            required_core_watermark=42,
            classification_ceiling=0,
            rights_revision="1" * 64,
            snapshot_at=NOW,
        ),
    )
    assert transaction.executed[0][1][4] == "[1.0,0.0,0.0]"


def test_driver_errors_are_sanitized_and_membership_is_exactly_scoped() -> None:
    transaction = RecordingTransaction()
    transaction.one[MEMBERSHIP_SQL] = {"allowed": True}
    repository = PostgresGovernedCorpusCoreRepository(
        lambda callback: callback(transaction)
    )
    assert repository.is_matter_member(TENANT, MATTER, PRINCIPAL) is True
    assert transaction.executed[-1][1] == (TENANT, MATTER, PRINCIPAL)

    def broken(_callback):
        raise RuntimeError("host and query details")

    unavailable = PostgresGovernedCorpusCoreRepository(broken)
    with pytest.raises(
        GovernedCorpusRepositoryUnavailable,
        match="governed corpus store is unavailable",
    ) as captured:
        unavailable.is_matter_member(TENANT, MATTER, PRINCIPAL)
    assert "host" not in str(captured.value)


def test_core_and_retrieval_use_distinct_explicit_runners() -> None:
    core_transaction = RecordingTransaction()
    retrieval_transaction = RecordingTransaction()
    core_transaction.one[MEMBERSHIP_SQL] = {"allowed": True}
    retrieval_transaction.all[SEARCH_SQL] = ()
    core = PostgresGovernedCorpusCoreRepository(
        lambda callback: callback(core_transaction)
    )
    retrieval = PostgresGovernedCorpusRetrievalRepository(
        lambda callback: callback(retrieval_transaction)
    )
    assert core.is_matter_member(TENANT, MATTER, PRINCIPAL)
    from sklegal_persistence.features.governed_corpus.models import RetrievalQuery

    retrieval.retrieve(
        tenant_id=TENANT,
        matter_id=MATTER,
        principal_id=PRINCIPAL,
        query=RetrievalQuery(
            query="public synthetic",
            mode="full_text",
            expected_release_id="synthetic-release-1",
            expected_projection_generation=3,
            required_core_watermark=42,
            classification_ceiling=0,
            rights_revision="1" * 64,
            snapshot_at=NOW,
        ),
    )
    assert [sql for sql, _ in core_transaction.executed] == [MEMBERSHIP_SQL]
    assert [sql for sql, _ in retrieval_transaction.executed] == [SEARCH_SQL]


def test_retrieval_projector_is_idempotent_and_separate_from_core_commit() -> None:
    from sklegal_persistence.features.governed_corpus.models import (
        CorpusProjectionCommand,
    )

    transaction = RecordingTransaction()
    calls = 0

    def runner(callback: Callable):
        nonlocal calls
        calls += 1
        return callback(transaction)

    receipt, _audit, outbox = write_evidence()
    command = CorpusProjectionCommand(
        command_id=outbox.outbox_id,
        operation="create",
        source=receipt.source,
        payload_sha256=outbox.payload_sha256,
        core_watermark=42,
        projected_at=receipt.source.recorded_at,
    )
    repository = PostgresGovernedCorpusRetrievalRepository(runner)
    repository.apply_projection(command)
    statements = [sql for sql, _ in transaction.executed]
    assert calls == 1
    assert statements.index(INSERT_PROJECTION_COMMAND_SQL) < statements.index(
        UPSERT_SOURCE_PROJECTION_SQL
    )
    assert INSERT_SOURCE_SQL not in statements
