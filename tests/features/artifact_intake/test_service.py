from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal
from uuid import UUID

import pytest
from sklegal_api.features.artifact_intake.contracts import (
    ArtifactCorrectionCommand,
    ArtifactReviewCommand,
    ArtifactSupersessionCommand,
    OriginalArtifactInput,
)
from sklegal_api.features.artifact_intake.service import (
    ArtifactAccessContext,
    ArtifactIntakeService,
    ArtifactPolicyDecision,
    ArtifactServiceError,
    StaticArtifactPolicy,
)
from sklegal_api.features.artifact_intake.store import InMemoryArtifactStore
from sklegal_api.features.artifact_intake.synthetic import (
    ArtifactAdapter,
    ArtifactAdapterUnavailable,
    ArtifactInspection,
    ArtifactStorageReceipt,
    DerivedArtifactMaterial,
    SyntheticArtifactAdapter,
)

from tests.features.artifact_intake.factories import (
    MATTER_ID,
    OTHER_MATTER_ID,
    PRINCIPAL_ID,
    T0,
    TENANT_ID,
    command,
    original,
)


def context(
    *,
    capability: Literal["evidence.manage", "evidence.read"] = "evidence.manage",
    matter_id: UUID = MATTER_ID,
    revoked: bool = False,
) -> ArtifactAccessContext:
    return ArtifactAccessContext(
        tenant_id=TENANT_ID,
        matter_id=matter_id,
        principal_id=PRINCIPAL_ID,
        capability=capability,
        purpose="evidence_review",
        authorization_decision_id=UUID("60000000-0000-4000-8000-000000000001"),
        credential_expires_at=T0 + timedelta(hours=1),
        revoked=revoked,
    )


def build(
    *, scan_state: str = "clean", adapter: ArtifactAdapter | None = None
) -> tuple[ArtifactIntakeService, InMemoryArtifactStore, StaticArtifactPolicy]:
    store = InMemoryArtifactStore()
    policy = StaticArtifactPolicy(
        memberships={(TENANT_ID, MATTER_ID, PRINCIPAL_ID)},
        allowed_classifications={"public"},
        revision="9" * 64,
        valid_until=T0 + timedelta(minutes=30),
    )
    service = ArtifactIntakeService(
        store=store,
        policy=policy,
        adapter=adapter or SyntheticArtifactAdapter(scan_state=scan_state),
        clock=lambda: T0,
    )
    return service, store, policy


def test_intake_preserves_original_and_derived_lineage_without_response_bytes() -> None:
    adapter = SyntheticArtifactAdapter()
    service, store, _ = build(adapter=adapter)

    receipt = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-intake-001",
        command=command(derivations=("text_extraction", "ocr")),
    )

    assert receipt.artifact.artifact_kind == "original"
    assert receipt.artifact.content_sha256 == receipt.artifact.original_sha256
    assert receipt.artifact.quarantine_state == "released"
    assert receipt.artifact.scan.state == "clean"
    assert len(receipt.derived_artifacts) == 2
    derivations = [item.derivation for item in receipt.derived_artifacts]
    assert all(item is not None for item in derivations)
    assert {item.kind for item in derivations if item is not None} == {
        "text_extraction",
        "ocr",
    }
    assert all(
        item.parent_artifact_id == receipt.artifact.artifact_id
        for item in receipt.derived_artifacts
    )
    assert all(
        item.original_sha256 == receipt.artifact.original_sha256
        for item in receipt.derived_artifacts
    )
    assert len(receipt.lineage.edges) == 2
    assert len(receipt.artifact.custody) == 1
    assert len(store.audit_events) == 1
    assert len(store.outbox) == 1
    assert (
        adapter.bytes_at(receipt.artifact.storage_locator)
        == command().original.decoded_bytes()
    )
    assert all(
        adapter.bytes_at(item.storage_locator) is not None
        for item in receipt.derived_artifacts
    )
    rendered = json.dumps(receipt.model_dump(mode="json", by_alias=True))
    assert "contentBase64" not in rendered
    assert command().original.content_base64 not in rendered


