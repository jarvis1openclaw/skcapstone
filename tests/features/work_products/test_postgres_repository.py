from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import timedelta

import pytest
from sklegal_persistence.features.work_products.models import canonical_sha256
from sklegal_persistence.features.work_products.postgres import (
    PostgresWorkProductRepository,
)
from sklegal_persistence.features.work_products.repository import (
    WorkProductIdempotencyConflict,
    WorkProductRepositoryUnavailable,
    WorkProductVersionConflict,
)

from .helpers import (
    DIGEST,
    EMPTY_REVOCATION_REVISION,
    MATTER,
    POLICY,
    T0,
    TENANT,
    actor,
    create_product,
    make_service,
    uid,
)


class FakeTransaction:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetched: list[tuple[str, tuple[object, ...]]] = []
        self.idempotency: Mapping[str, object] | None = None
        self.current: Mapping[str, object] | None = None
        self.rows: list[Mapping[str, object]] = []
        self.snapshot: object = {
            "revision": EMPTY_REVOCATION_REVISION,
            "revoked_credential_digests": [],
        }

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.executed.append((sql, parameters))

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        self.fetched.append((sql, parameters))
        if "work_product_feature_idempotency" in sql:
            return self.idempotency
        if "FOR UPDATE" in sql:
            return self.current
        if "matter_memberships" in sql:
            return {"allowed": True}
        if "ledger_claim_identities" in sql:
            return {"present": True}
        if "capability_revocation_snapshot" in sql:
            return {"snapshot": self.snapshot}
        if "version.aggregate_payload" in sql:
            return self.current
        return None

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> list[Mapping[str, object]]:
        del sql, parameters
        return self.rows


def repository(tx: FakeTransaction) -> PostgresWorkProductRepository:
    def run(call: Callable[[FakeTransaction], object]):
        return call(tx)

    return PostgresWorkProductRepository(
        transaction=run,  # type: ignore[arg-type]
        current_policy_revision=POLICY,
    )


def envelope():
    service, memory, _, _ = make_service()
    aggregate = create_product(service)
    return aggregate, memory.audit_events()[0], memory.outbox_events()[0]


def test_new_aggregate_write_contains_identity_version_receipt_audit_and_outbox() -> (
    None
):
    tx = FakeTransaction()
    repo = repository(tx)
    aggregate, audit, outbox = envelope()
    result = repo.commit(
        expected_aggregate_version=None,
        idempotency_key=uid(300),
        request_sha256="d" * 64,
        aggregate=aggregate,
        audit=audit,
        outbox=outbox,
    )
    assert result == aggregate
    statements = "\n".join(sql for sql, _ in tx.executed)
    for table in (
        "work_product_feature_identities",
        "work_product_feature_versions",
        "work_product_feature_idempotency",
        "work_product_feature_events",
        "work_product_feature_outbox",
    ):
        assert (
            f"INSERT INTO sklegal_{'audit' if table.endswith(('events', 'outbox')) else 'legal'}.{table}"
            in statements
        )


def test_stale_current_aggregate_is_rejected() -> None:
    tx = FakeTransaction()
    tx.current = {"current_aggregate_version": 9}
    repo = repository(tx)
    aggregate, audit, outbox = envelope()
    successor = aggregate.model_copy(update={"aggregate_version": 2})
    digest = canonical_sha256(successor)
    with pytest.raises(WorkProductVersionConflict):
        repo.commit(
            expected_aggregate_version=1,
            idempotency_key=uid(301),
            request_sha256="d" * 64,
            aggregate=successor,
            audit=audit.model_copy(
                update={"aggregate_version": 2, "subject_sha256": digest}
            ),
            outbox=outbox.model_copy(
                update={"aggregate_version": 2, "payload_sha256": digest}
            ),
        )


def test_idempotent_replay_returns_original_payload_without_new_inserts() -> None:
    tx = FakeTransaction()
    aggregate, audit, outbox = envelope()
    tx.idempotency = {
        "request_sha256": "d" * 64,
        "aggregate_payload": aggregate.model_dump(mode="json", by_alias=True),
    }
    result = repository(tx).commit(
        expected_aggregate_version=None,
        idempotency_key=uid(302),
        request_sha256="d" * 64,
        aggregate=aggregate,
        audit=audit,
        outbox=outbox,
    )
    assert result == aggregate
    assert len(tx.executed) == 1


def test_idempotent_reuse_with_other_request_is_rejected() -> None:
    tx = FakeTransaction()
    aggregate, audit, outbox = envelope()
    tx.idempotency = {
        "request_sha256": "e" * 64,
        "aggregate_payload": aggregate.model_dump(mode="json", by_alias=True),
    }
    with pytest.raises(WorkProductIdempotencyConflict):
        repository(tx).commit(
            expected_aggregate_version=None,
            idempotency_key=uid(303),
            request_sha256="d" * 64,
            aggregate=aggregate,
            audit=audit,
            outbox=outbox,
        )


