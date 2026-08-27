"""Reviewed V2 feature routers backed by the durable PostgreSQL boundaries."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from fastapi import APIRouter
from psycopg.types.json import Jsonb
from sklegal_capauth import (
    VERIFIER_POLICY_VERSION,
    Capability,
    CapabilityAuthorizer,
    Purpose,
)
from sklegal_persistence.features.agent_runs.models import (
    AgentRunRecord,
    ChallengeOutcome,
)
from sklegal_persistence.features.agent_runs.postgres import (
    PostgresAgentRunRepository,
)
from sklegal_persistence.features.artifact_intake import PostgresArtifactRepository
from sklegal_persistence.features.governed_corpus import (
    Classification,
    PostgresGovernedCorpusCoreRepository,
    PostgresGovernedCorpusRetrievalRepository,
)
from sklegal_persistence.features.joined_analysis import (
    PostgresJoinedAnalysisRepository,
)
from sklegal_persistence.features.matter_activity import (
    PostgresMatterActivityRepository,
)
from sklegal_persistence.features.task_deadlines.postgres import (
    PostgresTaskDeadlineRepository,
)
from sklegal_persistence.features.work_products.models import (
    ApprovalStatus,
    ValidationOutcome,
    WorkProductAggregate,
    WorkProductStatus,
    canonical_sha256,
)
from sklegal_persistence.features.work_products.postgres import (
    PostgresWorkProductRepository,
)

from .capauth import PrincipalResolver, ScopeResolver
from .durable_public_synthetic import (
    MATTER_ID,
    PRINCIPAL_ID,
    TENANT_ID,
    PostgresBoundary,
)
from .features.agent_runs.router import build_agent_runs_router
from .features.agent_runs.service import (
    AgentRunService,
    AnalysisExecution,
    ChallengeExecution,
    DeterministicPublicSyntheticExecutor,
    StaticAnalysisPinVerifier,
)
from .features.artifact_intake.contracts import DerivationRequest, OriginalArtifactInput
from .features.artifact_intake.postgres_store import PostgresArtifactStore
from .features.artifact_intake.router import build_artifact_intake_router
from .features.artifact_intake.service import (
    ArtifactAccessContext,
    ArtifactIntakeService,
    ArtifactPolicyDecision,
    ArtifactPolicyUnavailable,
)
from .features.artifact_intake.synthetic import (
    ArtifactAdapterUnavailable,
    ArtifactInspection,
    ArtifactStorageReceipt,
    DerivedArtifactMaterial,
)
from .features.governed_corpus.router import build_governed_corpus_router
from .features.governed_corpus.service import (
    GovernedCorpusAccessContext,
    GovernedCorpusPolicyDecision,
    GovernedCorpusService,
)
from .features.joined_analysis import (
    PostgresJoinedAnalysisStore,
    build_joined_analysis_router,
)
from .features.matter_activity.router import build_matter_activity_router
from .features.matter_activity.service import (
    ActivityAccessContext,
    ActivityPolicyDecision,
    ActivityPolicyUnavailable,
    MatterActivityService,
)
from .features.task_deadlines.router import build_task_deadline_router
from .features.task_deadlines.service import (
    TaskDeadlineAccessContext,
    TaskDeadlinePolicyDecision,
    TaskDeadlinePolicyUnavailable,
    TaskDeadlineService,
)
from .features.work_products.router import build_work_products_router
from .features.work_products.service import WorkProductService

T = TypeVar("T")
ROOT = Path(__file__).resolve().parents[4]
AGENT_RUN_FIXTURE = (
    ROOT / "tests/fixtures/mvp/fragments/agent_runs/public-synthetic-agent-run-v1.json"
)
RIGHTS_REVISION = hashlib.sha256(b"sklegal-public-synthetic-rights-v1").hexdigest()


class PsycopgTransaction:
    """Adapt one psycopg transaction to the reviewed repository protocols."""

    def __init__(self, connection: psycopg.Connection[Mapping[str, Any]]) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self._connection.execute(sql, parameters)

    def fetch_one(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Mapping[str, object] | None:
        return self._connection.execute(sql, parameters).fetchone()

    def fetch_all(
        self, sql: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        return tuple(self._connection.execute(sql, parameters).fetchall())


class PsycopgSession:
    """Adapt named-parameter repository SQL to one psycopg transaction."""

    def __init__(self, connection: psycopg.Connection[Mapping[str, Any]]) -> None:
        self._connection = connection

    def execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        adapted = {
            key: Jsonb(value)
            if isinstance(value, dict)
            or key in {"derived", "proposed_links"}
            or (
                isinstance(value, list)
                and any(isinstance(item, dict) for item in value)
            )
            else list(value)
            if isinstance(value, tuple)
            else value
            for key, value in parameters.items()
        }
        return list(self._connection.execute(statement, adapted).fetchall())


class PostgresArtifactAdapter:
    """Store public-synthetic artifact bytes immutably in scoped PostgreSQL."""

    filesystem_access = False
    provider_access = False
    signature_revision = hashlib.sha256(b"sklegal-synthetic-scan-v1").hexdigest()

    def __init__(self, boundary: PostgresBoundary) -> None:
        self._boundary = boundary

    def _preserve(self, content: bytes, content_sha256: str) -> ArtifactStorageReceipt:
        if hashlib.sha256(content).hexdigest() != content_sha256:
            raise ArtifactAdapterUnavailable("artifact content hash mismatch")
        try:
            with self._boundary.transaction(TENANT_ID, MATTER_ID) as connection:
                connection.execute(
                    """INSERT INTO sklegal_mvp.artifact_objects
                           (tenant_id, matter_id, content_sha256, content, byte_count)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, matter_id, content_sha256)
                       DO NOTHING""",
                    (TENANT_ID, MATTER_ID, content_sha256, content, len(content)),
                )
                row = connection.execute(
                    """SELECT content, byte_count
                       FROM sklegal_mvp.artifact_objects
                       WHERE tenant_id = %s AND matter_id = %s
                         AND content_sha256 = %s""",
                    (TENANT_ID, MATTER_ID, content_sha256),
                ).fetchone()
        except Exception:
            raise ArtifactAdapterUnavailable("artifact storage unavailable") from None
        if (
            row is None
            or bytes(cast(bytes, row["content"])) != content
            or int(cast(int, row["byte_count"])) != len(content)
        ):
            raise ArtifactAdapterUnavailable("immutable artifact object conflict")
        return ArtifactStorageReceipt(
            storage_locator=f"synthetic:sha256:{content_sha256}",
            content_sha256=content_sha256,
            byte_count=len(content),
        )

    def preserve_original(
        self, original: OriginalArtifactInput
    ) -> ArtifactStorageReceipt:
        return self._preserve(original.decoded_bytes(), original.content_sha256)

    def preserve_derived(
        self, derived: DerivedArtifactMaterial
    ) -> ArtifactStorageReceipt:
        return self._preserve(derived.content, derived.content_sha256)

    def inspect(
        self,
        original: OriginalArtifactInput,
        derivations: tuple[DerivationRequest, ...],
    ) -> ArtifactInspection:
        outputs: list[DerivedArtifactMaterial] = []
        for request in derivations:
            content = (
                "PUBLIC SYNTHETIC DERIVATION\n"
                f"kind={request.kind}\n"
                f"source_sha256={original.content_sha256}\n"
            ).encode()
            outputs.append(
                DerivedArtifactMaterial(
                    kind=request.kind,
                    filename_suffix=f".{request.kind}.txt",
                    media_type=request.output_media_type,
                    content=content,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    tool_name=request.tool_name,
                    tool_version=request.tool_version,
                )
            )
        return ArtifactInspection(
            scan_state="clean",
            scanner_name="synthetic-scanner",
            scanner_version="1.0.0",
            signature_revision=self.signature_revision,
            derived=tuple(outputs),
        )


class DurablePolicyState:
    """Read the current policy revision and Matter membership from PostgreSQL."""

    def __init__(self, boundary: PostgresBoundary) -> None:
        self._boundary = boundary

    def decision(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> tuple[str, bool]:
        row = self._boundary.one(
            tenant_id,
            """SELECT policy.revision, policy.stale,
                      EXISTS (
                        SELECT 1 FROM sklegal_mvp.matter_memberships AS membership
                        WHERE membership.tenant_id = %s
                          AND membership.matter_id = %s
                          AND membership.principal_id = %s
                      ) AS member
               FROM sklegal_mvp.policy_state AS policy
               WHERE policy.tenant_id = %s""",
            (tenant_id, matter_id, principal_id, tenant_id),
            matter_id=matter_id,
        )
        if row is None or row["stale"] is not False:
            raise RuntimeError("current policy is unavailable")
        revision = str(row["revision"])
        if len(revision) != 64 or any(
            value not in "0123456789abcdef" for value in revision
        ):
            raise RuntimeError("current policy revision is invalid")
        return revision, bool(row["member"])


def _policy_id(kind: str, revision: str, *parts: object) -> UUID:
    return uuid5(
        NAMESPACE_URL, ":".join((kind, revision, *(str(part) for part in parts)))
    )


class DurableArtifactPolicy:
    def __init__(self, state: DurablePolicyState) -> None:
        self._state = state

    def authorize(
        self,
        *,
        context: ArtifactAccessContext,
        operation: Any,
        classification: str,
        now: datetime,
    ) -> ArtifactPolicyDecision:
        try:
            revision, member = self._state.decision(
                context.tenant_id, context.matter_id, context.principal_id
            )
        except RuntimeError:
            raise ArtifactPolicyUnavailable("artifact policy unavailable") from None
        return ArtifactPolicyDecision(
            decision_id=_policy_id(
                "artifact-policy",
                revision,
                context.tenant_id,
                context.matter_id,
                context.principal_id,
                operation,
                classification,
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            classification=classification,
            allowed=member and classification == "public",
            revision=revision,
            evaluated_at=now,
            valid_until=now + timedelta(minutes=5),
        )


class DurableTaskDeadlinePolicy:
    def __init__(self, state: DurablePolicyState) -> None:
        self._state = state

    def authorize(
        self,
        *,
        context: TaskDeadlineAccessContext,
        operation: Any,
        capability: Capability,
        purpose: Purpose,
        now: datetime,
    ) -> TaskDeadlinePolicyDecision:
        try:
            revision, member = self._state.decision(
                context.tenant_id, context.matter_id, context.principal_id
            )
        except RuntimeError:
            raise TaskDeadlinePolicyUnavailable("task policy unavailable") from None
        allowed_pair = (capability, purpose) in {
            (Capability.MATTER_READ, Purpose.MATTER_MANAGEMENT),
            (Capability.MATTER_MANAGE, Purpose.MATTER_MANAGEMENT),
            (Capability.ACTION_EMAIL_PREPARE, Purpose.EXTERNAL_ACTION_PREPARATION),
        }
        return TaskDeadlinePolicyDecision(
            decision_id=_policy_id(
                "task-policy",
                revision,
                context.tenant_id,
                context.matter_id,
                context.principal_id,
                operation,
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            capability=capability,
            purpose=purpose,
            allowed=member and allowed_pair,
            revision=revision,
            evaluated_at=now,
            valid_until=now + timedelta(minutes=5),
        )


class DurableActivityPolicy:
    def __init__(self, state: DurablePolicyState) -> None:
        self._state = state

    def authorize(
        self,
        *,
        context: ActivityAccessContext,
        operation: Any,
        now: datetime,
    ) -> ActivityPolicyDecision:
        try:
            revision, member = self._state.decision(
                context.tenant_id, context.matter_id, context.principal_id
            )
        except RuntimeError:
            raise ActivityPolicyUnavailable("activity policy unavailable") from None
        return ActivityPolicyDecision(
            decision_id=_policy_id(
                "activity-policy",
                revision,
                context.tenant_id,
                context.matter_id,
                context.principal_id,
                operation,
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            allowed=member,
            revision=revision,
            evaluated_at=now,
            valid_until=now + timedelta(minutes=5),
        )


class DurableGovernedCorpusPolicy:
    def __init__(self, state: DurablePolicyState) -> None:
        self._state = state

    def authorize(
        self,
        *,
        context: GovernedCorpusAccessContext,
        operation: Any,
        capability: Capability,
        purpose: Purpose,
        now: datetime,
    ) -> GovernedCorpusPolicyDecision:
        revision, member = self._state.decision(
            context.tenant_id, context.matter_id, context.principal_id
        )
        allowed_pair = (capability, purpose) in {
            (Capability.CORPUS_SEARCH, Purpose.LEGAL_RESEARCH),
            (Capability.CORPUS_ARTIFACT_READ, Purpose.LEGAL_RESEARCH),
        }
        return GovernedCorpusPolicyDecision(
            decision_id=_policy_id(
                "corpus-policy",
                revision,
                context.tenant_id,
                context.matter_id,
                context.principal_id,
                operation,
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            capability=capability,
            purpose=purpose,
            allowed=member and allowed_pair,
            classification_ceiling=Classification.PUBLIC,
            rights_revision=RIGHTS_REVISION,
            revision=revision,
            evaluated_at=now,
            valid_until=now + timedelta(minutes=5),
        )


class DurableWorkProductRepository(PostgresWorkProductRepository):
    """Use the durable policy watermark before every Work Product mutation."""

    def __init__(
        self, transaction: Callable[[Callable[[Any], T]], T], state: DurablePolicyState
    ) -> None:
        super().__init__(
            transaction=transaction,
            current_policy_revision=VERIFIER_POLICY_VERSION,
        )
        self._state = state

    def current_policy_revision(self, tenant_id: UUID, matter_id: UUID) -> str:
        self._state.decision(tenant_id, matter_id, PRINCIPAL_ID)
        return VERIFIER_POLICY_VERSION


class PostgresApprovalGateVerifier:
    """Verify the exact current Approval from the durable Work Product aggregate."""

    def __init__(
        self,
        transaction: Callable[[Callable[[Any], T]], T],
        state: DurablePolicyState,
    ) -> None:
        self._repository = DurableWorkProductRepository(transaction, state)

    def verify(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        task_id: UUID,
        deadline_id: UUID | None,
        work_product_id: UUID,
        work_product_version_id: UUID,
        work_product_version_number: int,
        work_product_content_sha256: str,
        approval_id: UUID,
        approval_snapshot_sha256: str,
        action_kind: Literal["email"],
        destination_sha256: str,
    ) -> bool:
        del task_id, deadline_id, destination_sha256
        if action_kind != "email":
            return False
        try:
            self._repository.current_policy_revision(tenant_id, matter_id)
            aggregate = self._repository.get(tenant_id, matter_id, work_product_id)
            if aggregate is None:
                return False
            aggregate = WorkProductAggregate.model_validate(aggregate)
            version = aggregate.current_version
            approval = next(
                (
                    item
                    for item in aggregate.approvals
                    if item.approval_id == approval_id
                ),
                None,
            )
            if approval is None:
                return False
            validation = next(
                (
                    item
                    for item in aggregate.validations
                    if item.validation_id == approval.validation_id
                ),
                None,
            )
            now = _clock()
            return bool(
                aggregate.status is WorkProductStatus.APPROVED
                and version.version_id == work_product_version_id
                and version.version_number == work_product_version_number
                and version.content_sha256 == work_product_content_sha256
                and approval.status is ApprovalStatus.APPROVED
                and approval.binding == version.binding
                and validation is not None
                and validation.outcome is ValidationOutcome.PASSED
                and validation.binding == version.binding
                and canonical_sha256(approval) == approval_snapshot_sha256
                and approval.decision_authorization is not None
                and self._repository.authorization_is_active(
                    tenant_id,
                    matter_id,
                    approval.request_authorization,
                    now,
                )
                and self._repository.authorization_is_active(
                    tenant_id,
                    matter_id,
                    validation.authorization,
                    now,
                )
                and self._repository.authorization_is_active(
                    tenant_id,
                    matter_id,
                    approval.decision_authorization,
                    now,
                )
            )
        except Exception:
            return False


def _runner(boundary: PostgresBoundary) -> Callable[[Callable[[Any], T]], T]:
    def run(operation: Callable[[Any], T]) -> T:
        with boundary.transaction(TENANT_ID, MATTER_ID) as connection:
            return operation(PsycopgTransaction(connection))

    return run


def _executor(
    boundary: PostgresBoundary,
) -> Callable[[str, tuple[object, ...]], Sequence[Mapping[str, object]]]:
    def execute(
        statement: str, parameters: tuple[object, ...]
    ) -> Sequence[Mapping[str, object]]:
        with boundary.transaction(TENANT_ID, MATTER_ID) as connection:
            return tuple(connection.execute(statement, parameters).fetchall())

    return execute


def _sessions(
    boundary: PostgresBoundary,
) -> Callable[[], AbstractContextManager[PsycopgSession]]:
    @contextmanager
    def session() -> Iterator[PsycopgSession]:
        with boundary.transaction(TENANT_ID, MATTER_ID) as connection:
            yield PsycopgSession(connection)

    return session


def _clock() -> datetime:
    return datetime.now(UTC)


def _agent_run_service(core: PostgresBoundary) -> AgentRunService:
    record = AgentRunRecord.model_validate_json(AGENT_RUN_FIXTURE.read_text())
    request = record.request
    route = record.route
    if route is None or record.proposal_payload_sha256 is None:
        raise RuntimeError("public-synthetic Agent Run fixture is incomplete")
    now = _clock()
    analysis = AnalysisExecution(
        workflow_id=record.workflow_id,
        workflow_revision=record.workflow_revision,
        route=route,
        observed_agent_specification_sha256=request.agent_specification.spec_sha256,
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
        started_at=now,
        completed_at=now,
        tool_calls=record.tool_calls,
        recommendations=record.recommendations,
    )
    challenge = ChallengeExecution(
        challenger_route=route.model_copy(
            update={
                "logical_route_id": "sklegal.public-independent-challenge",
                "served_model_name": "qwen3.8-public-synthetic-challenger",
                "served_model_revision": "fixture-challenge-r1",
                "gateway_request_id": "public-synthetic-challenge-request-1",
            }
        ),
        independent_output_sha256="b1" * 32,
        outcome=ChallengeOutcome.NO_DEFECT,
        defects=(),
        created_at=now,
    )
    pins = StaticAnalysisPinVerifier(
        allowed_route_ids=frozenset(
            {request.requested_logical_route_id, "sklegal.public-independent-challenge"}
        ),
        current_snapshots=request.snapshots,
        allowed_agent_specifications=frozenset(
            {
                (
                    request.agent_specification.spec_sha256,
                    request.agent_specification.deployment_sha256,
                )
            }
        ),
        current_prompt_template_id=request.prompt_template_id,
        current_prompt_template_sha256=request.prompt_template_sha256,
        current_output_schema_id=request.output_schema_id,
        current_output_schema_sha256=request.output_schema_sha256,
        current_scoring_policy_id=request.scoring_policy_id,
        current_scoring_policy_sha256=request.scoring_policy_sha256,
        allowed_challenger_spec_sha256=frozenset({"a1" * 32}),
    )
    return AgentRunService(
        repository=PostgresAgentRunRepository(_runner(core)),
        executor=DeterministicPublicSyntheticExecutor(
            analysis=analysis,
            challenge=challenge,
        ),
        pins=pins,
        clock=_clock,
        id_factory=uuid4,
    )


def build_durable_feature_routers(
    *,
    core: PostgresBoundary,
    retrieval: PostgresBoundary,
    authorizer: CapabilityAuthorizer,
    principal_resolver: PrincipalResolver,
    scope_resolver: ScopeResolver,
) -> tuple[APIRouter, ...]:
    """Build all reviewed V2 feature routers with no in-memory repository."""

    core_runner = _runner(core)
    policy_state = DurablePolicyState(core)
    feature_routers = (
        build_joined_analysis_router(
            store=PostgresJoinedAnalysisStore(
                PostgresJoinedAnalysisRepository(_executor(core))
            ),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        ),
        build_agent_runs_router(
            service=_agent_run_service(core),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        ),
        build_artifact_intake_router(
            service=ArtifactIntakeService(
                store=PostgresArtifactStore(
                    PostgresArtifactRepository(_sessions(core))
                ),
                policy=DurableArtifactPolicy(policy_state),
                adapter=PostgresArtifactAdapter(core),
                clock=_clock,
            ),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        ),
        build_work_products_router(
            service=WorkProductService(
                repository=DurableWorkProductRepository(core_runner, policy_state),
                clock=_clock,
                id_factory=uuid4,
            ),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
            clock=_clock,
        ),
        build_task_deadline_router(
            service=TaskDeadlineService(
                repository=PostgresTaskDeadlineRepository(core_runner),
                policy=DurableTaskDeadlinePolicy(policy_state),
                simulation_gate=PostgresApprovalGateVerifier(core_runner, policy_state),
                clock=_clock,
            ),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        ),
        build_matter_activity_router(
            service=MatterActivityService(
                store=PostgresMatterActivityRepository(_sessions(core)),
                policy=DurableActivityPolicy(policy_state),
                clock=_clock,
            ),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        ),
        build_governed_corpus_router(
            service=GovernedCorpusService(
                core_repository=PostgresGovernedCorpusCoreRepository(core_runner),
                retrieval_repository=PostgresGovernedCorpusRetrievalRepository(
                    _runner(retrieval)
                ),
                policy=DurableGovernedCorpusPolicy(policy_state),
                clock=_clock,
                cursor_signing_key=hashlib.sha256(
                    b"sklegal-public-synthetic-cursor-v1"
                ).digest(),
            ),
            authorizer=authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        ),
    )
    if len(feature_routers) != 7:
        raise RuntimeError("durable V2 feature router inventory is incomplete")
    return feature_routers


__all__ = ["build_durable_feature_routers"]
