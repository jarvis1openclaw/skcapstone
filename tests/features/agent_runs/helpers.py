from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sklegal_api.features.agent_runs.contracts import (
    AnalysisRequestBody,
    BlindChallengeBody,
)
from sklegal_api.features.agent_runs.service import (
    AgentRunService,
    AnalysisExecution,
    ChallengeExecution,
    DeterministicPublicSyntheticExecutor,
    RunActor,
    StaticAnalysisPinVerifier,
)
from sklegal_persistence.features.agent_runs.models import (
    AgentRunRecord,
    ChallengeOutcome,
    SnapshotPins,
)
from sklegal_persistence.features.agent_runs.repository import (
    InMemoryAgentRunRepository,
)

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("30000000-0000-4000-8000-000000000001")
OTHER_MATTER_ID = UUID("30000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("20000000-0000-4000-8000-000000000001")
POLICY_SHA256 = "7" * 64
AUTHZ_POLICY = "sklegal-authz/v1"
NOW = datetime(2026, 8, 23, 12, 0, 2, tzinfo=UTC)


class SequentialIds:
    def __init__(self, start: int = 100) -> None:
        self.value = start

    def __call__(self) -> UUID:
        result = UUID(f"10000000-0000-4000-8000-{self.value:012d}")
        self.value += 1
        return result


def fixture_path() -> Path:
    return (
        Path(__file__).parents[2]
        / "fixtures/mvp/fragments/agent_runs/public-synthetic-agent-run-v1.json"
    )


def fixture_record() -> AgentRunRecord:
    return AgentRunRecord.model_validate_json(fixture_path().read_text())


def analysis_command() -> AnalysisRequestBody:
    request = fixture_record().request.model_dump(
        mode="json",
        exclude={
            "tenant_id",
            "matter_id",
            "principal_id",
            "request_id",
            "idempotency_key",
            "analysis_kind",
            "schema_version",
        },
    )
    request["schema_version"] = "sklegal.agent-analysis-command/v1"
    request["analysis_kind"] = "matter_analysis"
    return AnalysisRequestBody.model_validate(request)


def analysis_execution() -> AnalysisExecution:
    record = fixture_record()
    request = record.request
    assert record.route is not None
    assert record.proposal_payload_sha256 is not None
    return AnalysisExecution(
        workflow_id=record.workflow_id,
        workflow_revision=record.workflow_revision,
        route=record.route,
        observed_agent_specification_sha256=(request.agent_specification.spec_sha256),
        observed_matter_snapshot_sha256=request.snapshots.matter_snapshot_sha256,
        observed_corpus_snapshot_sha256=request.snapshots.corpus_snapshot_sha256,
        observed_authority_snapshot_sha256=(
            request.snapshots.authority_snapshot_sha256
        ),
        observed_policy_snapshot_sha256=request.snapshots.policy_snapshot_sha256,
        observed_prompt_template_sha256=request.prompt_template_sha256,
        observed_output_schema_sha256=request.output_schema_sha256,
        observed_scoring_policy_sha256=request.scoring_policy_sha256,
        proposal_payload_sha256=record.proposal_payload_sha256,
        started_at=record.created_at,
        completed_at=record.updated_at,
        tool_calls=record.tool_calls,
        recommendations=record.recommendations,
    )


def challenge_command(*, run_version: int = 1) -> BlindChallengeBody:
    record = fixture_record()
    recommendation = record.recommendations[0]
    return BlindChallengeBody(
        expected_run_version=run_version,
        recommendation_id=recommendation.recommendation_id,
        recommendation_version=recommendation.version,
        challenger_spec_id="sklegal.public-independent-challenger",
        challenger_spec_version=1,
        challenger_spec_sha256="a1" * 32,
        requested_logical_route_id="sklegal.public-independent-challenge",
    )


def challenge_execution(
    *, outcome: ChallengeOutcome = ChallengeOutcome.NO_DEFECT
) -> ChallengeExecution:
    route = fixture_record().route
    assert route is not None
    return ChallengeExecution(
        challenger_route=route.model_copy(
            update={
                "logical_route_id": "sklegal.public-independent-challenge",
                "served_model_name": "qwen3.8-public-synthetic-challenger",
                "served_model_revision": "fixture-challenge-r1",
                "gateway_request_id": "public-synthetic-challenge-request-1",
            }
        ),
        independent_output_sha256="b1" * 32,
        outcome=outcome,
        defects=(),
        created_at=NOW,
    )


def actor(
    *,
    matter_id: UUID = MATTER_ID,
    capability: str = "claim.propose",
    purpose: str = "claim_development",
) -> RunActor:
    return RunActor(
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        authorized_matter_id=matter_id,
        decision_id=UUID("10000000-0000-4000-8000-000000000050"),
        correlation_id=UUID("10000000-0000-4000-8000-000000000051"),
        capability=capability,
        purpose=purpose,
        verifier_policy_version=AUTHZ_POLICY,
        revocation_revision="a" * 64,
        credential_digest="b" * 64,
    )


def pin_verifier(
    *,
    allowed_route_ids: frozenset[str] | None = None,
    current_snapshots: SnapshotPins | None = None,
    allowed_agent_specifications: frozenset[tuple[str, str]] | None = None,
    current_prompt_template_id: str | None = None,
    current_prompt_template_sha256: str | None = None,
    current_output_schema_id: str | None = None,
    current_output_schema_sha256: str | None = None,
    current_scoring_policy_id: str | None = None,
    current_scoring_policy_sha256: str | None = None,
    allowed_challenger_spec_sha256: frozenset[str] | None = None,
    revoked_deployment_sha256: frozenset[str] = frozenset(),
) -> StaticAnalysisPinVerifier:
    command = analysis_command()
    return StaticAnalysisPinVerifier(
        allowed_route_ids=allowed_route_ids
        if allowed_route_ids is not None
        else frozenset(
            {
                "sklegal.corpus-analysis",
                "sklegal.public-independent-challenge",
            }
        ),
        current_snapshots=current_snapshots or command.snapshots,
        allowed_agent_specifications=allowed_agent_specifications
        if allowed_agent_specifications is not None
        else frozenset(
            {
                (
                    command.agent_specification.spec_sha256,
                    command.agent_specification.deployment_sha256,
                )
            }
        ),
        current_prompt_template_id=(
            current_prompt_template_id or command.prompt_template_id
        ),
        current_prompt_template_sha256=(
            current_prompt_template_sha256 or command.prompt_template_sha256
        ),
        current_output_schema_id=(current_output_schema_id or command.output_schema_id),
        current_output_schema_sha256=(
            current_output_schema_sha256 or command.output_schema_sha256
        ),
        current_scoring_policy_id=(
            current_scoring_policy_id or command.scoring_policy_id
        ),
        current_scoring_policy_sha256=(
            current_scoring_policy_sha256 or command.scoring_policy_sha256
        ),
        allowed_challenger_spec_sha256=(
            allowed_challenger_spec_sha256
            if allowed_challenger_spec_sha256 is not None
            else frozenset({"a1" * 32})
        ),
        revoked_deployment_sha256=revoked_deployment_sha256,
    )


def service_fixture(
    *,
    repository: InMemoryAgentRunRepository | None = None,
    executor: DeterministicPublicSyntheticExecutor | None = None,
) -> tuple[
    AgentRunService,
    InMemoryAgentRunRepository,
    DeterministicPublicSyntheticExecutor,
]:
    store = repository or InMemoryAgentRunRepository()
    store.set_matter_members(TENANT_ID, MATTER_ID, (PRINCIPAL_ID,))
    injected = executor or DeterministicPublicSyntheticExecutor(
        analysis=analysis_execution(),
        challenge=challenge_execution(),
    )
    service = AgentRunService(
        repository=store,
        executor=injected,
        pins=pin_verifier(),
        clock=lambda: NOW,
        id_factory=SequentialIds(),
    )
    return service, store, injected


def fixture_json() -> dict[str, object]:
    return json.loads(fixture_path().read_text())
