from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sklegal_persistence.features.artifact_intake.repository import (
    ATOMIC_ARTIFACT_CORRECTION_SQL,
    ATOMIC_ARTIFACT_READ_SQL,
    ATOMIC_ARTIFACT_REVIEW_SQL,
    ATOMIC_ARTIFACT_SUPERSESSION_SQL,
    ArtifactCorrectionBatch,
    ArtifactMutationContext,
    ArtifactPersistenceBatch,
    ArtifactPersistenceConflict,
    ArtifactPersistenceUnavailable,
    ArtifactReadContext,
    ArtifactReviewBatch,
    ArtifactSupersessionBatch,
    DerivedArtifactRow,
    PostgresArtifactRepository,
    ProposedArtifactLinkRow,
)

T0 = datetime(2099, 1, 2, 3, 4, 5, tzinfo=UTC)


def batch() -> ArtifactPersistenceBatch:
    return ArtifactPersistenceBatch(
        tenant_id=UUID("10000000-0000-4000-8000-000000000001"),
        matter_id=UUID("20000000-0000-4000-8000-000000000001"),
        artifact_id=UUID("30000000-0000-4000-8000-000000000001"),
        principal_id=UUID("40000000-0000-4000-8000-000000000001"),
        source_identity="synthetic-source-001",
        source_version="v1",
        source_identity_sha256="1" * 64,
        filename="synthetic-exhibit.txt",
        media_type="text/plain",
        byte_count=64,
        content_sha256="2" * 64,
        storage_locator="synthetic:sha256:" + "2" * 64,
        acquisition_method="synthetic_adapter",
        classification="public",
        privilege_state="not_privileged",
        retention_policy_id=UUID("50000000-0000-4000-8000-000000000001"),
        legal_hold_ids=(),
        ethical_wall_ids=(),
        policy_decision_id=UUID("60000000-0000-4000-8000-000000000001"),
        policy_revision="3" * 64,
        scan_id=UUID("70000000-0000-4000-8000-000000000001"),
        scan_state="clean",
        scanner_name="synthetic-scanner",
        scanner_version="1.0.0",
        signature_revision="4" * 64,
        custody_event_id=UUID("80000000-0000-4000-8000-000000000001"),
        idempotency_key="artifact-persistence-001",
        idempotency_key_sha256="5" * 64,
        request_sha256="6" * 64,
        audit_event_id=UUID("90000000-0000-4000-8000-000000000001"),
        outbox_id=UUID("a0000000-0000-4000-8000-000000000001"),
        occurred_at=T0,
        derived=(
            DerivedArtifactRow(
                artifact_id=UUID("b0000000-0000-4000-8000-000000000001"),
                scan_id=UUID("b0000000-0000-4000-8000-000000000002"),
                custody_event_id=UUID("b0000000-0000-4000-8000-000000000003"),
                filename="synthetic-exhibit.txt.ocr.txt",
                media_type="text/plain",
                byte_count=32,
                content_sha256="7" * 64,
                storage_locator="synthetic:sha256:" + "7" * 64,
                derivation_kind="ocr",
                tool_name="synthetic-ocr",
                tool_version="1.0.0",
            ),
        ),
        proposed_links=(
            ProposedArtifactLinkRow(
                link_id=UUID("c0000000-0000-4000-8000-000000000001"),
                target_type="fact_assertion",
                target_id=UUID("d0000000-0000-4000-8000-000000000001"),
                rationale="Synthetic proposed support link.",
            ),
        ),
    )


def mutation_context() -> ArtifactMutationContext:
    source = batch()
    return ArtifactMutationContext(
        tenant_id=source.tenant_id,
        matter_id=source.matter_id,
        artifact_id=source.derived[0].artifact_id,
        principal_id=source.principal_id,
        expected_projection_revision=1,
        idempotency_key_sha256=source.idempotency_key_sha256,
        request_sha256=source.request_sha256,
        policy_decision_id=source.policy_decision_id,
        policy_revision=source.policy_revision,
        audit_event_id=source.audit_event_id,
        outbox_id=source.outbox_id,
        occurred_at=source.occurred_at,
    )


