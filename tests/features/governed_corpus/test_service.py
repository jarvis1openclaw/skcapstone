from __future__ import annotations

import base64
import json
from datetime import timedelta
from uuid import UUID

import pytest
import sklegal_persistence.features.governed_corpus.repository as corpus_repository
from pydantic import ValidationError
from sklegal_api.features.governed_corpus.contracts import (
    CorpusSearchCommand,
    CorpusSpanCommand,
)
from sklegal_api.features.governed_corpus.service import GovernedCorpusServiceError
from sklegal_persistence.features.governed_corpus.models import (
    Classification,
    CorpusProjectionCommand,
    CorpusSourceVersion,
    ProjectionGuardStatus,
    ProjectionState,
)

from .helpers import (
    MATTER,
    NOW,
    OTHER_MATTER,
    OTHER_PRINCIPAL,
    RIGHTS,
    TENANT,
    access,
    composition,
    fixture,
    source_command,
)


def query(mode: str = "full_text", **changes: object) -> CorpusSearchCommand:
    values: dict[str, object] = {
        "query": "public synthetic filing date",
        "mode": mode,
        "max_results": 10,
        "expected_release_id": "synthetic-release-1",
        "expected_projection_generation": 3,
        "required_core_watermark": 42,
    }
    if mode != "full_text":
        values["query_embedding"] = (1.0, 0.0, 0.0)
    values.update(changes)
    return CorpusSearchCommand.model_validate(values)


def add_sources(service, count: int, *, first: int = 10) -> None:
    _, sources = fixture()
    template = source_command(sources[0]).model_dump(mode="python")
    for offset in range(count):
        ordinal = first + offset
        values = {
            **template,
            "source_id": f"synthetic-page-source-{ordinal}",
            "source_version_id": UUID(f"30000000-0000-4000-8000-{ordinal:012d}"),
            "source_version": f"page-{ordinal}",
        }
        receipt = service.record_source(
            context=access("corpus.ingest.submit", "corpus_ingestion"),
            matter_id=MATTER,
            idempotency_key=f"page-source-{ordinal}",
            command=type(source_command(sources[0])).model_validate(values),
        )
        repository = service._retrieval_repository
        repository.apply_projection(
            CorpusProjectionCommand(
                command_id=receipt.outbox_id,
                operation="create",
                source=receipt.source,
                payload_sha256=repository.outbox_records[-1].payload_sha256,
                core_watermark=42,
                projected_at=receipt.source.recorded_at,
            )
        )


def hit_ids(result) -> tuple[UUID, ...]:
    return tuple(hit.source.source_version_id for hit in result.hits)


@pytest.mark.parametrize(
    ("mode", "path"),
    [
        ("full_text", ("full_text",)),
        ("vector_exact", ("pgvector_exact",)),
        ("hybrid_rrf", ("full_text", "pgvector_exact", "hybrid_rrf")),
    ],
)
def test_all_rank_modes_preserve_exact_governance(
    mode: str, path: tuple[str, ...]
) -> None:
    service, _, _ = composition()
    result = service.search(
        context=access(), matter_id=MATTER, command=query(mode)
    ).result
    assert result.no_answer is False
    assert result.projection.release_id == "synthetic-release-1"
    assert result.projection.projection_generation == 3
    assert result.projection.backend_watermark == 42
    assert result.hits[0].rank.rank_path == path
    assert result.hits[0].source.source_sha256
    assert result.hits[0].source.locator.end == len(result.hits[0].source.exact_span)
    assert result.hits[0].source.rights_revision == RIGHTS


def test_no_answer_is_explicit_and_discloses_no_source_presence() -> None:
    service, _, _ = composition()
    result = service.search(
        context=access(),
        matter_id=MATTER,
        command=query(query="not-present-token"),
    ).result
    assert result.no_answer is True
    assert result.hits == ()


