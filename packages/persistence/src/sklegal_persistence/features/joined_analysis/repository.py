"""Driver-neutral durable repository for joined Matter analysis snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

SqlExecutor = Callable[[str, tuple[object, ...]], Sequence[Mapping[str, object]]]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PLACEHOLDER = "0" * 64

MEMBERSHIP_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM sklegal_legal.matter_memberships AS membership
    WHERE membership.tenant_id = %s
      AND membership.matter_id = %s
      AND membership.principal_id = %s
      AND membership.active
) AS member
""".strip()

CURRENT_PROJECTION_SQL = """
SELECT projection, projection_sha256
FROM sklegal_legal.joined_analysis_snapshot_current
WHERE tenant_id = %s AND matter_id = %s
LIMIT 1
""".strip()


class JoinedAnalysisPersistenceUnavailable(RuntimeError):
    """The durable read side cannot prove a valid current projection."""


@dataclass(frozen=True, slots=True)
class StoredJoinedAnalysisProjection:
    projection: Mapping[str, object]
    projection_sha256: str


def canonical_projection_sha256(projection: Mapping[str, object]) -> str:
    """Hash projection JSON with its exact digest slot replaced by zeroes."""

    try:
        copied: dict[str, Any] = json.loads(
            json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        snapshot = copied["snapshot"]
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be an object")
        snapshot["projection_sha256"] = _DIGEST_PLACEHOLDER
        canonical = json.dumps(
            copied,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:
        raise JoinedAnalysisPersistenceUnavailable(
            "joined analysis projection is not canonical JSON"
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


class PostgresJoinedAnalysisRepository:
    """Execute only parameterized, Tenant and Matter scoped read queries."""

    def __init__(self, executor: SqlExecutor) -> None:
        if not callable(executor):
            raise TypeError("executor must be callable")
        self._executor = executor

    def _execute(
        self, statement: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        try:
            rows = self._executor(statement, parameters)
        except Exception as exc:
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis persistence unavailable"
            ) from exc
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis executor returned the wrong type"
            )
        return rows

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        rows = self._execute(MEMBERSHIP_SQL, (tenant_id, matter_id, principal_id))
        if len(rows) != 1 or set(rows[0]) != {"member"}:
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis membership result is invalid"
            )
        member = rows[0]["member"]
        if not isinstance(member, bool):
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis membership result is invalid"
            )
        return member

    def load_current(
        self, tenant_id: UUID, matter_id: UUID
    ) -> StoredJoinedAnalysisProjection | None:
        rows = self._execute(CURRENT_PROJECTION_SQL, (tenant_id, matter_id))
        if not rows:
            return None
        if len(rows) != 1 or set(rows[0]) != {"projection", "projection_sha256"}:
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis snapshot result is invalid"
            )
        raw_projection = rows[0]["projection"]
        digest = rows[0]["projection_sha256"]
        if isinstance(raw_projection, str):
            try:
                raw_projection = json.loads(raw_projection)
            except ValueError as exc:
                raise JoinedAnalysisPersistenceUnavailable(
                    "joined analysis projection is invalid"
                ) from exc
        if not isinstance(raw_projection, Mapping):
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis projection is invalid"
            )
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis projection digest is invalid"
            )
        if raw_projection.get("tenant_id") != str(tenant_id) or raw_projection.get(
            "matter_id"
        ) != str(matter_id):
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis projection scope drift"
            )
        snapshot = raw_projection.get("snapshot")
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("projection_sha256") != digest
        ):
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis projection metadata drift"
            )
        if canonical_projection_sha256(raw_projection) != digest:
            raise JoinedAnalysisPersistenceUnavailable(
                "joined analysis projection digest drift"
            )
        return StoredJoinedAnalysisProjection(
            projection=raw_projection,
            projection_sha256=digest,
        )