class RecordingSession:
    def __init__(self, result: list[Mapping[str, object]]) -> None:
        self.result = result
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        self.calls.append((statement, parameters))
        return self.result


def repository(
    result: list[Mapping[str, object]], *, fail: bool = False
) -> tuple[PostgresArtifactRepository, RecordingSession]:
    session = RecordingSession(result)

    @contextmanager
    def factory() -> Iterator[RecordingSession]:
        if fail:
            raise RuntimeError("synthetic database outage")
        yield session

    return PostgresArtifactRepository(factory), session


def test_atomic_repository_sends_one_hash_only_transaction() -> None:
    repo, session = repository(
        [
            {
                "artifact_id": batch().artifact_id,
                "duplicate": False,
                "replayed": False,
                "conflict": False,
                "projection_revision": 1,
            }
        ]
    )

    receipt = repo.append_intake(batch())

    assert receipt.artifact_id == batch().artifact_id
    assert receipt.duplicate is False
    assert receipt.replayed is False
    assert len(session.calls) == 1
    statement, parameters = session.calls[0]
    assert "artifact_idempotency_receipts" in statement
    assert "artifact_audit_facts" in statement
    assert "artifact_outbox" in statement
    assert "projection_revision + 1" in statement
    assert "UPDATE sklegal_artifact" not in statement
    assert "jsonb_to_recordset" in statement
    assert "inserted_derived_scans" in statement
    assert "inserted_derived_custody" in statement
    assert "inserted_duplicate_projection" in statement
    assert "artifact_projection_revisions" in statement
    assert "content_base64" not in statement
    assert "content_base64" not in parameters
    assert parameters["content_sha256"] == "2" * 64
    derived = parameters["derived"]
    assert isinstance(derived, list)
    assert derived[0]["content_sha256"] == "7" * 64


def test_repository_reports_replay_duplicate_and_conflict_without_guessing() -> None:
    replay_repo, _ = repository(
        [
            {
                "artifact_id": batch().artifact_id,
                "duplicate": True,
                "replayed": True,
                "conflict": False,
                "projection_revision": 2,
            }
        ]
    )
    replay = replay_repo.append_intake(batch())
    assert replay.replayed is True
    assert replay.duplicate is True

    conflict_repo, _ = repository(
        [
            {
                "artifact_id": None,
                "duplicate": False,
                "replayed": False,
                "conflict": True,
                "projection_revision": None,
            }
        ]
    )
    with pytest.raises(ArtifactPersistenceConflict):
        conflict_repo.append_intake(batch())


def test_repository_fails_closed_on_outage_or_invalid_driver_response() -> None:
    outage, _ = repository([], fail=True)
    with pytest.raises(ArtifactPersistenceUnavailable):
        outage.append_intake(batch())

    empty, _ = repository([])
    with pytest.raises(ArtifactPersistenceUnavailable):
        empty.append_intake(batch())