def test_cursor_page_two_is_complete_nonoverlapping_and_ranked() -> None:
    service, _, _ = composition()
    add_sources(service, 2)
    complete = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=10)
    ).result
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    assert first.continuation_cursor is not None
    repeated_first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    assert repeated_first.continuation_cursor == first.continuation_cursor
    second = service.search(
        context=access(),
        matter_id=MATTER,
        command=query(max_results=2, continuation_cursor=first.continuation_cursor),
    ).result
    assert set(hit_ids(first)).isdisjoint(hit_ids(second))
    assert hit_ids(first) + hit_ids(second) == hit_ids(complete)
    assert tuple(hit.rank.rank for hit in first.hits) == (1, 2)
    assert tuple(hit.rank.rank for hit in second.hits) == (3, 4)
    assert second.continuation_cursor is None


def test_cursor_accepts_fresh_authorization_and_policy_decision_ids() -> None:
    service, _, policy = composition()
    add_sources(service, 2)
    decision_counter = 0
    original_authorize = policy.authorize

    def authorize(**values):
        nonlocal decision_counter
        decision_counter += 1
        decision = original_authorize(**values)
        return decision.model_copy(
            update={
                "decision_id": UUID(f"51000000-0000-4000-8000-{decision_counter:012d}")
            }
        )

    policy.authorize = authorize  # type: ignore[method-assign]
    first_context = access()
    first = service.search(
        context=first_context,
        matter_id=MATTER,
        command=query(max_results=2),
    ).result
    assert first.continuation_cursor is not None
    encoded_payload = first.continuation_cursor.partition(".")[0]
    payload = json.loads(
        base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
    )
    assert "authorization_decision_id" not in payload
    assert "policy_decision_id" not in payload
    assert payload["authorization_context_sha256"] == "3" * 64
    assert payload["operation"] == "corpus.search"
    assert payload["capability"] == "corpus.search"
    assert payload["purpose"] == "legal_research"
    second_context = first_context.model_copy(
        update={
            "authorization_decision_id": UUID("50000000-0000-4000-8000-000000000002")
        }
    )
    second = service.search(
        context=second_context,
        matter_id=MATTER,
        command=query(max_results=2, continuation_cursor=first.continuation_cursor),
    ).result
    assert first.authorization_decision_id != second.authorization_decision_id
    assert first.policy_decision_id != second.policy_decision_id
    assert set(hit_ids(first)).isdisjoint(hit_ids(second))


def test_cursor_rejects_changed_stable_authorization_and_policy_context() -> None:
    service, _, policy = composition()
    add_sources(service, 2)
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    cursor = first.continuation_cursor
    assert cursor is not None
    changed_context = access().model_copy(
        update={"authorization_context_sha256": "4" * 64}
    )
    with pytest.raises(GovernedCorpusServiceError) as authorization_error:
        service.search(
            context=changed_context,
            matter_id=MATTER,
            command=query(max_results=2, continuation_cursor=cursor),
        )
    assert authorization_error.value.code == "validation_failed"

    policy.revision = "5" * 64
    with pytest.raises(GovernedCorpusServiceError) as policy_error:
        service.search(
            context=access(),
            matter_id=MATTER,
            command=query(max_results=2, continuation_cursor=cursor),
        )
    assert policy_error.value.code == "validation_failed"


def test_cursor_snapshot_excludes_later_unrelated_writes() -> None:
    current = [NOW]
    service, _, _ = composition(clock=lambda: current[0])
    add_sources(service, 2)
    before = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=10)
    ).result
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    current[0] += timedelta(minutes=1)
    add_sources(service, 1, first=20)
    second = service.search(
        context=access(),
        matter_id=MATTER,
        command=query(max_results=2, continuation_cursor=first.continuation_cursor),
    ).result
    assert hit_ids(first) + hit_ids(second) == hit_ids(before)


def test_cursor_tamper_query_rank_and_page_size_mismatch_fail_closed() -> None:
    service, _, _ = composition()
    add_sources(service, 2)
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    cursor = first.continuation_cursor
    assert cursor is not None
    replacement = "A" if cursor[-1] != "A" else "B"
    commands = (
        query(max_results=2, continuation_cursor=cursor[:-1] + replacement),
        query(
            query="synthetic changed",
            max_results=2,
            continuation_cursor=cursor,
        ),
        query("hybrid_rrf", max_results=2, continuation_cursor=cursor),
        query(max_results=1, continuation_cursor=cursor),
    )
    for command in commands:
        with pytest.raises(GovernedCorpusServiceError) as captured:
            service.search(context=access(), matter_id=MATTER, command=command)
        assert captured.value.code == "validation_failed"