def test_retry_is_idempotent_and_changed_request_conflicts() -> None:
    service, store, _ = build()
    first = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-intake-retry",
        command=command(),
    )
    replay = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-intake-retry",
        command=command(),
    )

    assert replay.replayed is True
    assert replay.artifact.artifact_id == first.artifact.artifact_id
    assert len(store.audit_events) == 1
    assert len(store.outbox) == 1

    with pytest.raises(ArtifactServiceError) as raised:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-intake-retry",
            command=command(content=b"Different public synthetic bytes."),
        )
    assert raised.value.code == "idempotency_conflict"


def test_duplicate_bytes_reuse_canonical_artifact_and_append_custody() -> None:
    service, store, _ = build()
    first = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-first",
        command=command(source_identity="synthetic-source-first"),
    )
    duplicate = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-duplicate",
        command=command(source_identity="synthetic-source-second"),
    )

    assert duplicate.duplicate is True
    assert duplicate.artifact.artifact_id == first.artifact.artifact_id
    assert len(duplicate.artifact.custody) == 2
    assert duplicate.artifact.projection_revision == 2
    assert len(store.artifact_ids(TENANT_ID, MATTER_ID)) == 2
    assert len(store.audit_events) == 2


@pytest.mark.parametrize("state", ["unsafe", "failed"])
def test_unsafe_or_failed_scan_fails_closed_without_mutation(state: str) -> None:
    service, store, _ = build(scan_state=state)

    with pytest.raises(ArtifactServiceError) as raised:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key=f"artifact-{state}",
            command=command(),
        )

    assert raised.value.code == (
        "precondition_failed" if state == "unsafe" else "dependency_unavailable"
    )
    assert store.artifact_ids(TENANT_ID, MATTER_ID) == ()
    assert store.audit_events == ()


def test_pending_scan_records_quarantine_but_runs_no_derivation() -> None:
    service, _, _ = build(scan_state="pending")

    receipt = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-pending",
        command=command(derivations=("ocr",)),
    )

    assert receipt.artifact.quarantine_state == "quarantined"
    assert receipt.artifact.extraction_state == "pending"
    assert receipt.derived_artifacts == ()


def test_authentication_and_scope_fail_before_policy_or_repository_lookup() -> None:
    service, store, policy = build()

    for denied in (
        context(capability="evidence.read"),
        context(matter_id=OTHER_MATTER_ID),
        context(revoked=True),
        context().model_copy(update={"credential_expires_at": T0}),
    ):
        with pytest.raises(ArtifactServiceError) as raised:
            service.intake(
                context=denied,
                matter_id=MATTER_ID,
                idempotency_key="artifact-denied",
                command=command(),
            )
        assert raised.value.code == "access_denied"

    assert policy.calls == ()
    assert store.lookup_count == 0


def test_policy_decision_must_bind_the_exact_operation_and_evaluation_time() -> None:
    store = InMemoryArtifactStore()

    class MismatchedPolicy:
        def authorize(self, **_kwargs: object) -> ArtifactPolicyDecision:
            return ArtifactPolicyDecision(
                decision_id=UUID("60000000-0000-4000-8000-000000000009"),
                tenant_id=TENANT_ID,
                matter_id=MATTER_ID,
                principal_id=PRINCIPAL_ID,
                operation="read",
                classification="public",
                allowed=True,
                revision="9" * 64,
                evaluated_at=T0 + timedelta(seconds=1),
                valid_until=T0 + timedelta(minutes=30),
            )

    service = ArtifactIntakeService(
        store=store,
        policy=MismatchedPolicy(),
        adapter=SyntheticArtifactAdapter(),
        clock=lambda: T0,
    )
    with pytest.raises(ArtifactServiceError) as denied:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-policy-mismatch",
            command=command(),
        )

    assert denied.value.code == "access_denied"
    assert store.lookup_count == 0


