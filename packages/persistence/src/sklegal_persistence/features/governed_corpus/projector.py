"""Core-first governed corpus outbox projector."""

from __future__ import annotations

from uuid import UUID

from .repository import (
    GovernedCorpusCoreRepository,
    GovernedCorpusRetrievalRepository,
)


class GovernedCorpusProjector:
    """Replay canonical core commands into one rebuildable retrieval store."""

    def __init__(
        self,
        *,
        core_repository: GovernedCorpusCoreRepository,
        retrieval_repository: GovernedCorpusRetrievalRepository,
    ) -> None:
        self._core = core_repository
        self._retrieval = retrieval_repository

    def project(self, tenant_id: UUID, matter_id: UUID) -> int:
        commands = self._core.projection_commands(tenant_id, matter_id)
        for command in commands:
            self._retrieval.apply_projection(command)
        return len(commands)

    def rebuild(self, tenant_id: UUID, matter_id: UUID) -> int:
        commands = self._core.projection_commands(tenant_id, matter_id)
        self._retrieval.rebuild_projection(tenant_id, matter_id, commands)
        return len(commands)


__all__ = ["GovernedCorpusProjector"]
