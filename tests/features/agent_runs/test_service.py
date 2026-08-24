from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from sklegal_api.features.agent_runs.contracts import HumanDispositionBody
from sklegal_api.features.agent_runs.service import (
    AgentRunService,
    AgentRunServiceError,
    AnalysisExecutionError,
    DeterministicPublicSyntheticExecutor,
)
from sklegal_persistence.features.agent_runs.models import (
    AgentRunStatus,
    ChallengeDefectRecord,
    ChallengeOutcome,
    HumanDispositionDecision,
)
from sklegal_persistence.features.agent_runs.repository import (
    InMemoryAgentRunRepository,
)

from tests.features.agent_runs.helpers import (
    AUTHZ_POLICY,
    MATTER_ID,
    NOW,
    OTHER_MATTER_ID,
    POLICY_SHA256,
    PRINCIPAL_ID,
    TENANT_ID,
    SequentialIds,
    actor,
    analysis_command,
    analysis_execution,
    challenge_command,
    challenge_execution,
    pin_verifier,
    service_fixture,
)

START_KEY = UUID("10000000-0000-4000-8000-000000000080")
CHALLENGE_KEY = UUID("10000000-0000-4000-8000-000000000081")
DISPOSITION_KEY = UUID("10000000-0000-4000-8000-000000000082")


def reviewer():  # type: ignore[no-untyped-def]
    return actor(capability="claim.review", purpose="claim_review")


def reader():  # type: ignore[no-untyped-def]
    return actor(capability="matter.read", purpose="matter_management")


def start_record():  # type: ignore[no-untyped-def]
    service, store, executor = service_fixture()
    record = service.start(
        matter_id=MATTER_ID,
        command=analysis_command(),
        idempotency_key=START_KEY,
        actor=actor(),
    )
    return service, store, executor, record


def test_start_persists_exact_pins_route_tools_recommendations_and_audit() -> None:
    service, store, executor, record = start_record()
    assert service is not None
    assert record.status is AgentRunStatus.COMPLETED
    assert record.version == 1
    assert record.request.classification == "public"
    assert record.request.public_synthetic is True
    assert record.request.snapshots.policy_snapshot_sha256 == POLICY_SHA256
    assert record.route is not None
    assert record.route.logical_route_id == "sklegal.corpus-analysis"
    assert record.tool_calls[0].tool_id == "matter.snapshot.read"
    assert record.recommendations[0].downstream_human_gate
    assert len(store.audit_events()) == 1
    assert executor.analysis_calls == 1


def test_start_is_idempotent_and_byte_drift_is_denied_without_reexecution() -> None:
    service, _, executor, first = start_record()
    replay = service.start(
        matter_id=MATTER_ID,
        command=analysis_command(),
        idempotency_key=START_KEY,
        actor=actor(),
    )
    assert replay == first
    assert executor.analysis_calls == 1
    changed = analysis_command().model_copy(
        update={"analysis_kind": "recommendation_refresh"}
    )
    with pytest.raises(AgentRunServiceError, match="idempotency_key_reused"):
        service.start(
            matter_id=MATTER_ID,
            command=changed,
            idempotency_key=START_KEY,
            actor=actor(),
        )
    assert executor.analysis_calls == 1


def test_scope_membership_and_operation_capability_fail_closed() -> None:
    service, _, _, _ = start_record()
    with pytest.raises(AgentRunServiceError, match="authorization_scope_mismatch"):
        service.start(
            matter_id=MATTER_ID,
            command=analysis_command(),
            idempotency_key=UUID("10000000-0000-4000-8000-000000000083"),
            actor=actor(matter_id=OTHER_MATTER_ID),
        )
    with pytest.raises(AgentRunServiceError, match="operation_capability_mismatch"):
        service.list(matter_id=MATTER_ID, actor=actor())
    with pytest.raises(AgentRunServiceError, match="analysis_purpose_mismatch"):
        service.start(
            matter_id=MATTER_ID,
            command=analysis_command().model_copy(update={"purpose": "other-purpose"}),
            idempotency_key=UUID("10000000-0000-4000-8000-000000000089"),
            actor=actor(),
        )

    store = InMemoryAgentRunRepository()
    executor = DeterministicPublicSyntheticExecutor(
        analysis=analysis_execution(), challenge=challenge_execution()
    )
    denied = AgentRunService(
        repository=store,
        executor=executor,
        pins=pin_verifier(),
        clock=lambda: NOW,
        id_factory=SequentialIds(),
    )
    with pytest.raises(AgentRunServiceError, match="matter_membership_denied"):
        denied.start(
            matter_id=MATTER_ID,
            command=analysis_command(),
            idempotency_key=START_KEY,
            actor=actor(),
        )