def test_policy_staleness_and_dependency_outages_precede_mutation() -> None:
    service, store, policy = build()
    policy.valid_until = T0
    with pytest.raises(ArtifactServiceError) as stale:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-stale",
            command=command(),
        )
    assert stale.value.code == "policy_unavailable"
    assert store.lookup_count == 0

    policy.valid_until = T0 + timedelta(minutes=30)
    policy.available = False
    with pytest.raises(ArtifactServiceError) as unavailable:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-policy-outage",
            command=command(),
        )
    assert unavailable.value.code == "policy_unavailable"

    policy.available = True
    store.available = False
    with pytest.raises(ArtifactServiceError) as repository:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-store-outage",
            command=command(),
        )
    assert repository.value.code == "dependency_unavailable"
    assert store.artifact_ids(TENANT_ID, MATTER_ID) == ()


def test_adapter_outage_is_sanitized_and_leaves_no_partial_records() -> None:
    class UnavailableAdapter:
        def inspect(self, *_args: object, **_kwargs: object) -> ArtifactInspection:
            raise ArtifactAdapterUnavailable("synthetic scanner unavailable")

        def preserve_original(
            self, _original: OriginalArtifactInput
        ) -> ArtifactStorageReceipt:
            raise ArtifactAdapterUnavailable("synthetic storage unavailable")

        def preserve_derived(
            self, _derived: DerivedArtifactMaterial
        ) -> ArtifactStorageReceipt:
            raise ArtifactAdapterUnavailable("synthetic storage unavailable")

    service, store, _ = build(adapter=UnavailableAdapter())
    with pytest.raises(ArtifactServiceError) as unavailable:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-adapter-outage",
            command=command(),
        )

    assert unavailable.value.code == "dependency_unavailable"
    assert store.artifact_ids(TENANT_ID, MATTER_ID) == ()
    assert store.audit_events == ()


def test_content_storage_outage_leaves_no_artifact_metadata() -> None:
    class StorageOutageAdapter(SyntheticArtifactAdapter):
        def preserve_original(
            self, _original: OriginalArtifactInput
        ) -> ArtifactStorageReceipt:
            raise ArtifactAdapterUnavailable("synthetic storage unavailable")

    service, store, _ = build(adapter=StorageOutageAdapter())
    with pytest.raises(ArtifactServiceError) as unavailable:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-storage-outage",
            command=command(),
        )

    assert unavailable.value.code == "dependency_unavailable"
    assert store.artifact_ids(TENANT_ID, MATTER_ID) == ()
    assert store.audit_events == ()

    store.available = True
    store.audit_available = False
    with pytest.raises(ArtifactServiceError) as audit:
        service.intake(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-audit-outage",
            command=command(),
        )
    assert audit.value.code == "dependency_unavailable"
    assert store.artifact_ids(TENANT_ID, MATTER_ID) == ()


def test_human_correction_creates_child_and_supersedes_only_the_derived_record() -> (
    None
):
    service, store, _ = build()
    intake = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-before-correction",
        command=command(derivations=("ocr",)),
    )
    target = intake.derived_artifacts[0]
    corrected = service.correct(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-correction",
        command=ArtifactCorrectionCommand(
            target_artifact_id=target.artifact_id,
            expected_projection_revision=target.projection_revision,
            corrected=original(
                b"Corrected public synthetic OCR text.",
                filename="synthetic-exhibit.corrected.txt",
            ),
            reason="Human reviewer corrected a synthetic OCR transcription.",
        ),
    )

    successor = corrected.artifact
    current_target = service.get(
        context=context(capability="evidence.read"),
        matter_id=MATTER_ID,
        artifact_id=target.artifact_id,
    )
    original_record = service.get(
        context=context(capability="evidence.read"),
        matter_id=MATTER_ID,
        artifact_id=intake.artifact.artifact_id,
    )
    assert successor.artifact_kind == "derived"
    assert successor.derivation is not None
    assert successor.derivation.kind == "human_correction"
    assert successor.original_sha256 == intake.artifact.original_sha256
    assert current_target.supersession is not None
    assert current_target.supersession.successor_artifact_id == successor.artifact_id
    assert original_record.content_sha256 == intake.artifact.content_sha256
    assert len(store.history(TENANT_ID, MATTER_ID, target.artifact_id)) == 2

    with pytest.raises(ArtifactServiceError) as superseded_review:
        service.review(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-review-superseded",
            command=ArtifactReviewCommand(
                artifact_id=target.artifact_id,
                expected_projection_revision=current_target.projection_revision,
                decision="accepted",
                rationale="A superseded artifact must remain superseded.",
            ),
        )
    assert superseded_review.value.code == "precondition_failed"


