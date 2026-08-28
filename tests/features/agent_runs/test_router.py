from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.agent_runs.contracts import HumanDispositionBody
from sklegal_api.features.agent_runs.router import build_agent_runs_router
from sklegal_api.features.agent_runs.service import (
    AgentRunService,
    DeterministicPublicSyntheticExecutor,
)
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose
from sklegal_persistence.features.agent_runs.repository import (
    InMemoryAgentRunRepository,
)

from tests.features.agent_runs.helpers import (
    AUTHZ_POLICY,
    MATTER_ID,
    NOW,
    SequentialIds,
    analysis_command,
    analysis_execution,
    challenge_command,
    challenge_execution,
    pin_verifier,
)
from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

START_KEY = "10000000-0000-4000-8000-000000000090"
CHALLENGE_KEY = "10000000-0000-4000-8000-000000000091"
DISPOSITION_KEY = "10000000-0000-4000-8000-000000000092"
OTHER_MATTER_ID = UUID("30000000-0000-4000-8000-000000000099")


class TestAgentRunsRouter:
    def setup_method(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.store = InMemoryAgentRunRepository()
        self.store.set_matter_members(
            self.principal.tenant_id, MATTER_ID, (self.principal.principal_id,)
        )
        self.executor = DeterministicPublicSyntheticExecutor(
            analysis=analysis_execution(), challenge=challenge_execution()
        )
        self.service = AgentRunService(
            repository=self.store,
            executor=self.executor,
            pins=pin_verifier(),
            clock=lambda: NOW,
            id_factory=SequentialIds(300),
        )

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter_id = UUID(str(request.path_params["matter_id"]))
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=matter_id,
                resource_id=str(matter_id),
            )

        app = FastAPI()
        app.include_router(
            build_agent_runs_router(
                service=self.service,
                authorizer=self.rig.authorizer,
                principal_resolver=principal_resolver,
                scope_resolver=scope_resolver,
            )
        )
        self.app = app
        self.client = TestClient(app)

    def teardown_method(self) -> None:
        self.client.close()
        self.rig.close()

    def token(
        self,
        route: str,
        capability: Capability,
        purpose: Purpose,
        *,
        matter_id: UUID = MATTER_ID,
    ) -> str:
        grant = self.rig.grant(
            capability=capability,
            purpose=purpose,
            target=f"api:agent_runs.{route}",
            matter_id=matter_id,
            resource_id=str(matter_id),
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    @staticmethod
    def auth(token: str, *, key: str | None = None) -> dict[str, str]:
        result = {"Authorization": f"Bearer {token}"}
        if key is not None:
            result["Idempotency-Key"] = key
        return result

    def start(self, *, key: str = START_KEY):  # type: ignore[no-untyped-def]
        token = self.token("start", Capability.CLAIM_PROPOSE, Purpose.CLAIM_DEVELOPMENT)
        return self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            json=analysis_command().model_dump(mode="json", by_alias=True),
            headers=self.auth(token, key=key),
        )

    def test_openapi_exposes_only_five_versioned_namespaced_operations(self) -> None:
        operation_ids = {
            operation["operationId"]
            for path in self.app.openapi()["paths"].values()
            for operation in path.values()
        }
        assert operation_ids == {
            "agent_runs_start",
            "agent_runs_list",
            "agent_runs_get",
            "agent_runs_challenge",
            "agent_runs_dispose",
        }
        start = self.app.openapi()["paths"]["/v1/matters/{matter_id}/agent-runs"][
            "post"
        ]
        assert any(
            parameter["name"] == "Idempotency-Key" and parameter["required"] is True
            for parameter in start["parameters"]
        )
        schema = start["requestBody"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/AnalysisRequestBody")
        response_schema = start["responses"]["201"]["content"]["application/json"][
            "schema"
        ]
        assert response_schema["$ref"].endswith("/AgentRunRecord")
        components = self.app.openapi()["components"]["schemas"]
        run_schema = components["AgentRunRecord"]
        assert "recommendations" in run_schema["properties"]
        assert "toolCalls" in run_schema["properties"]
        assert "authorization" in run_schema["properties"]

    def test_start_list_get_challenge_and_disposition_round_trip(self) -> None:
        started = self.start()
        assert started.status_code == 201
        run = started.json()
        assert run["request"]["classification"] == "public"
        assert run["request"]["publicSynthetic"] is True
        assert run["route"]["logicalRouteId"] == "sklegal.corpus-analysis"

        list_token = self.token("list", Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW)
        listed = self.client.get(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            headers=self.auth(list_token),
        )
        assert listed.status_code == 200
        assert [item["runId"] for item in listed.json()] == [run["runId"]]

        get_token = self.token("get", Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW)
        got = self.client.get(
            f"/v1/matters/{MATTER_ID}/agent-runs/{run['runId']}",
            headers=self.auth(get_token),
        )
        assert got.status_code == 200
        assert got.json()["auditEventIds"] == run["auditEventIds"]

        challenge_token = self.token(
            "challenge", Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW
        )
        challenged = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs/{run['runId']}/challenges",
            json=challenge_command().model_dump(mode="json", by_alias=True),
            headers=self.auth(challenge_token, key=CHALLENGE_KEY),
        )
        assert challenged.status_code == 200
        assert challenged.json()["version"] == 2
        assert challenged.json()["challenges"][0]["outcome"] == "no_defect"

        disposition = HumanDispositionBody(
            expected_run_version=2,
            recommendation_id=run["recommendations"][0]["recommendationId"],
            recommendation_version=1,
            decision="accept_as_proposed_task",
            rationale="Public-synthetic human review completed.",
            policy_revision=AUTHZ_POLICY,
        )
        disposition_token = self.token(
            "dispose", Capability.CLAIM_REVIEW, Purpose.CLAIM_REVIEW
        )
        disposed = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs/{run['runId']}/dispositions",
            json=disposition.model_dump(mode="json", by_alias=True),
            headers=self.auth(disposition_token, key=DISPOSITION_KEY),
        )
        assert disposed.status_code == 200
        assert disposed.json()["version"] == 3
        assert disposed.json()["dispositions"][0]["createsDomainRecord"] is False
        assert disposed.json()["dispositions"][0]["externalEffect"] is False

    def test_missing_wrong_and_cross_matter_capability_are_denied(self) -> None:
        missing = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            json=analysis_command().model_dump(mode="json", by_alias=True),
            headers={"Idempotency-Key": START_KEY},
        )
        assert missing.status_code == 403
        assert missing.json()["detail"]["code"] == "capability_denied"

        wrong = self.token("start", Capability.MATTER_READ, Purpose.MATTER_MANAGEMENT)
        denied = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            json=analysis_command().model_dump(mode="json", by_alias=True),
            headers=self.auth(wrong, key=START_KEY),
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "capability_denied"

        other = self.token(
            "start",
            Capability.CLAIM_PROPOSE,
            Purpose.CLAIM_DEVELOPMENT,
            matter_id=OTHER_MATTER_ID,
        )
        cross_scope = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            json=analysis_command().model_dump(mode="json", by_alias=True),
            headers=self.auth(other, key=START_KEY),
        )
        assert cross_scope.status_code == 403
        assert cross_scope.json()["detail"]["code"] == "capability_denied"

    def test_idempotency_drift_and_missing_header_are_sanitized(self) -> None:
        assert self.start().status_code == 201
        changed = analysis_command().model_copy(
            update={"analysis_kind": "recommendation_refresh"}
        )
        token = self.token("start", Capability.CLAIM_PROPOSE, Purpose.CLAIM_DEVELOPMENT)
        drift = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            json=changed.model_dump(mode="json", by_alias=True),
            headers=self.auth(token, key=START_KEY),
        )
        assert drift.status_code == 409
        assert drift.json()["detail"] == {"code": "idempotency_key_reused"}

        missing_key_token = self.token(
            "start", Capability.CLAIM_PROPOSE, Purpose.CLAIM_DEVELOPMENT
        )
        missing_key = self.client.post(
            f"/v1/matters/{MATTER_ID}/agent-runs",
            json=analysis_command().model_dump(mode="json", by_alias=True),
            headers=self.auth(missing_key_token),
        )
        assert missing_key.status_code == 422

    def test_repository_outage_fails_closed_without_executor_call(self) -> None:
        self.store.available = False
        response = self.start()
        assert response.status_code == 503
        assert response.json()["detail"] == {"code": "agent_run_store_unavailable"}
        assert self.executor.analysis_calls == 0