@pytest.mark.parametrize(
    ("code", "expected_status"),
    (
        ("analysis_timed_out", AgentRunStatus.TIMED_OUT),
        ("analysis_cancelled", AgentRunStatus.CANCELLED),
        ("executor_unavailable", AgentRunStatus.FAILED),
    ),
)
def test_executor_failures_are_sanitized_and_durable(
    code: str, expected_status: AgentRunStatus
) -> None:
    execution_error = AnalysisExecutionError(code, retryable=True)  # type: ignore[arg-type]
    executor = DeterministicPublicSyntheticExecutor(
        analysis=execution_error,
        challenge=challenge_execution(),
    )
    service, store, _ = service_fixture(executor=executor)
    record = service.start(
        matter_id=MATTER_ID,
        command=analysis_command(),
        idempotency_key=START_KEY,
        actor=actor(),
    )
    assert record.status is expected_status
    assert record.attempts[0].error_code == code
    assert record.attempts[0].retryable is True
    assert record.route is None
    assert record.recommendations == ()
    assert store.audit_events()[0].outcome == "failed"


def test_retry_links_failed_run_and_increments_attributable_attempt_number() -> None:
    executor = DeterministicPublicSyntheticExecutor(
        analysis=AnalysisExecutionError("analysis_timed_out", retryable=True),
        challenge=challenge_execution(),
    )
    service, store, _ = service_fixture(executor=executor)
    failed = service.start(
        matter_id=MATTER_ID,
        command=analysis_command(),
        idempotency_key=START_KEY,
        actor=actor(),
    )
    executor.analysis = analysis_execution()
    retried = service.start(
        matter_id=MATTER_ID,
        command=analysis_command().model_copy(
            update={"retry_of_run_id": failed.run_id}
        ),
        idempotency_key=UUID("10000000-0000-4000-8000-000000000084"),
        actor=actor(),
    )
    assert retried.request.retry_of_run_id == failed.run_id
    assert retried.run_id != failed.run_id
    assert retried.attempts[0].attempt_number == 2
    assert len(store.list_for_matter(TENANT_ID, MATTER_ID)) == 2


def test_retry_contract_drift_is_denied_without_reexecution() -> None:
    executor = DeterministicPublicSyntheticExecutor(
        analysis=AnalysisExecutionError("analysis_timed_out", retryable=True),
        challenge=challenge_execution(),
    )
    service, store, _ = service_fixture(executor=executor)
    failed = service.start(
        matter_id=MATTER_ID,
        command=analysis_command(),
        idempotency_key=START_KEY,
        actor=actor(),
    )
    executor.analysis = analysis_execution()
    drifted = analysis_command().model_copy(
        update={
            "analysis_kind": "recommendation_refresh",
            "retry_of_run_id": failed.run_id,
        }
    )
    with pytest.raises(AgentRunServiceError, match="retry_contract_mismatch"):
        service.start(
            matter_id=MATTER_ID,
            command=drifted,
            idempotency_key=UUID("10000000-0000-4000-8000-000000000088"),
            actor=actor(),
        )
    assert executor.analysis_calls == 1
    assert len(store.list_for_matter(TENANT_ID, MATTER_ID)) == 1


def test_stale_snapshot_unknown_route_and_revoked_deployment_prevent_execution() -> (
    None
):
    command = analysis_command()
    snapshot_fields = (
        "matter_snapshot_sha256",
        "corpus_snapshot_sha256",
        "authority_snapshot_sha256",
        "policy_snapshot_sha256",
    )
    cases = [
        (
            pin_verifier(
                current_snapshots=command.snapshots.model_copy(update={field: "0" * 64})
            ),
            "stale_analysis_snapshot",
        )
        for field in snapshot_fields
    ]
    cases.extend(
        (
            (pin_verifier(allowed_route_ids=frozenset()), "logical_route_unavailable"),
            (
                pin_verifier(
                    revoked_deployment_sha256=frozenset(
                        {command.agent_specification.deployment_sha256}
                    ),
                ),
                "deployment_revision_revoked",
            ),
            (
                pin_verifier(allowed_agent_specifications=frozenset()),
                "agent_specification_unavailable",
            ),
            (
                pin_verifier(current_prompt_template_id="stale-prompt-template"),
                "stale_prompt_template",
            ),
            (
                pin_verifier(current_prompt_template_sha256="0" * 64),
                "stale_prompt_template",
            ),
            (
                pin_verifier(current_output_schema_id="stale-output-schema"),
                "stale_output_schema",
            ),
            (
                pin_verifier(current_output_schema_sha256="0" * 64),
                "stale_output_schema",
            ),
            (
                pin_verifier(current_scoring_policy_id="stale-scoring-policy"),
                "stale_scoring_policy",
            ),
            (
                pin_verifier(current_scoring_policy_sha256="0" * 64),
                "stale_scoring_policy",
            ),
        )
    )
    for pins, expected in cases:
        store = InMemoryAgentRunRepository()
        store.set_matter_members(TENANT_ID, MATTER_ID, (PRINCIPAL_ID,))
        executor = DeterministicPublicSyntheticExecutor(
            analysis=analysis_execution(), challenge=challenge_execution()
        )
        service = AgentRunService(
            repository=store,
            executor=executor,
            pins=pins,
            clock=lambda: NOW,
            id_factory=SequentialIds(),
        )
        with pytest.raises(AgentRunServiceError, match=expected):
            service.start(
                matter_id=MATTER_ID,
                command=command,
                idempotency_key=START_KEY,
                actor=actor(),
            )
        assert executor.analysis_calls == 0
        assert store.list_for_matter(TENANT_ID, MATTER_ID) == ()