def test_explicit_supersession_is_version_bound_and_immutable_originals_reject() -> (
    None
):
    service, store, _ = build()
    intake = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-before-supersession",
        command=command(derivations=("ocr", "text_extraction")),
    )
    target, successor = intake.derived_artifacts
    receipt = service.supersede(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-supersession",
        command=ArtifactSupersessionCommand(
            artifact_id=target.artifact_id,
            successor_artifact_id=successor.artifact_id,
            expected_projection_revision=target.projection_revision,
            reason="Synthetic derived output was superseded after human review.",
        ),
    )
    assert receipt.artifact.review_state == "superseded"
    assert receipt.artifact.supersession is not None
    assert receipt.artifact.supersession.successor_artifact_id == successor.artifact_id
    assert store.audit_events[-1].action == "artifact.supersession.recorded"

    with pytest.raises(ArtifactServiceError) as original_denied:
        service.supersede(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-original-supersession",
            command=ArtifactSupersessionCommand(
                artifact_id=intake.artifact.artifact_id,
                successor_artifact_id=successor.artifact_id,
                expected_projection_revision=intake.artifact.projection_revision,
                reason="Original bytes are immutable.",
            ),
        )
    assert original_denied.value.code == "precondition_failed"


def test_review_is_version_bound_append_only_and_replayable() -> None:
    service, store, _ = build()
    intake = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-before-review",
        command=command(derivations=()),
    )
    reviewed = service.review(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-review",
        command=ArtifactReviewCommand(
            artifact_id=intake.artifact.artifact_id,
            expected_projection_revision=1,
            decision="accepted",
            rationale="Synthetic artifact metadata and custody are complete.",
        ),
    )

    assert reviewed.artifact.review_state == "accepted"
    assert reviewed.artifact.reviewed_by_principal_id == PRINCIPAL_ID
    assert reviewed.artifact.projection_revision == 2
    assert len(store.history(TENANT_ID, MATTER_ID, intake.artifact.artifact_id)) == 2
    replay = service.review(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-review",
        command=ArtifactReviewCommand(
            artifact_id=intake.artifact.artifact_id,
            expected_projection_revision=1,
            decision="accepted",
            rationale="Synthetic artifact metadata and custody are complete.",
        ),
    )
    assert replay.replayed is True

    with pytest.raises(ArtifactServiceError) as stale:
        service.review(
            context=context(),
            matter_id=MATTER_ID,
            idempotency_key="artifact-review-stale",
            command=ArtifactReviewCommand(
                artifact_id=intake.artifact.artifact_id,
                expected_projection_revision=1,
                decision="rejected",
                rationale="A deliberately stale synthetic decision.",
            ),
        )
    assert stale.value.code == "precondition_failed"


def test_get_authorizes_before_lookup_and_audits_success() -> None:
    service, store, _ = build()
    receipt = service.intake(
        context=context(),
        matter_id=MATTER_ID,
        idempotency_key="artifact-read-source",
        command=command(derivations=()),
    )
    before = store.lookup_count
    fetched = service.get(
        context=context(capability="evidence.read"),
        matter_id=MATTER_ID,
        artifact_id=receipt.artifact.artifact_id,
    )
    assert fetched.artifact_id == receipt.artifact.artifact_id
    assert store.lookup_count > before
    assert store.audit_events[-1].action == "artifact.read"

    denied_before = store.lookup_count
    with pytest.raises(ArtifactServiceError) as denied:
        service.get(
            context=context(capability="evidence.read", matter_id=OTHER_MATTER_ID),
            matter_id=MATTER_ID,
            artifact_id=receipt.artifact.artifact_id,
        )
    assert denied.value.code == "access_denied"
    assert store.lookup_count == denied_before

    with pytest.raises(ArtifactServiceError) as missing:
        service.get(
            context=context(capability="evidence.read"),
            matter_id=MATTER_ID,
            artifact_id=UUID("70000000-0000-4000-8000-000000000099"),
        )
    assert missing.value.code == "resource_unavailable"
