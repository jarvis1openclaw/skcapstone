"""Public-synthetic and durable adapters for joined Matter analysis."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .contract import JoinedAnalysisSnapshotProjection
from .service import (
    JoinedAnalysisStoreUnavailable,
    projection_provenance_sha256,
    projection_sha256,
    verify_projection_digest,
)

_PUBLIC_SYNTHETIC_DIGEST_SLOT = "0" * 64
PUBLIC_SYNTHETIC_PROJECTION_SHA256 = "".join(
    (
        "64f6215b",
        "54dee775",
        "7840683e",
        "f1cadbe8",
        "ce71b975",
        "209b6615",
        "bc6bd722",
        "6d91c7f9",
    )
)


class StoredProjection(Protocol):
    projection: Mapping[str, object]
    projection_sha256: str


class JoinedAnalysisRepository(Protocol):
    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool: ...

    def load_current(
        self, tenant_id: UUID, matter_id: UUID
    ) -> StoredProjection | None: ...


def seal_public_synthetic_projection(
    payload: object,
) -> JoinedAnalysisSnapshotProjection:
    """Compile an explicit public fixture template into immutable bytes."""

    try:
        template = JoinedAnalysisSnapshotProjection.model_validate(payload)
    except Exception as exc:
        raise JoinedAnalysisStoreUnavailable("public projection is invalid") from exc
    if template.classification.source_rights != "public_synthetic" or any(
        getattr(template.snapshot, name) != _PUBLIC_SYNTHETIC_DIGEST_SLOT
        for name in (
            "matter_snapshot_sha256",
            "claim_projection_revision",
            "authority_snapshot",
            "projection_revision",
            "projection_sha256",
        )
    ):
        raise JoinedAnalysisStoreUnavailable(
            "public projection template is not explicit"
        )
    provenance = projection_provenance_sha256(template)
    snapshot_with_provenance = template.snapshot.model_copy(update=provenance)
    compiled = template.model_copy(update={"snapshot": snapshot_with_provenance})
    calculated = projection_sha256(compiled)
    if calculated != PUBLIC_SYNTHETIC_PROJECTION_SHA256:
        raise JoinedAnalysisStoreUnavailable("public projection digest drift")
    snapshot = snapshot_with_provenance.model_copy(
        update={"projection_sha256": calculated}
    )
    sealed = template.model_copy(update={"snapshot": snapshot})
    verify_projection_digest(sealed)
    return sealed


class PublicSyntheticJoinedAnalysisStore:
    """Explicit public fixture adapter forbidden from implicit membership."""

    synthetic = True

    def __init__(self, fixture_path: Path, *, available: bool = True) -> None:
        self.available = available
        self._fixture_path = fixture_path
        self._members: dict[tuple[UUID, UUID], frozenset[UUID]] = {}

    def set_matter_members(
        self, tenant_id: UUID, matter_id: UUID, principal_ids: frozenset[UUID]
    ) -> None:
        self._members[(tenant_id, matter_id)] = principal_ids

    def _require_available(self) -> None:
        if not self.available:
            raise JoinedAnalysisStoreUnavailable("joined analysis store unavailable")

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self._require_available()
        return principal_id in self._members.get((tenant_id, matter_id), frozenset())

    def projection(
        self, tenant_id: UUID, matter_id: UUID
    ) -> JoinedAnalysisSnapshotProjection | None:
        self._require_available()
        try:
            payload = json.loads(self._fixture_path.read_text(encoding="utf-8"))
            projection = seal_public_synthetic_projection(payload)
        except JoinedAnalysisStoreUnavailable:
            raise
        except Exception as exc:
            raise JoinedAnalysisStoreUnavailable(
                "public projection is invalid"
            ) from exc
        if projection.tenant_id != tenant_id or projection.matter_id != matter_id:
            return None
        verify_projection_digest(projection)
        return projection


class PostgresJoinedAnalysisStore:
    """Validate repository output through the same closed response contract."""

    synthetic = False

    def __init__(self, repository: JoinedAnalysisRepository) -> None:
        self._repository = repository

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        return self._repository.is_matter_member(tenant_id, matter_id, principal_id)

    def projection(
        self, tenant_id: UUID, matter_id: UUID
    ) -> JoinedAnalysisSnapshotProjection | None:
        stored = self._repository.load_current(tenant_id, matter_id)
        if stored is None:
            return None
        try:
            projection = JoinedAnalysisSnapshotProjection.model_validate(
                stored.projection
            )
        except Exception as exc:
            raise JoinedAnalysisStoreUnavailable(
                "durable projection is invalid"
            ) from exc
        if stored.projection_sha256 != projection.snapshot.projection_sha256:
            raise JoinedAnalysisStoreUnavailable("durable projection metadata drift")
        verify_projection_digest(projection)
        return projection