def test_incomplete_executor_attribution_records_a_failed_attempt() -> None:
    malformed = analysis_execution().model_copy(
        update={"observed_prompt_template_sha256": "f" * 64}
    )
    executor = DeterministicPublicSyntheticExecutor(
        analysis=malformed, challenge=challenge_execution()
    )
    service, store, _ = service_fixture(executor=executor)
    record = service.start(
        matter_id=MATTER_ID,
        command=analysis_command(),
        idempotency_key=START_KEY,
        actor=actor(),
    )
    assert record.status is AgentRunStatus.FAILED
    assert record.attempts[0].error_code == "executor_output_invalid"
    assert len(store.audit_events()) == 1


def test_atomic_audit_outage_leaves_no_record_or_receipt() -> None:
    store = InMemoryAgentRunRepository(audit_available=False)
    service, _, _ = service_fixture(repository=store)
    with pytest.raises(AgentRunServiceError, match="agent_run_store_unavailable"):
        service.start(
            matter_id=MATTER_ID,
            command=analysis_command(),
            idempotency_key=START_KEY,
            actor=actor(),
        )
    store.audit_available = True
    assert store.list_for_matter(TENANT_ID, MATTER_ID) == ()
    assert store.find_idempotent(TENANT_ID, MATTER_ID, START_KEY) is None


def test_independent_no_defect_challenge_then_human_disposition_is_append_only() -> (
    None
):
    service, store, executor, initial = start_record()
    challenged = service.challenge(
        matter_id=MATTER_ID,
        run_id=initial.run_id,
        command=challenge_command(),
        idempotency_key=CHALLENGE_KEY,
        actor=reviewer(),
    )
    assert challenged.version == 2
    assert challenged.challenges[0].outcome is ChallengeOutcome.NO_DEFECT
    assert challenged.challenges[0].saw_challenged_conclusion is False
    assert challenged.challenges[0].authorization.capability == "claim.review"
    assert challenged.challenges[0].authorization.revocation_revision == "a" * 64
    material = executor.last_challenge_material
    assert material is not None
    material_bytes = material.model_dump_json()
    assert initial.recommendations[0].reason not in material_bytes
    assert "proposed_output" not in material_bytes
    assert "score_total" not in material_bytes
    assert challenged.challenges[0].blind_input_sha256
    assert challenged.challenges[0].independent_output_sha256 == "b1" * 32
    disposition = HumanDispositionBody(
        expected_run_version=2,
        recommendation_id=initial.recommendations[0].recommendation_id,
        recommendation_version=1,
        decision=HumanDispositionDecision.ACCEPT_AS_PROPOSED_TASK,
        rationale="Public-synthetic review completed.",
        policy_revision=AUTHZ_POLICY,
    )
    disposed = service.dispose(
        matter_id=MATTER_ID,
        run_id=initial.run_id,
        command=disposition,
        idempotency_key=DISPOSITION_KEY,
        actor=reviewer(),
    )
    assert disposed.version == 3
    assert disposed.dispositions[0].creates_domain_record is False
    assert disposed.dispositions[0].external_effect is False
    assert disposed.dispositions[0].authorization.purpose == "claim_review"
    assert [
        item.version for item in store.history(TENANT_ID, MATTER_ID, initial.run_id)
    ] == [
        1,
        2,
        3,
    ]
    assert len(store.audit_events()) == 3