def test_cursor_cross_scope_fails_closed() -> None:
    service, repository, policy = composition()
    add_sources(service, 2)
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    cursor = first.continuation_cursor
    assert cursor is not None
    projection, _ = fixture()
    repository.set_matter_members(TENANT, OTHER_MATTER, {access().principal_id})
    repository.set_projection(TENANT, OTHER_MATTER, projection)
    policy.grants.add(
        (
            TENANT,
            OTHER_MATTER,
            access().principal_id,
            "corpus.search",
            "legal_research",
        )
    )
    with pytest.raises(GovernedCorpusServiceError) as cross_scope:
        service.search(
            context=access(matter_id=OTHER_MATTER),
            matter_id=OTHER_MATTER,
            command=query(max_results=2, continuation_cursor=cursor),
        )
    assert cross_scope.value.code == "validation_failed"


@pytest.mark.parametrize(
    ("projection_changes", "command_changes"),
    [
        (
            {"release_id": "synthetic-release-2"},
            {"expected_release_id": "synthetic-release-2"},
        ),
        (
            {"projection_generation": 4},
            {"expected_projection_generation": 4},
        ),
        (
            {"backend_watermark": 43, "core_watermark": 43},
            {"required_core_watermark": 43},
        ),
    ],
)
def test_cursor_stale_release_generation_and_watermark_fail_closed(
    projection_changes: dict[str, object], command_changes: dict[str, object]
) -> None:
    service, repository, _ = composition()
    projection, _ = fixture()
    add_sources(service, 2)
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    cursor = first.continuation_cursor
    assert cursor is not None
    advanced = projection.model_copy(update=projection_changes)
    repository.set_projection(TENANT, MATTER, advanced)
    with pytest.raises(GovernedCorpusServiceError) as stale:
        service.search(
            context=access(),
            matter_id=MATTER,
            command=query(
                max_results=2,
                continuation_cursor=cursor,
                **command_changes,
            ),
        )
    assert stale.value.code == "validation_failed"


def test_cursor_filter_sensitivity_detects_overlap(monkeypatch) -> None:
    service, _, _ = composition()
    add_sources(service, 2)
    first = service.search(
        context=access(), matter_id=MATTER, command=query(max_results=2)
    ).result
    monkeypatch.setattr(corpus_repository, "_after_cursor", lambda _row, _query: True)
    with pytest.raises(GovernedCorpusServiceError) as captured:
        service.search(
            context=access(),
            matter_id=MATTER,
            command=query(max_results=2, continuation_cursor=first.continuation_cursor),
        )
    assert captured.value.code == "internal_error"


def test_search_wire_keeps_decisions_but_omits_rights_roster_and_embedding() -> None:
    service, _, _ = composition()
    response = service.search(
        context=access(), matter_id=MATTER, command=query()
    ).model_dump(mode="json", by_alias=True)
    encoded = str(response)
    assert response["result"]["authorizationDecisionId"]
    assert response["result"]["policyDecisionId"]
    assert "permittedPrincipalIds" not in encoded
    assert "embedding" not in encoded


def test_exact_span_requires_current_rights_and_returns_full_lineage() -> None:
    service, _, policy = composition()
    span = service.read_span(
        context=access("corpus.artifact.read", "legal_research"),
        matter_id=MATTER,
        command=CorpusSpanCommand(
            source_id="synthetic-filing-record",
            expected_release_id="synthetic-release-1",
            expected_projection_generation=3,
            required_core_watermark=42,
        ),
    )
    assert span.source.chunk_sha256
    assert span.source.source_role == "matter_evidence"
    policy.rights_revision = "f" * 64
    with pytest.raises(GovernedCorpusServiceError, match="not_found"):
        service.read_span(
            context=access("corpus.artifact.read", "legal_research"),
            matter_id=MATTER,
            command=CorpusSpanCommand(
                source_id="synthetic-filing-record",
                expected_release_id="synthetic-release-1",
                expected_projection_generation=3,
                required_core_watermark=42,
            ),
        )