def test_repository_exposes_atomic_read_review_correction_and_supersession() -> None:
    expected_id = mutation_context().artifact_id
    repo, session = repository(
        [
            {
                "artifact_id": expected_id,
                "projection_revision": 2,
                "replayed": False,
                "conflict": False,
                "precondition": False,
            }
        ]
    )
    review = repo.append_review(
        ArtifactReviewBatch(
            context=mutation_context(),
            review_id=UUID("e0000000-0000-4000-8000-000000000001"),
            decision="accepted",
            rationale="Synthetic review accepted the exact projection.",
        )
    )
    assert review.projection_revision == 2
    assert session.calls[-1][0] == ATOMIC_ARTIFACT_REVIEW_SQL
    assert "artifact_idempotency_receipts" in session.calls[-1][0]
    assert "artifact_audit_facts" in session.calls[-1][0]
    assert "artifact_outbox" in session.calls[-1][0]

    repo, session = repository(
        [
            {
                "artifact_id": expected_id,
                "projection_revision": 2,
                "replayed": False,
                "conflict": False,
                "precondition": False,
            }
        ]
    )
    supersession = repo.append_supersession(
        ArtifactSupersessionBatch(
            context=mutation_context(),
            supersession_id=UUID("e0000000-0000-4000-8000-000000000002"),
            successor_artifact_id=UUID("e0000000-0000-4000-8000-000000000003"),
            reason="Synthetic successor was selected by exact review.",
        )
    )
    assert supersession.projection_revision == 2
    assert session.calls[-1][0] == ATOMIC_ARTIFACT_SUPERSESSION_SQL

    corrected_row = DerivedArtifactRow(
        artifact_id=UUID("e0000000-0000-4000-8000-000000000004"),
        scan_id=UUID("e0000000-0000-4000-8000-000000000005"),
        custody_event_id=UUID("e0000000-0000-4000-8000-000000000006"),
        filename="synthetic.corrected.txt",
        media_type="text/plain",
        byte_count=33,
        content_sha256="a" * 64,
        storage_locator="synthetic:sha256:" + "a" * 64,
        derivation_kind="human_correction",
        tool_name="human-review",
        tool_version="1",
    )
    repo, session = repository(
        [
            {
                "artifact_id": corrected_row.artifact_id,
                "projection_revision": 1,
                "replayed": False,
                "conflict": False,
                "precondition": False,
            }
        ]
    )
    correction = repo.append_correction(
        ArtifactCorrectionBatch(
            context=mutation_context(),
            correction_id=UUID("e0000000-0000-4000-8000-000000000007"),
            supersession_id=UUID("e0000000-0000-4000-8000-000000000008"),
            corrected=corrected_row,
            reason="Synthetic correction preserves immutable original bytes.",
        )
    )
    assert correction.artifact_id == corrected_row.artifact_id
    assert session.calls[-1][0] == ATOMIC_ARTIFACT_CORRECTION_SQL
    assert session.calls[-1][1]["corrected_content_sha256"] == "a" * 64


def test_repository_audited_read_includes_revision_and_custody_parity() -> None:
    source = batch()
    repo, session = repository(
        [
            {
                "artifact_id": source.artifact_id,
                "artifact_kind": "original",
                "content_sha256": source.content_sha256,
                "original_sha256": source.content_sha256,
                "review_state": "proposed",
                "projection_revision": 2,
                "custody_count": 2,
            }
        ]
    )
    artifact = repo.read(
        ArtifactReadContext(
            tenant_id=source.tenant_id,
            matter_id=source.matter_id,
            artifact_id=source.artifact_id,
            principal_id=source.principal_id,
            policy_decision_id=source.policy_decision_id,
            policy_revision=source.policy_revision,
            request_sha256=source.request_sha256,
            audit_event_id=source.audit_event_id,
            outbox_id=source.outbox_id,
            occurred_at=source.occurred_at,
        )
    )
    assert artifact is not None
    assert artifact.projection_revision == 2
    assert artifact.custody_count == 2
    assert session.calls[-1][0] == ATOMIC_ARTIFACT_READ_SQL
    assert "'artifact.read'" in session.calls[-1][0]
    assert "artifact_outbox" in session.calls[-1][0]


@pytest.mark.parametrize(
    "change",
    [
        {"content_sha256": "not-a-hash"},
        {"request_sha256": "0" * 63},
        {"byte_count": -1},
        {"classification": "unclassified"},
        {"scan_state": "unknown"},
    ],
)
def test_persistence_batch_rejects_unbounded_or_invalid_values(
    change: dict[str, object],
) -> None:
    values = asdict(batch()) | change
    with pytest.raises((TypeError, ValueError)):
        ArtifactPersistenceBatch(**values)