def test_challenge_defect_blocks_positive_disposition_but_preserves_defect() -> None:
    service, store, executor, initial = start_record()
    executor.challenge_result = challenge_execution().model_copy(
        update={
            "outcome": ChallengeOutcome.DEFECT_FOUND,
            "defects": (
                ChallengeDefectRecord(
                    defect_kind="unsupported_inference",
                    description="The conclusion exceeds the exact cited source span.",
                    evidence_sha256="e1" * 32,
                ),
            ),
        }
    )
    challenged = service.challenge(
        matter_id=MATTER_ID,
        run_id=initial.run_id,
        command=challenge_command(),
        idempotency_key=CHALLENGE_KEY,
        actor=reviewer(),
    )
    assert challenged.challenges[0].defects[0].defect_kind == "unsupported_inference"
    disposition = HumanDispositionBody(
        expected_run_version=2,
        recommendation_id=initial.recommendations[0].recommendation_id,
        recommendation_version=1,
        decision="request_work_product_proposal",
        rationale="Attempted positive review.",
        policy_revision=AUTHZ_POLICY,
    )
    with pytest.raises(AgentRunServiceError, match="independent_challenge_required"):
        service.dispose(
            matter_id=MATTER_ID,
            run_id=initial.run_id,
            command=disposition,
            idempotency_key=DISPOSITION_KEY,
            actor=reviewer(),
        )
    assert len(store.history(TENANT_ID, MATTER_ID, initial.run_id)) == 2


def test_stale_versions_same_model_challenger_and_stale_reviewer_policy_are_denied() -> (
    None
):
    service, _, executor, initial = start_record()
    with pytest.raises(AgentRunServiceError, match="agent_run_version_conflict"):
        service.challenge(
            matter_id=MATTER_ID,
            run_id=initial.run_id,
            command=challenge_command(run_version=9),
            idempotency_key=CHALLENGE_KEY,
            actor=reviewer(),
        )
    with pytest.raises(
        AgentRunServiceError, match="challenger_specification_unavailable"
    ):
        service.challenge(
            matter_id=MATTER_ID,
            run_id=initial.run_id,
            command=challenge_command().model_copy(
                update={"challenger_spec_sha256": "c1" * 32}
            ),
            idempotency_key=UUID("10000000-0000-4000-8000-000000000086"),
            actor=reviewer(),
        )
    assert executor.challenge_calls == 0
    assert initial.route is not None
    executor.challenge_result = challenge_execution().model_copy(
        update={"blind_input_sha256": "0" * 64}
    )
    with pytest.raises(AgentRunServiceError, match="challenge_attribution_invalid"):
        service.challenge(
            matter_id=MATTER_ID,
            run_id=initial.run_id,
            command=challenge_command(),
            idempotency_key=UUID("10000000-0000-4000-8000-000000000087"),
            actor=reviewer(),
        )
    executor.challenge_result = challenge_execution().model_copy(
        update={
            "challenger_route": challenge_execution().challenger_route.model_copy(
                update={
                    "served_model_name": initial.route.served_model_name,
                    "served_model_revision": initial.route.served_model_revision,
                }
            )
        }
    )
    with pytest.raises(AgentRunServiceError, match="challenger_not_independent"):
        service.challenge(
            matter_id=MATTER_ID,
            run_id=initial.run_id,
            command=challenge_command(),
            idempotency_key=UUID("10000000-0000-4000-8000-000000000085"),
            actor=reviewer(),
        )
    reject = HumanDispositionBody(
        expected_run_version=1,
        recommendation_id=initial.recommendations[0].recommendation_id,
        recommendation_version=1,
        decision="reject",
        rationale="Review policy pin is stale.",
        policy_revision="stale-policy",
    )
    with pytest.raises(AgentRunServiceError, match="stale_reviewer_policy"):
        service.dispose(
            matter_id=MATTER_ID,
            run_id=initial.run_id,
            command=reject,
            idempotency_key=DISPOSITION_KEY,
            actor=reviewer(),
        )


def test_read_requires_read_capability_and_returns_scoped_record() -> None:
    service, _, _, initial = start_record()
    assert (
        service.get(matter_id=MATTER_ID, run_id=initial.run_id, actor=reader())
        == initial
    )
    assert service.list(matter_id=MATTER_ID, actor=reader()) == (initial,)
    wrong_scope_reader = replace(reader(), authorized_matter_id=OTHER_MATTER_ID)
    with pytest.raises(AgentRunServiceError, match="authorization_scope_mismatch"):
        service.list(matter_id=MATTER_ID, actor=wrong_scope_reader)