def test_classification_denial_matches_an_unknown_source() -> None:
    service, _, policy = composition(seed=False)
    _, sources = fixture()
    policy.classification_ceiling = Classification.INTERNAL
    command_values = source_command(sources[0]).model_dump(mode="python")
    command_values["classification"] = Classification.INTERNAL
    service.record_source(
        context=access("corpus.ingest.submit", "corpus_ingestion"),
        matter_id=MATTER,
        idempotency_key="internal-source",
        command=type(source_command(sources[0])).model_validate(command_values),
    )
    policy.classification_ceiling = Classification.PUBLIC
    denied = CorpusSpanCommand(
        source_id=sources[0].source_id,
        expected_release_id="synthetic-release-1",
        expected_projection_generation=3,
        required_core_watermark=42,
    )
    unknown = denied.model_copy(update={"source_id": "unknown-source"})
    for command in (denied, unknown):
        with pytest.raises(GovernedCorpusServiceError) as captured:
            service.read_span(
                context=access("corpus.artifact.read", "legal_research"),
                matter_id=MATTER,
                command=command,
            )
        assert captured.value.code == "not_found"


@pytest.mark.parametrize(
    "context",
    [
        access(matter_id=OTHER_MATTER),
        access(principal_id=OTHER_PRINCIPAL),
        access("corpus.artifact.read", "legal_research"),
        access("corpus.search", "corpus_ingestion"),
        access(revoked=True),
    ],
)
def test_wrong_scope_member_capability_and_revocation_fail_closed(context) -> None:
    service, _, _ = composition()
    with pytest.raises(GovernedCorpusServiceError, match="access_denied"):
        service.search(context=context, matter_id=MATTER, command=query())


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_release_id": "stale-release"},
        {"expected_projection_generation": 2},
        {"required_core_watermark": 41},
    ],
)
def test_stale_release_generation_and_watermark_fail_closed(changes) -> None:
    service, _, _ = composition()
    with pytest.raises(GovernedCorpusServiceError, match="stale_projection"):
        service.search(context=access(), matter_id=MATTER, command=query(**changes))


def test_projection_lag_and_dependency_outages_are_sanitized() -> None:
    service, repository, policy = composition()
    projection, _ = fixture()
    repository.set_projection(
        TENANT,
        MATTER,
        ProjectionState.model_validate({**projection.model_dump(), "lag_events": 3}),
    )
    with pytest.raises(GovernedCorpusServiceError, match="stale_projection"):
        service.search(context=access(), matter_id=MATTER, command=query())
    repository.set_projection(TENANT, MATTER, projection)
    policy.available = False
    with pytest.raises(GovernedCorpusServiceError, match="policy_unavailable"):
        service.search(context=access(), matter_id=MATTER, command=query())
    policy.available = True
    repository.available = False
    with pytest.raises(GovernedCorpusServiceError, match="resource_unavailable"):
        service.search(context=access(), matter_id=MATTER, command=query())


def test_projection_guard_states_are_distinct_and_fail_closed() -> None:
    service, repository, policy = composition()
    repository.retrieval_available = False
    with pytest.raises(GovernedCorpusServiceError) as unavailable:
        service.search(context=access(), matter_id=MATTER, command=query())
    assert unavailable.value.projection_status is ProjectionGuardStatus.UNAVAILABLE

    repository.retrieval_available = True
    registry = repository._registries.pop((TENANT, MATTER))
    with pytest.raises(GovernedCorpusServiceError) as unknown:
        service.search(context=access(), matter_id=MATTER, command=query())
    assert unknown.value.projection_status is ProjectionGuardStatus.UNKNOWN

    repository._registries[(TENANT, MATTER)] = registry
    projection, _ = fixture()
    repository.set_projection(
        TENANT,
        MATTER,
        projection.model_copy(update={"backend_watermark": 41}),
    )
    with pytest.raises(GovernedCorpusServiceError) as stale:
        service.search(context=access(), matter_id=MATTER, command=query())
    assert stale.value.projection_status is ProjectionGuardStatus.STALE

    repository.set_projection(TENANT, MATTER, projection)
    policy.rights_revision = "f" * 64
    with pytest.raises(GovernedCorpusServiceError) as unauthorized:
        service.search(context=access(), matter_id=MATTER, command=query())
    assert unauthorized.value.projection_status is ProjectionGuardStatus.UNAUTHORIZED


