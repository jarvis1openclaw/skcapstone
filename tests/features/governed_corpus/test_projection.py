from __future__ import annotations

from uuid import UUID

import pytest
from sklegal_api.features.governed_corpus.service import GovernedCorpusServiceError
from sklegal_persistence.features.governed_corpus.models import (
    CorpusProjectionCommand,
    canonical_sha256,
)
from sklegal_persistence.features.governed_corpus.projector import (
    GovernedCorpusProjector,
)
from sklegal_persistence.features.governed_corpus.repository import (
    GovernedCorpusRepositoryUnavailable,
)

from .helpers import MATTER, access, composition, fixture, source_command
from .test_service import query


def _record(service, repository, index: int) -> CorpusProjectionCommand:
    _, sources = fixture()
    source = sources[index].model_copy(
        update={"source_version_id": UUID(f"30000000-0000-4000-9000-{index + 1:012d}")}
    )
    receipt = service.record_source(
        context=access("corpus.ingest.submit", "corpus_ingestion"),
        matter_id=MATTER,
        idempotency_key=f"project-{index}",
        command=source_command(source),
    )
    return CorpusProjectionCommand(
        command_id=receipt.outbox_id,
        operation="create",
        source=receipt.source,
        payload_sha256=repository.outbox_records[-1].payload_sha256,
        core_watermark=42,
        projected_at=receipt.source.recorded_at,
    )


def test_core_commit_is_not_a_hidden_retrieval_write() -> None:
    service, repository, _ = composition(seed=False)
    command = _record(service, repository, 0)
    before = service.search(context=access(), matter_id=MATTER, command=query()).result
    assert before.no_answer is True
    assert len(repository.outbox_records) == 1

    projector = GovernedCorpusProjector(
        core_repository=repository,
        retrieval_repository=repository,
    )
    assert projector.project(command.source.tenant_id, MATTER) == 1
    assert projector.project(command.source.tenant_id, MATTER) == 1
    after = service.search(context=access(), matter_id=MATTER, command=query()).result
    assert [hit.source.source_version_id for hit in after.hits] == [
        command.source.source_version_id
    ]


def test_retrieval_outage_preserves_core_commit_and_fails_search_closed() -> None:
    service, repository, _ = composition(seed=False)
    command = _record(service, repository, 0)
    repository.retrieval_available = False
    second = _record(service, repository, 1)
    assert len(repository.audit_events) == 2
    assert len(repository.outbox_records) == 2
    with pytest.raises(GovernedCorpusRepositoryUnavailable):
        repository.apply_projection(command)
    with pytest.raises(GovernedCorpusServiceError) as unavailable:
        service.search(context=access(), matter_id=MATTER, command=query())
    assert unavailable.value.code == "resource_unavailable"

    repository.retrieval_available = True
    repository.apply_projection(command)
    repository.apply_projection(second)
    assert (
        service.search(
            context=access(), matter_id=MATTER, command=query()
        ).result.no_answer
        is False
    )


def test_projection_rebuild_is_order_independent_and_revocable() -> None:
    service, repository, _ = composition(seed=False)
    commands = (_record(service, repository, 0), _record(service, repository, 1))
    repository.rebuild_projection(
        commands[0].source.tenant_id,
        MATTER,
        reversed(commands),
    )
    first = service.search(
        context=access(), matter_id=MATTER, command=query()
    ).result.model_dump(mode="json")
    repository.rebuild_projection(
        commands[0].source.tenant_id,
        MATTER,
        commands,
    )
    second = service.search(
        context=access(), matter_id=MATTER, command=query()
    ).result.model_dump(mode="json")
    assert first == second

    corrected_source = commands[0].source.model_copy(
        update={"title": "Corrected public synthetic filing record"}
    )
    corrected = CorpusProjectionCommand(
        command_id=UUID("90000000-0000-4000-8000-000000000002"),
        operation="correct",
        source=corrected_source,
        payload_sha256=canonical_sha256(corrected_source),
        core_watermark=42,
        projected_at=commands[0].projected_at,
    )
    repository.apply_projection(corrected)
    assert (
        repository._projection_versions[
            (
                corrected_source.tenant_id,
                corrected_source.matter_id,
                corrected_source.source_version_id,
            )
        ].title
        == "Corrected public synthetic filing record"
    )

    revoked = CorpusProjectionCommand(
        command_id=UUID("90000000-0000-4000-8000-000000000001"),
        operation="revoke",
        source=commands[0].source,
        payload_sha256=commands[0].payload_sha256,
        core_watermark=42,
        projected_at=commands[0].projected_at,
    )
    repository.apply_projection(revoked)
    remaining = service.search(
        context=access(), matter_id=MATTER, command=query()
    ).result
    assert all(
        hit.source.source_version_id != commands[0].source.source_version_id
        for hit in remaining.hits
    )
