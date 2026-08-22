"""Live PostgreSQL qualification for the durable CapAuth replay backend."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

from sklegal_capauth import PostgresReplayBackend

from tests.integration.persistence_contract_support import PersistenceContractBase


class PersistenceContract13CapAuthReplayTests(PersistenceContractBase):
    def test_17_multi_worker_replay_reservation_allows_exactly_one(self) -> None:
        tenant_id = UUID(self.fixture["tenant_alpha"])
        credential_digest = "e3" * 32
        worker_count = 8
        start = Barrier(worker_count)

        def execute(sql: str, params: tuple[object, ...]) -> object:
            start.wait(timeout=10)
            return self._capauth_execute(sql, params)

        backend = PostgresReplayBackend(execute, tenant_id=tenant_id)
        expires_at = datetime.now(UTC) + timedelta(minutes=5)

        def reserve(worker: int) -> tuple[str, bool]:
            decision_id = f"13000000-0000-4000-8000-{worker:012d}"
            return (
                decision_id,
                backend.reserve(
                    credential_digest=credential_digest,
                    decision_id=decision_id,
                    expires_at=expires_at,
                ),
            )

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            results = list(pool.map(reserve, range(1, worker_count + 1)))

        winners = [decision_id for decision_id, allowed in results if allowed]
        self.assertEqual(1, len(winners), results)
        self.assertEqual(worker_count - 1, sum(not allowed for _, allowed in results))

        stored = self._psql(
            "postgres",
            f"""
            SELECT decision_id
            FROM sklegal_identity.capability_replay_reservations
            WHERE tenant_id = '{tenant_id}'
              AND credential_digest = '{credential_digest}';
            """,
        )
        self.assertEqual(winners[0], stored.stdout.strip())