def test_source_write_is_idempotent_atomic_and_supersession_is_current_only() -> None:
    service, repository, _ = composition(seed=False)
    _, sources = fixture()
    first_command = source_command(sources[0])
    first = service.record_source(
        context=access("corpus.ingest.submit", "corpus_ingestion"),
        matter_id=MATTER,
        idempotency_key="source-one",
        command=first_command,
    )
    replay = service.record_source(
        context=access("corpus.ingest.submit", "corpus_ingestion"),
        matter_id=MATTER,
        idempotency_key="source-one",
        command=first_command,
    )
    assert replay.source == first.source
    assert len(repository.audit_events) == 1
    assert len(repository.outbox_records) == 1
    assert repository.outbox_records[0].qdrant_dispatch_allowed is False
    assert repository.outbox_records[0].falkordb_dispatch_allowed is False
    repository.apply_projection(
        CorpusProjectionCommand(
            command_id=first.outbox_id,
            operation="create",
            source=first.source,
            payload_sha256=repository.outbox_records[0].payload_sha256,
            core_watermark=42,
            projected_at=first.source.recorded_at,
        )
    )

    successor_data = first_command.model_dump(mode="python")
    successor_data.update(
        source_version_id=UUID("30000000-0000-4000-8000-000000000099"),
        source_version="v2",
        supersedes_source_version_id=first.source.source_version_id,
    )
    successor = service.record_source(
        context=access("corpus.ingest.submit", "corpus_ingestion"),
        matter_id=MATTER,
        idempotency_key="source-two",
        command=type(first_command).model_validate(successor_data),
    )
    repository.apply_projection(
        CorpusProjectionCommand(
            command_id=successor.outbox_id,
            operation="supersede",
            source=successor.source,
            payload_sha256=repository.outbox_records[1].payload_sha256,
            core_watermark=42,
            projected_at=successor.source.recorded_at,
        )
    )
    result = service.search(context=access(), matter_id=MATTER, command=query()).result
    assert tuple(hit.source.source_version_id for hit in result.hits) == (
        successor.source.source_version_id,
    )


def test_audit_and_outbox_failure_cannot_partially_mutate() -> None:
    _, source_rows = fixture()
    for flag in ("audit_available", "outbox_available"):
        service, repository, _ = composition(seed=False)
        setattr(repository, flag, False)
        with pytest.raises(GovernedCorpusServiceError, match="resource_unavailable"):
            service.record_source(
                context=access("corpus.ingest.submit", "corpus_ingestion"),
                matter_id=MATTER,
                idempotency_key=flag,
                command=source_command(source_rows[0]),
            )
        assert repository.audit_events == ()
        assert repository.outbox_records == ()


def test_wrong_hash_locator_and_malformed_vectors_are_rejected() -> None:
    _, sources = fixture()
    value = sources[0].model_dump(mode="python")
    with pytest.raises(ValidationError):
        CorpusSourceVersion.model_validate({**value, "chunk_sha256": "f" * 64})
    with pytest.raises(ValidationError):
        CorpusSourceVersion.model_validate(
            {**value, "locator": {"kind": "character", "start": 0, "end": 2}}
        )
    with pytest.raises(ValidationError):
        CorpusSourceVersion.model_validate({**value, "embedding": (float("nan"),)})
    with pytest.raises(ValidationError):
        query("vector_exact", query_embedding=(float("inf"),))
