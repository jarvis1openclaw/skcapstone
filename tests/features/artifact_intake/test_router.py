from __future__ import annotations

import unittest
from datetime import timedelta
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.artifact_intake.router import build_artifact_intake_router
from sklegal_api.features.artifact_intake.service import (
    ArtifactIntakeService,
    StaticArtifactPolicy,
)
from sklegal_api.features.artifact_intake.store import InMemoryArtifactStore
from sklegal_api.features.artifact_intake.synthetic import SyntheticArtifactAdapter
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose

from tests.features.artifact_intake.factories import MATTER_ID, command, original
from tests.support.capauth_contract import CapabilityTestRig, raw_leaf


class ArtifactIntakeRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.store = InMemoryArtifactStore()
        self.policy = StaticArtifactPolicy(
            memberships={
                (
                    self.principal.tenant_id,
                    MATTER_ID,
                    self.principal.principal_id,
                )
            },
            allowed_classifications={"public"},
            revision="8" * 64,
            valid_until=self.rig.clock() + timedelta(hours=1),
        )
        self.service = ArtifactIntakeService(
            store=self.store,
            policy=self.policy,
            adapter=SyntheticArtifactAdapter(),
            clock=self.rig.clock,
        )

        def principal_resolver(_: Request) -> PrincipalContext:
            return self.principal

        def scope_resolver(request: Request) -> BoundaryScope:
            matter_id = UUID(str(request.path_params["matter_id"]))
            resource_id = request.path_params.get("artifact_id") or str(matter_id)
            return BoundaryScope(
                tenant_id=self.principal.tenant_id,
                matter_id=matter_id,
                resource_id=str(resource_id),
            )

        app = FastAPI()
        app.include_router(
            build_artifact_intake_router(
                service=self.service,
                authorizer=self.rig.authorizer,
                principal_resolver=principal_resolver,
                scope_resolver=scope_resolver,
            )
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.rig.close()

    def token(
        self,
        *,
        route: str,
        capability: Capability,
        resource_id: str | None = None,
    ) -> str:
        grant = self.rig.grant(
            capability=capability,
            purpose=Purpose.EVIDENCE_REVIEW,
            target=f"api:artifact_intake.{route}",
            tenant_id=self.principal.tenant_id,
            matter_id=MATTER_ID,
            resource_id=resource_id or str(MATTER_ID),
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def test_post_and_get_use_exact_frozen_routes_and_camel_case_contract(self) -> None:
        posted = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts",
            headers={
                "Authorization": f"Bearer {self.token(route='create', capability=Capability.EVIDENCE_MANAGE)}",
                "Idempotency-Key": "router-artifact-001",
            },
            json=command(derivations=("ocr", "text_extraction")).model_dump(
                mode="json", by_alias=True
            ),
        )
        self.assertEqual(201, posted.status_code, posted.text)
        receipt = posted.json()
        artifact_id = receipt["artifact"]["artifactId"]
        self.assertEqual("original", receipt["artifact"]["artifactKind"])
        self.assertNotIn("contentBase64", posted.text)

        fetched = self.client.get(
            f"/v1/matters/{MATTER_ID}/artifacts/{artifact_id}",
            headers={
                "Authorization": f"Bearer {self.token(route='get', capability=Capability.EVIDENCE_READ, resource_id=artifact_id)}"
            },
        )
        self.assertEqual(200, fetched.status_code, fetched.text)
        self.assertEqual(artifact_id, fetched.json()["artifactId"])
        self.assertEqual("artifact.read", self.store.audit_events[-1].action)

        review = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts/{artifact_id}/reviews",
            headers={
                "Authorization": f"Bearer {self.token(route='review', capability=Capability.EVIDENCE_MANAGE, resource_id=artifact_id)}",
                "Idempotency-Key": "router-artifact-review-001",
            },
            json={
                "artifactId": artifact_id,
                "expectedProjectionRevision": 1,
                "decision": "accepted",
                "rationale": "Public synthetic review is complete.",
            },
        )
        self.assertEqual(200, review.status_code, review.text)
        derived = receipt["derivedArtifacts"]
        correction_target = derived[0]["artifactId"]
        correction_body = {
            "targetArtifactId": correction_target,
            "expectedProjectionRevision": 1,
            "corrected": original(
                b"Corrected public synthetic output.",
                filename="synthetic.corrected.txt",
            ).model_dump(mode="json", by_alias=True),
            "reason": "Human review corrected the synthetic derivation.",
            "toolName": "human-review",
            "toolVersion": "1",
        }
        corrected = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts/{correction_target}/corrections",
            headers={
                "Authorization": f"Bearer {self.token(route='correct', capability=Capability.EVIDENCE_MANAGE, resource_id=correction_target)}",
                "Idempotency-Key": "router-artifact-correction-001",
            },
            json=correction_body,
        )
        self.assertEqual(201, corrected.status_code, corrected.text)

        supersession_target = derived[1]["artifactId"]
        successor = corrected.json()["artifact"]["artifactId"]
        superseded = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts/{supersession_target}/supersessions",
            headers={
                "Authorization": f"Bearer {self.token(route='supersede', capability=Capability.EVIDENCE_MANAGE, resource_id=supersession_target)}",
                "Idempotency-Key": "router-artifact-supersession-001",
            },
            json={
                "artifactId": supersession_target,
                "successorArtifactId": successor,
                "expectedProjectionRevision": 1,
                "reason": "Reviewed synthetic output has a preferred successor.",
            },
        )
        self.assertEqual(201, superseded.status_code, superseded.text)
        self.assertEqual("superseded", superseded.json()["artifact"]["reviewState"])

    def test_missing_idempotency_and_invalid_body_are_sanitized(self) -> None:
        token = self.token(route="create", capability=Capability.EVIDENCE_MANAGE)
        missing = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts",
            headers={"Authorization": f"Bearer {token}"},
            json=command().model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(400, missing.status_code)
        self.assertEqual("validation_failed", missing.json()["detail"]["code"])

        malformed_token = self.token(
            route="create", capability=Capability.EVIDENCE_MANAGE
        )
        malformed = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts",
            headers={
                "Authorization": f"Bearer {malformed_token}",
                "Idempotency-Key": "router-invalid",
            },
            json={"original": {"contentBase64": "PUBLIC-SYNTHETIC-SECRET-LIKE"}},
        )
        self.assertEqual(422, malformed.status_code)
        self.assertEqual("validation_failed", malformed.json()["detail"]["code"])
        self.assertNotIn("PUBLIC-SYNTHETIC-SECRET-LIKE", malformed.text)

    def test_wrong_capability_denies_before_store_lookup(self) -> None:
        token = self.token(route="create", capability=Capability.EVIDENCE_READ)
        before = self.store.lookup_count
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "router-wrong-capability",
            },
            json=command().model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("capability_denied", response.json()["detail"]["code"])
        self.assertEqual(before, self.store.lookup_count)

    def test_policy_and_store_outages_use_closed_codes(self) -> None:
        self.policy.available = False
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts",
            headers={
                "Authorization": f"Bearer {self.token(route='create', capability=Capability.EVIDENCE_MANAGE)}",
                "Idempotency-Key": "router-policy-outage",
            },
            json=command().model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(503, response.status_code)
        self.assertEqual("policy_unavailable", response.json()["detail"]["code"])

        self.policy.available = True
        self.store.audit_available = False
        response = self.client.post(
            f"/v1/matters/{MATTER_ID}/artifacts",
            headers={
                "Authorization": f"Bearer {self.token(route='create', capability=Capability.EVIDENCE_MANAGE)}",
                "Idempotency-Key": "router-audit-outage",
            },
            json=command().model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(503, response.status_code)
        self.assertEqual("dependency_unavailable", response.json()["detail"]["code"])
        self.assertEqual(
            (), self.store.artifact_ids(self.principal.tenant_id, MATTER_ID)
        )

    def test_openapi_contains_all_frozen_artifact_lifecycle_operations(self) -> None:
        document = self.client.get("/openapi.json").json()
        routes = {
            (method, path, operation["operationId"])
            for path, path_item in document["paths"].items()
            for method, operation in path_item.items()
            if path.endswith("/artifacts") or "/artifacts/{artifact_id}" in path
        }
        self.assertEqual(
            {
                (
                    "post",
                    "/v1/matters/{matter_id}/artifacts",
                    "artifact_intake_create",
                ),
                (
                    "get",
                    "/v1/matters/{matter_id}/artifacts/{artifact_id}",
                    "artifact_intake_get",
                ),
                (
                    "post",
                    "/v1/matters/{matter_id}/artifacts/{artifact_id}/reviews",
                    "artifact_intake_review",
                ),
                (
                    "post",
                    "/v1/matters/{matter_id}/artifacts/{artifact_id}/corrections",
                    "artifact_intake_correct",
                ),
                (
                    "post",
                    "/v1/matters/{matter_id}/artifacts/{artifact_id}/supersessions",
                    "artifact_intake_supersede",
                ),
            },
            routes,
        )
        create_schema = document["paths"]["/v1/matters/{matter_id}/artifacts"]["post"][
            "responses"
        ]["201"]["content"]["application/json"]["schema"]
        get_schema = document["paths"][
            "/v1/matters/{matter_id}/artifacts/{artifact_id}"
        ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertTrue(create_schema["$ref"].endswith("/ArtifactIntakeReceipt"))
        self.assertTrue(get_schema["$ref"].endswith("/ArtifactRead"))


if __name__ == "__main__":
    unittest.main()
