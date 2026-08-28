"""Governed Agent Run application service.

The service accepts only an injected executor. It never opens a provider
transport, changes legal-domain state, or performs an external action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sklegal_persistence.features.agent_runs.models import (
    AgentRunAttemptRecord,
    AgentRunAuditEvent,
    AgentRunRecord,
    AgentRunStatus,
    AnalysisRequestRecord,
    AuthorizationEvidence,
    BlindChallengeRecord,
    ChallengeDefectRecord,
    ChallengeOutcome,
    HumanDispositionDecision,
    HumanDispositionRecord,
    ModelRouteEvidence,
    RecommendationRecord,
    RecommendationReviewState,
    SnapshotPins,
    SourceRoleEvidence,
    ToolCallEvidence,
    canonical_sha256,
)
from sklegal_persistence.features.agent_runs.repository import (
    AgentRunIdempotencyConflict,
    AgentRunNotFound,
    AgentRunRepository,
    AgentRunVersionConflict,
)

from .contracts import AnalysisRequestBody, BlindChallengeBody, HumanDispositionBody


class AgentRunServiceError(RuntimeError):
    """Sanitized application failure with a stable API code."""

    def __init__(self, code: str, *, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class AnalysisExecutionError(RuntimeError):
    """Sanitized failure emitted by the injected public-synthetic executor."""

    def __init__(
        self,
        code: Literal[
            "analysis_cancelled",
            "analysis_timed_out",
            "logical_route_unavailable",
            "executor_unavailable",
        ],
        *,
        retryable: bool,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class ExecutorValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisExecution(ExecutorValue):
    workflow_id: str = Field(min_length=1, max_length=255)
    workflow_revision: str = Field(min_length=1, max_length=160)
    route: ModelRouteEvidence
    observed_agent_specification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_matter_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_corpus_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_authority_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_policy_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_prompt_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_scoring_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    completed_at: datetime
    tool_calls: tuple[ToolCallEvidence, ...] = ()
    recommendations: tuple[RecommendationRecord, ...] = Field(min_length=1)


class ChallengeExecution(ExecutorValue):
    challenger_route: ModelRouteEvidence
    blind_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    independent_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    saw_challenged_conclusion: Literal[False] = False
    outcome: ChallengeOutcome
    defects: tuple[ChallengeDefectRecord, ...] = ()
    created_at: datetime


class BlindChallengeMaterial(ExecutorValue):
    """Conclusion-free input supplied to the independent challenger."""

    recommendation_id: UUID
    recommendation_version: int = Field(ge=1)
    target_kind: Literal["issue", "claim", "defense", "element", "proceeding"]
    target_id: UUID
    proceeding_phase: str = Field(min_length=1, max_length=160)
    snapshots: SnapshotPins
    output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scoring_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prerequisites: tuple[str, ...] = ()
    prohibited_sequencing: tuple[str, ...] = ()
    evidence: tuple[SourceRoleEvidence, ...] = Field(min_length=1)


class AnalysisExecutor(Protocol):
    def execute(self, request: AnalysisRequestRecord) -> AnalysisExecution: ...

    def challenge(
        self,
        *,
        request: AnalysisRequestRecord,
        material: BlindChallengeMaterial,
        command: BlindChallengeBody,
    ) -> ChallengeExecution: ...


class AnalysisPinVerifier(Protocol):
    """Current-policy and route catalog check, evaluated before execution."""

    def verify_analysis(self, request: AnalysisRequestRecord) -> None: ...

    def verify_challenge(self, command: BlindChallengeBody) -> None: ...


@dataclass(frozen=True, slots=True)
class RunActor:
    tenant_id: UUID
    principal_id: UUID
    authorized_matter_id: UUID
    decision_id: UUID
    correlation_id: UUID
    capability: str
    purpose: str
    verifier_policy_version: str
    revocation_revision: str
    credential_digest: str


class StaticAnalysisPinVerifier:
    """Exact public-synthetic pin verifier for a bounded composition."""

    def __init__(
        self,
        *,
        allowed_route_ids: frozenset[str],
        current_snapshots: SnapshotPins,
        allowed_agent_specifications: frozenset[tuple[str, str]],
        current_prompt_template_id: str,
        current_prompt_template_sha256: str,
        current_output_schema_id: str,
        current_output_schema_sha256: str,
        current_scoring_policy_id: str,
        current_scoring_policy_sha256: str,
        allowed_challenger_spec_sha256: frozenset[str],
        revoked_deployment_sha256: frozenset[str] = frozenset(),
    ) -> None:
        self._allowed_routes = allowed_route_ids
        self._current_snapshots = current_snapshots
        self._allowed_agent_specifications = allowed_agent_specifications
        self._prompt_template = (
            current_prompt_template_id,
            current_prompt_template_sha256,
        )
        self._output_schema = (current_output_schema_id, current_output_schema_sha256)
        self._scoring_policy = (
            current_scoring_policy_id,
            current_scoring_policy_sha256,
        )
        self._allowed_challenger_specs = allowed_challenger_spec_sha256
        self._revoked_deployments = revoked_deployment_sha256

    def verify_analysis(self, request: AnalysisRequestRecord) -> None:
        if request.requested_logical_route_id not in self._allowed_routes:
            raise AgentRunServiceError("logical_route_unavailable", status_code=503)
        if request.snapshots != self._current_snapshots:
            raise AgentRunServiceError("stale_analysis_snapshot", status_code=409)
        if (
            request.agent_specification.spec_sha256,
            request.agent_specification.deployment_sha256,
        ) not in self._allowed_agent_specifications:
            raise AgentRunServiceError(
                "agent_specification_unavailable", status_code=409
            )
        if (
            request.prompt_template_id,
            request.prompt_template_sha256,
        ) != self._prompt_template:
            raise AgentRunServiceError("stale_prompt_template", status_code=409)
        if (
            request.output_schema_id,
            request.output_schema_sha256,
        ) != self._output_schema:
            raise AgentRunServiceError("stale_output_schema", status_code=409)
        if (
            request.scoring_policy_id,
            request.scoring_policy_sha256,
        ) != self._scoring_policy:
            raise AgentRunServiceError("stale_scoring_policy", status_code=409)
        if request.agent_specification.deployment_sha256 in self._revoked_deployments:
            raise AgentRunServiceError("deployment_revision_revoked", status_code=403)

    def verify_challenge(self, command: BlindChallengeBody) -> None:
        if command.requested_logical_route_id not in self._allowed_routes:
            raise AgentRunServiceError("logical_route_unavailable", status_code=503)
        if command.challenger_spec_sha256 not in self._allowed_challenger_specs:
            raise AgentRunServiceError(
                "challenger_specification_unavailable", status_code=409
            )


class DeterministicPublicSyntheticExecutor:
    """Explicit injected fixture executor with no network or tool side effects."""

    def __init__(
        self,
        *,
        analysis: AnalysisExecution | AnalysisExecutionError,
        challenge: ChallengeExecution | AnalysisExecutionError,
    ) -> None:
        self.analysis = analysis
        self.challenge_result = challenge
        self.analysis_calls = 0
        self.challenge_calls = 0
        self.last_challenge_material: BlindChallengeMaterial | None = None

    def execute(self, request: AnalysisRequestRecord) -> AnalysisExecution:
        del request
        self.analysis_calls += 1
        if isinstance(self.analysis, AnalysisExecutionError):
            raise self.analysis
        return self.analysis

    def challenge(
        self,
        *,
        request: AnalysisRequestRecord,
        material: BlindChallengeMaterial,
        command: BlindChallengeBody,
    ) -> ChallengeExecution:
        del request, command
        self.challenge_calls += 1
        self.last_challenge_material = material
        if isinstance(self.challenge_result, AnalysisExecutionError):
            raise self.challenge_result
        if self.challenge_result.blind_input_sha256 is None:
            return self.challenge_result.model_copy(
                update={"blind_input_sha256": canonical_sha256(material)}
            )
        return self.challenge_result


class AgentRunService:
    """Fail-closed orchestration for analysis, challenge, and disposition."""

    def __init__(
        self,
        *,
        repository: AgentRunRepository,
        executor: AnalysisExecutor,
        pins: AnalysisPinVerifier,
        clock: Callable[[], datetime],
        id_factory: Callable[[], UUID],
        workflow_id: str = "matter-analysis",
        workflow_revision: str = "sklegal.agent-run-workflow/v1",
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._pins = pins
        self._clock = clock
        self._id_factory = id_factory
        self._workflow_id = workflow_id
        self._workflow_revision = workflow_revision

    @staticmethod
    def _authorization(actor: RunActor) -> AuthorizationEvidence:
        return AuthorizationEvidence(
            decision_id=actor.decision_id,
            correlation_id=actor.correlation_id,
            principal_id=actor.principal_id,
            capability=actor.capability,
            purpose=actor.purpose,
            verifier_policy_version=actor.verifier_policy_version,
            revocation_revision=actor.revocation_revision,
            credential_digest=actor.credential_digest,
        )

    def _require_member(self, actor: RunActor, matter_id: UUID) -> None:
        if actor.authorized_matter_id != matter_id:
            raise AgentRunServiceError("authorization_scope_mismatch", status_code=403)
        try:
            member = self._repository.is_matter_member(
                actor.tenant_id, matter_id, actor.principal_id
            )
        except Exception:
            raise AgentRunServiceError(
                "agent_run_store_unavailable", status_code=503
            ) from None
        if not member:
            raise AgentRunServiceError("matter_membership_denied", status_code=403)

    @staticmethod
    def _require_operation(actor: RunActor, *, capability: str, purpose: str) -> None:
        if actor.capability != capability or actor.purpose != purpose:
            raise AgentRunServiceError("operation_capability_mismatch", status_code=403)

    @staticmethod
    def _fingerprint(*, actor: RunActor, matter_id: UUID, command: BaseModel) -> str:
        return canonical_sha256(
            {
                "tenantId": str(actor.tenant_id),
                "matterId": str(matter_id),
                "principalId": str(actor.principal_id),
                "command": command.model_dump(mode="json", by_alias=True),
            }
        )

    @staticmethod
    def _map_repository_error(exc: Exception) -> AgentRunServiceError:
        if isinstance(exc, AgentRunIdempotencyConflict):
            return AgentRunServiceError("idempotency_key_reused", status_code=409)
        if isinstance(exc, AgentRunVersionConflict):
            return AgentRunServiceError("agent_run_version_conflict", status_code=409)
        if isinstance(exc, AgentRunNotFound):
            return AgentRunServiceError("agent_run_not_found", status_code=404)
        return AgentRunServiceError("agent_run_store_unavailable", status_code=503)

    def start(
        self,
        *,
        matter_id: UUID,
        command: AnalysisRequestBody,
        idempotency_key: UUID,
        actor: RunActor,
    ) -> AgentRunRecord:
        self._require_operation(
            actor, capability="claim.propose", purpose="claim_development"
        )
        if command.purpose != actor.purpose:
            raise AgentRunServiceError("analysis_purpose_mismatch", status_code=403)
        self._require_member(actor, matter_id)
        fingerprint = self._fingerprint(
            actor=actor, matter_id=matter_id, command=command
        )
        try:
            prior = self._repository.find_idempotent(
                actor.tenant_id, matter_id, idempotency_key
            )
        except Exception:
            raise AgentRunServiceError(
                "agent_run_store_unavailable", status_code=503
            ) from None
        if prior is not None:
            if prior[0] != fingerprint:
                raise AgentRunServiceError("idempotency_key_reused", status_code=409)
            return prior[1]

        request = AnalysisRequestRecord(
            tenant_id=actor.tenant_id,
            matter_id=matter_id,
            principal_id=actor.principal_id,
            request_id=self._id_factory(),
            idempotency_key=idempotency_key,
            **command.model_dump(exclude={"schema_version"}),
        )
        self._pins.verify_analysis(request)
        attempt_number = 1
        if request.retry_of_run_id is not None:
            try:
                prior_run = self._repository.get(
                    actor.tenant_id, matter_id, request.retry_of_run_id
                )
            except Exception:
                raise AgentRunServiceError(
                    "agent_run_store_unavailable", status_code=503
                ) from None
            if prior_run is None:
                raise AgentRunServiceError("agent_run_not_found", status_code=404)
            if self._analysis_contract(prior_run.request) != self._analysis_contract(
                request
            ):
                raise AgentRunServiceError("retry_contract_mismatch", status_code=409)
            if (
                prior_run.status is AgentRunStatus.COMPLETED
                or not prior_run.attempts[-1].retryable
                or prior_run.attempts[-1].attempt_number >= 9
            ):
                raise AgentRunServiceError("agent_run_retry_denied", status_code=409)
            attempt_number = prior_run.attempts[-1].attempt_number + 1
        run_id = self._id_factory()
        attempt_id = self._id_factory()
        audit_id = self._id_factory()
        started_at = self._clock()
        try:
            execution = self._executor.execute(request)
            self._validate_execution(request, execution)
            attempt = AgentRunAttemptRecord(
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                started_at=execution.started_at,
                completed_at=execution.completed_at,
                outcome=AgentRunStatus.COMPLETED,
                retryable=False,
            )
            record = AgentRunRecord(
                run_id=run_id,
                version=1,
                tenant_id=actor.tenant_id,
                matter_id=matter_id,
                request=request,
                status=AgentRunStatus.COMPLETED,
                workflow_id=execution.workflow_id,
                workflow_revision=execution.workflow_revision,
                authorization=self._authorization(actor),
                route=execution.route,
                attempts=(attempt,),
                tool_calls=execution.tool_calls,
                recommendations=execution.recommendations,
                proposal_payload_sha256=execution.proposal_payload_sha256,
                audit_event_ids=(audit_id,),
                created_at=execution.started_at,
                updated_at=execution.completed_at,
            )
            audit_outcome: Literal["completed", "failed", "denied"] = "completed"
        except AnalysisExecutionError as exc:
            completed_at = self._clock()
            status_value = {
                "analysis_cancelled": AgentRunStatus.CANCELLED,
                "analysis_timed_out": AgentRunStatus.TIMED_OUT,
            }.get(exc.code, AgentRunStatus.FAILED)
            attempt = AgentRunAttemptRecord(
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                started_at=started_at,
                completed_at=completed_at,
                outcome=status_value,
                error_code=exc.code,
                retryable=exc.retryable,
            )
            record = AgentRunRecord(
                run_id=run_id,
                version=1,
                tenant_id=actor.tenant_id,
                matter_id=matter_id,
                request=request,
                status=status_value,
                workflow_id=self._workflow_id,
                workflow_revision=self._workflow_revision,
                authorization=self._authorization(actor),
                attempts=(attempt,),
                audit_event_ids=(audit_id,),
                created_at=started_at,
                updated_at=completed_at,
            )
            audit_outcome = "failed"
        except Exception:
            completed_at = self._clock()
            attempt = AgentRunAttemptRecord(
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                started_at=started_at,
                completed_at=completed_at,
                outcome=AgentRunStatus.FAILED,
                error_code="executor_output_invalid",
                retryable=False,
            )
            record = AgentRunRecord(
                run_id=run_id,
                version=1,
                tenant_id=actor.tenant_id,
                matter_id=matter_id,
                request=request,
                status=AgentRunStatus.FAILED,
                workflow_id=self._workflow_id,
                workflow_revision=self._workflow_revision,
                authorization=self._authorization(actor),
                attempts=(attempt,),
                audit_event_ids=(audit_id,),
                created_at=started_at,
                updated_at=completed_at,
            )
            audit_outcome = "failed"

        audit = AgentRunAuditEvent(
            event_id=audit_id,
            tenant_id=actor.tenant_id,
            matter_id=matter_id,
            run_id=run_id,
            run_version=1,
            correlation_id=actor.correlation_id,
            actor_principal_id=actor.principal_id,
            action="agent_run.recorded",
            outcome=audit_outcome,
            subject_sha256=canonical_sha256(record),
            occurred_at=record.updated_at,
        )
        try:
            return self._repository.commit_new(
                request_sha256=fingerprint, record=record, audit=audit
            )
        except Exception as exc:
            raise self._map_repository_error(exc) from None

    @staticmethod
    def _validate_execution(
        request: AnalysisRequestRecord, execution: AnalysisExecution
    ) -> None:
        observed = (
            execution.observed_agent_specification_sha256,
            execution.observed_matter_snapshot_sha256,
            execution.observed_corpus_snapshot_sha256,
            execution.observed_authority_snapshot_sha256,
            execution.observed_policy_snapshot_sha256,
            execution.observed_prompt_template_sha256,
            execution.observed_output_schema_sha256,
            execution.observed_scoring_policy_sha256,
        )
        expected = (
            request.agent_specification.spec_sha256,
            request.snapshots.matter_snapshot_sha256,
            request.snapshots.corpus_snapshot_sha256,
            request.snapshots.authority_snapshot_sha256,
            request.snapshots.policy_snapshot_sha256,
            request.prompt_template_sha256,
            request.output_schema_sha256,
            request.scoring_policy_sha256,
        )
        if observed != expected:
            raise ValueError("executor evidence does not match exact request pins")
        if execution.route.logical_route_id != request.requested_logical_route_id:
            raise ValueError("served logical route differs from request")
        if execution.completed_at < execution.started_at:
            raise ValueError("analysis completion cannot precede start")
        sequences = tuple(item.sequence for item in execution.tool_calls)
        if sequences != tuple(range(1, len(sequences) + 1)):
            raise ValueError("tool-call sequence is incomplete")

    @staticmethod
    def _analysis_contract(request: AnalysisRequestRecord) -> str:
        return canonical_sha256(
            request.model_dump(
                mode="json",
                by_alias=True,
                exclude={
                    "tenant_id",
                    "matter_id",
                    "principal_id",
                    "request_id",
                    "idempotency_key",
                    "retry_of_run_id",
                },
            )
        )

    def get(self, *, matter_id: UUID, run_id: UUID, actor: RunActor) -> AgentRunRecord:
        self._require_operation(
            actor, capability="claim.review", purpose="claim_review"
        )
        self._require_member(actor, matter_id)
        try:
            record = self._repository.get(actor.tenant_id, matter_id, run_id)
        except Exception:
            raise AgentRunServiceError(
                "agent_run_store_unavailable", status_code=503
            ) from None
        if record is None:
            raise AgentRunServiceError("agent_run_not_found", status_code=404)
        return record

    def list(self, *, matter_id: UUID, actor: RunActor) -> tuple[AgentRunRecord, ...]:
        self._require_operation(
            actor, capability="claim.review", purpose="claim_review"
        )
        self._require_member(actor, matter_id)
        try:
            return self._repository.list_for_matter(actor.tenant_id, matter_id)
        except Exception:
            raise AgentRunServiceError(
                "agent_run_store_unavailable", status_code=503
            ) from None

    def challenge(
        self,
        *,
        matter_id: UUID,
        run_id: UUID,
        command: BlindChallengeBody,
        idempotency_key: UUID,
        actor: RunActor,
    ) -> AgentRunRecord:
        self._require_operation(
            actor, capability="claim.review", purpose="claim_review"
        )
        self._require_member(actor, matter_id)
        fingerprint = self._fingerprint(
            actor=actor, matter_id=matter_id, command=command
        )
        prior = self._idempotent_mutation(
            matter_id=matter_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            actor=actor,
        )
        if prior is not None:
            return prior
        current = self._get_after_authorization(
            matter_id=matter_id, run_id=run_id, actor=actor
        )
        if current.version != command.expected_run_version:
            raise AgentRunServiceError("agent_run_version_conflict", status_code=409)
        recommendation = self._recommendation(current, command.recommendation_id)
        if recommendation.version != command.recommendation_version:
            raise AgentRunServiceError(
                "recommendation_version_conflict", status_code=409
            )
        if (
            command.challenger_spec_sha256
            == current.request.agent_specification.spec_sha256
        ):
            raise AgentRunServiceError("challenger_not_independent", status_code=409)
        self._pins.verify_challenge(command)
        material = BlindChallengeMaterial(
            recommendation_id=recommendation.recommendation_id,
            recommendation_version=recommendation.version,
            target_kind=recommendation.target_kind,
            target_id=recommendation.target_id,
            proceeding_phase=recommendation.proceeding_phase,
            snapshots=current.request.snapshots,
            output_schema_sha256=current.request.output_schema_sha256,
            scoring_policy_sha256=current.request.scoring_policy_sha256,
            prerequisites=recommendation.prerequisites,
            prohibited_sequencing=recommendation.prohibited_sequencing,
            evidence=recommendation.evidence,
        )
        blind_input_sha256 = canonical_sha256(material)
        try:
            execution = self._executor.challenge(
                request=current.request, material=material, command=command
            )
        except AnalysisExecutionError as exc:
            raise AgentRunServiceError(exc.code, status_code=503) from None
        except Exception:
            raise AgentRunServiceError(
                "challenge_executor_invalid", status_code=503
            ) from None
        if (
            execution.challenger_route.logical_route_id
            != command.requested_logical_route_id
        ):
            raise AgentRunServiceError("challenge_attribution_invalid", status_code=422)
        if execution.blind_input_sha256 != blind_input_sha256:
            raise AgentRunServiceError("challenge_attribution_invalid", status_code=422)
        if current.route is not None and (
            execution.challenger_route.served_model_name,
            execution.challenger_route.served_model_revision,
        ) == (current.route.served_model_name, current.route.served_model_revision):
            raise AgentRunServiceError("challenger_not_independent", status_code=409)
        try:
            challenge = BlindChallengeRecord(
                challenge_id=self._id_factory(),
                version=1
                + max(
                    (
                        item.version
                        for item in current.challenges
                        if item.recommendation_id == recommendation.recommendation_id
                    ),
                    default=0,
                ),
                recommendation_id=recommendation.recommendation_id,
                recommendation_version=recommendation.version,
                challenger_spec_id=command.challenger_spec_id,
                challenger_spec_version=command.challenger_spec_version,
                challenger_spec_sha256=command.challenger_spec_sha256,
                challenger_route=execution.challenger_route,
                blind_input_sha256=blind_input_sha256,
                independent_output_sha256=execution.independent_output_sha256,
                authorization=self._authorization(actor),
                saw_challenged_conclusion=execution.saw_challenged_conclusion,
                outcome=execution.outcome,
                defects=execution.defects,
                created_at=execution.created_at,
            )
        except Exception:
            raise AgentRunServiceError(
                "challenge_output_invalid", status_code=422
            ) from None
        recommendations = tuple(
            item.model_copy(
                update={"review_state": RecommendationReviewState.CHALLENGED}
            )
            if item.recommendation_id == recommendation.recommendation_id
            else item
            for item in current.recommendations
        )
        return self._append(
            current=current,
            actor=actor,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            action="agent_run.challenge_recorded",
            record=current.model_copy(
                update={
                    "version": current.version + 1,
                    "recommendations": recommendations,
                    "challenges": (*current.challenges, challenge),
                    "updated_at": execution.created_at,
                }
            ),
        )

    def dispose(
        self,
        *,
        matter_id: UUID,
        run_id: UUID,
        command: HumanDispositionBody,
        idempotency_key: UUID,
        actor: RunActor,
    ) -> AgentRunRecord:
        self._require_operation(
            actor, capability="claim.review", purpose="claim_review"
        )
        self._require_member(actor, matter_id)
        fingerprint = self._fingerprint(
            actor=actor, matter_id=matter_id, command=command
        )
        prior = self._idempotent_mutation(
            matter_id=matter_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            actor=actor,
        )
        if prior is not None:
            return prior
        current = self._get_after_authorization(
            matter_id=matter_id, run_id=run_id, actor=actor
        )
        if current.version != command.expected_run_version:
            raise AgentRunServiceError("agent_run_version_conflict", status_code=409)
        recommendation = self._recommendation(current, command.recommendation_id)
        if recommendation.version != command.recommendation_version:
            raise AgentRunServiceError(
                "recommendation_version_conflict", status_code=409
            )
        if command.policy_revision != actor.verifier_policy_version:
            raise AgentRunServiceError("stale_reviewer_policy", status_code=409)
        positive = command.decision in {
            HumanDispositionDecision.ACCEPT_AS_PROPOSED_TASK,
            HumanDispositionDecision.REQUEST_WORK_PRODUCT_PROPOSAL,
        }
        challenges = tuple(
            item
            for item in current.challenges
            if item.recommendation_id == recommendation.recommendation_id
            and item.recommendation_version == recommendation.version
        )
        if positive and (
            not challenges or challenges[-1].outcome is ChallengeOutcome.DEFECT_FOUND
        ):
            raise AgentRunServiceError(
                "independent_challenge_required", status_code=409
            )
        decided_at = self._clock()
        disposition = HumanDispositionRecord(
            disposition_id=self._id_factory(),
            version=1
            + max(
                (
                    item.version
                    for item in current.dispositions
                    if item.recommendation_id == recommendation.recommendation_id
                ),
                default=0,
            ),
            recommendation_id=recommendation.recommendation_id,
            recommendation_version=recommendation.version,
            decision=command.decision,
            reviewer_principal_id=actor.principal_id,
            rationale=command.rationale,
            policy_revision=command.policy_revision,
            capability_decision_id=actor.decision_id,
            authorization=self._authorization(actor),
            decided_at=decided_at,
        )
        recommendations = tuple(
            item.model_copy(update={"review_state": RecommendationReviewState.DISPOSED})
            if item.recommendation_id == recommendation.recommendation_id
            else item
            for item in current.recommendations
        )
        return self._append(
            current=current,
            actor=actor,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            action="agent_run.disposition_recorded",
            record=current.model_copy(
                update={
                    "version": current.version + 1,
                    "recommendations": recommendations,
                    "dispositions": (*current.dispositions, disposition),
                    "updated_at": decided_at,
                }
            ),
        )

    def _idempotent_mutation(
        self,
        *,
        matter_id: UUID,
        run_id: UUID,
        idempotency_key: UUID,
        fingerprint: str,
        actor: RunActor,
    ) -> AgentRunRecord | None:
        try:
            prior = self._repository.find_idempotent(
                actor.tenant_id, matter_id, idempotency_key
            )
        except Exception:
            raise AgentRunServiceError(
                "agent_run_store_unavailable", status_code=503
            ) from None
        if prior is None:
            return None
        if prior[0] != fingerprint or prior[1].run_id != run_id:
            raise AgentRunServiceError("idempotency_key_reused", status_code=409)
        return prior[1]

    def _get_after_authorization(
        self, *, matter_id: UUID, run_id: UUID, actor: RunActor
    ) -> AgentRunRecord:
        try:
            record = self._repository.get(actor.tenant_id, matter_id, run_id)
        except Exception:
            raise AgentRunServiceError(
                "agent_run_store_unavailable", status_code=503
            ) from None
        if record is None:
            raise AgentRunServiceError("agent_run_not_found", status_code=404)
        return record

    @staticmethod
    def _recommendation(
        current: AgentRunRecord, recommendation_id: UUID
    ) -> RecommendationRecord:
        result = next(
            (
                item
                for item in current.recommendations
                if item.recommendation_id == recommendation_id
            ),
            None,
        )
        if result is None:
            raise AgentRunServiceError("recommendation_not_found", status_code=404)
        return result

    def _append(
        self,
        *,
        current: AgentRunRecord,
        actor: RunActor,
        idempotency_key: UUID,
        fingerprint: str,
        action: Literal[
            "agent_run.challenge_recorded", "agent_run.disposition_recorded"
        ],
        record: AgentRunRecord,
    ) -> AgentRunRecord:
        audit_id = self._id_factory()
        record = AgentRunRecord.model_validate(
            record.model_copy(
                update={"audit_event_ids": (*record.audit_event_ids, audit_id)}
            ).model_dump(mode="json", by_alias=True)
        )
        audit = AgentRunAuditEvent(
            event_id=audit_id,
            tenant_id=record.tenant_id,
            matter_id=record.matter_id,
            run_id=record.run_id,
            run_version=record.version,
            correlation_id=actor.correlation_id,
            actor_principal_id=actor.principal_id,
            action=action,
            outcome="completed",
            subject_sha256=canonical_sha256(record),
            occurred_at=record.updated_at,
        )
        try:
            return self._repository.append_version(
                expected_version=current.version,
                idempotency_key=idempotency_key,
                request_sha256=fingerprint,
                record=record,
                audit=audit,
            )
        except Exception as exc:
            raise self._map_repository_error(exc) from None