def test_transaction_failure_is_sanitized() -> None:
    aggregate, audit, outbox = envelope()

    def fail(call: object):
        del call
        raise RuntimeError("database detail")

    repo = PostgresWorkProductRepository(
        transaction=fail,  # type: ignore[arg-type]
        current_policy_revision=POLICY,
    )
    with pytest.raises(WorkProductRepositoryUnavailable) as caught:
        repo.commit(
            expected_aggregate_version=None,
            idempotency_key=uid(304),
            request_sha256="d" * 64,
            aggregate=aggregate,
            audit=audit,
            outbox=outbox,
        )
    assert "database detail" not in str(caught.value)


def test_tenant_matter_are_present_in_every_write_parameter_set() -> None:
    tx = FakeTransaction()
    aggregate, audit, outbox = envelope()
    repository(tx).commit(
        expected_aggregate_version=None,
        idempotency_key=uid(305),
        request_sha256="d" * 64,
        aggregate=aggregate,
        audit=audit,
        outbox=outbox,
    )
    writes = [
        (sql, params)
        for sql, params in tx.executed
        if sql.lstrip().startswith("INSERT")
    ]
    assert writes
    assert all(params[0:2] == (TENANT, MATTER) for _, params in writes)


def test_reads_are_tenant_and_matter_scoped() -> None:
    tx = FakeTransaction()
    aggregate, _, _ = envelope()
    tx.current = {
        "aggregate_payload": json.dumps(
            aggregate.model_dump(mode="json", by_alias=True)
        )
    }
    restored = repository(tx).get(TENANT, MATTER, aggregate.work_product_id)
    assert restored == aggregate


def test_membership_claim_and_revocation_queries_fail_closed() -> None:
    tx = FakeTransaction()
    repo = repository(tx)
    assert repo.is_matter_member(TENANT, MATTER, uid(10)) is True
    assert repo.claim_exists(TENANT, MATTER, uid(11)) is True
    assert repo.current_policy_revision(TENANT, MATTER) == POLICY


def test_authorization_uses_only_exact_revocation_snapshot_boundary() -> None:
    tx = FakeTransaction()
    evidence = actor().evidence
    assert repository(tx).authorization_is_active(TENANT, MATTER, evidence, T0)
    assert len(tx.fetched) == 1
    sql, parameters = tx.fetched[0]
    assert "capability_revocation_snapshot" in sql
    assert "FROM sklegal_identity.capability_revocations" not in sql
    assert parameters == (TENANT, [DIGEST])


def test_authorization_snapshot_binds_leaf_ancestors_and_current_revision() -> None:
    ancestor = "c" * 64
    evidence = actor(ancestor_credential_digests=(ancestor,)).evidence
    tx = FakeTransaction()
    repo = repository(tx)
    assert repo.authorization_is_active(TENANT, MATTER, evidence, T0)
    assert tx.fetched[-1][1] == (TENANT, [DIGEST, ancestor])
    tx.snapshot = {
        "revision": "d" * 64,
        "revoked_credential_digests": [],
    }
    assert not repo.authorization_is_active(TENANT, MATTER, evidence, T0)
    tx.snapshot = {
        "revision": "e" * 64,
        "revoked_credential_digests": [ancestor],
    }
    revoked_evidence = evidence.model_copy(update={"revocation_revision": "e" * 64})
    assert not repo.authorization_is_active(TENANT, MATTER, revoked_evidence, T0)


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        {"revision": "not-a-digest", "revoked_credential_digests": []},
        {"revision": EMPTY_REVOCATION_REVISION, "revoked_credential_digests": "x"},
        {
            "revision": EMPTY_REVOCATION_REVISION,
            "revoked_credential_digests": ["f" * 64],
        },
    ],
)
def test_malformed_authorization_snapshot_is_sanitized(snapshot: object) -> None:
    tx = FakeTransaction()
    tx.snapshot = snapshot
    with pytest.raises(WorkProductRepositoryUnavailable) as caught:
        repository(tx).authorization_is_active(TENANT, MATTER, actor().evidence, T0)
    assert "not-a-digest" not in str(caught.value)


def test_authorization_scope_time_and_policy_deny_before_snapshot_dispatch() -> None:
    tx = FakeTransaction()
    repo = repository(tx)
    evidence = actor().evidence
    assert not repo.authorization_is_active(uid(901), MATTER, evidence, T0)
    assert not repo.authorization_is_active(TENANT, uid(902), evidence, T0)
    assert not repo.authorization_is_active(
        TENANT,
        MATTER,
        evidence.model_copy(update={"authorized_at": T0 + timedelta(seconds=1)}),
        T0,
    )
    assert not repo.authorization_is_active(
        TENANT,
        MATTER,
        evidence.model_copy(update={"policy_revision": "stale"}),
        T0,
    )
    assert tx.fetched == []
