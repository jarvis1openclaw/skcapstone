from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sklegal_api.features.matter_activity.router import build_matter_activity_router
from sklegal_api.features.matter_activity.service import (
    MatterActivityService,
    StaticActivityPolicy,
)
from sklegal_api.features.matter_activity.synthetic import (
    MATTER_ID,
    OTHER_MATTER_ID,
    build_public_synthetic_activity_store,
)
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose

from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

COMMON_ENVELOPE = json.loads(
    Path("docs/contracts/v2-mvp/v2-common-envelope.v1.schema.json").read_text(
        encoding="utf-8"
    )
)


class TestMatterActivityRouter:
    def setup_method(self) -> None:
        self.rig = CapabilityTestRig()
        self.principal = self.rig.principal()
        self.store = build_public_synthetic_activity_store()
        self.policy = StaticActivityPolicy(
            memberships={
                (
                    self.principal.tenant_id,
                    MATTER_ID,
                    self.principal.principal_id,
                )
            },
            revision="9" * 64,
            valid_until=self.rig.clock() + timedelta(hours=1),
        )
        service = MatterActivityService(
            store=self.store,
            policy=self.policy,
            clock=self.rig.clock,
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
            build_matter_activity_router(
                service=service,
                authorizer=self.rig.authorizer,
                principal_resolver=principal_resolver,
                scope_resolver=scope_resolver,
            )
        )
        self.client = TestClient(app)

    def teardown_method(self) -> None:
        self.client.close()
        self.rig.close()

    def token(
        self,
        *,
        route: str,
        capability: Capability,
        purpose: Purpose,
    ) -> str:
        grant = self.rig.grant(
            capability=capability,
            purpose=purpose,
            target=f"api:matter_activity.{route}",
            tenant_id=self.principal.tenant_id,
            matter_id=MATTER_ID,
            resource_id=str(MATTER_ID),
        )
        return raw_leaf(self.rig.issue(self.principal, grant))

    def test_list_and_export_use_exact_capabilities_and_camel_case(self) -> None:
        listed = self.client.get(
            f"/v1/matters/{MATTER_ID}/activity?limit=4",
            headers={
                "Authorization": "Bearer "
                + self.token(
                    route="list_activity",
                    capability=Capability.AUDIT_READ,
                    purpose=Purpose.AUDIT_REVIEW,
                )
            },
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["version"] == "sklegal-matter-activity/v1"
        assert [item["eventSequence"] for item in body["items"]] == [1, 2, 3, 4]
        assert body["nextCursor"]

        export = self.client.post(
            f"/v1/matters/{MATTER_ID}/activity-exports",
            headers={
                "Authorization": "Bearer "
                + self.token(
                    route="create_activity_export",
                    capability=Capability.AUDIT_READ,
                    purpose=Purpose.AUDIT_REVIEW,
                ),
                "Idempotency-Key": "router-activity-export-001",
            },
            json={
                "title": "Public synthetic activity export",
                "firstEventSequence": 1,
                "lastEventSequence": 4,
                "expectedProjectedSequence": body["snapshotSequence"],
                "expectedProjectedSha256": body["snapshotSha256"],
                "expectedResourceVersion": body["snapshotSequence"],
            },
        )
        assert export.status_code == 201, export.text
        receipt = export.json()
        assert receipt["operation_id"] == "create_activity_export"
        assert receipt["tenant_id"] == str(self.principal.tenant_id)
        assert receipt["matter_id"] == str(MATTER_ID)
        assert receipt["resource_id"] == receipt["proposal"]["proposalId"]
        assert receipt["resource_version"] == 1
        assert receipt["idempotency_key"] == "router-activity-export-001"
        assert receipt["correlation_id"]
        assert receipt["request_sha256"]
        assert receipt["audit_id"]
        assert receipt["outbox_id"]
        assert receipt["schema_revision"]
        assert receipt["projection_revision"]
        assert receipt["provenance"]["projection_revision"]
        assert receipt["provenance"]["watermark"]["projectedSequence"]
        assert receipt["provenance"]["export_sha256"]
        assert receipt["provenance"]["manifest_sha256"]
        assert receipt["provenance"]["source_chain_heads"]
        assert receipt["proposal"]["status"] == "proposed"
        assert receipt["proposal"]["approvalId"] is None
        assert receipt["proposal"]["dispatchState"] == "not_requested"
        mutation_receipt = {
            key: receipt[key]
            for key in COMMON_ENVELOPE["$defs"]["mutation_receipt"]["required"]
        }
        Draft202012Validator(COMMON_ENVELOPE["$defs"]["mutation_receipt"]).validate(
            mutation_receipt
        )

    def test_wrong_capability_denies_before_cursor_or_store(self) -> None:
        self.store.available = False
        wrong = self.client.get(
            f"/v1/matters/{MATTER_ID}/activity?cursor=not-a-cursor",
            headers={
                "Authorization": "Bearer "
                + self.token(
                    route="list_activity",
                    capability=Capability.MATTER_READ,
                    purpose=Purpose.MATTER_MANAGEMENT,
                )
            },
        )
        assert wrong.status_code == 403
        assert wrong.json()["detail"] == {
            "code": "access_denied",
            "message": "Access is denied.",
            "correlation_id": wrong.json()["detail"]["correlation_id"],
            "retryable": False,
        }
        Draft202012Validator(COMMON_ENVELOPE["$defs"]["error"]).validate(wrong.json())

    def test_matter_resource_scope_denies_cross_matter_before_store(self) -> None:
        self.store.available = False
        response = self.client.get(
            f"/v1/matters/{OTHER_MATTER_ID}/activity?cursor=not-a-cursor",
            headers={
                "Authorization": "Bearer "
                + self.token(
                    route="list_activity",
                    capability=Capability.AUDIT_READ,
                    purpose=Purpose.AUDIT_REVIEW,
                )
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "access_denied"
        Draft202012Validator(COMMON_ENVELOPE["$defs"]["error"]).validate(
            response.json()
        )

    def test_invalid_export_is_sanitized_and_never_dispatches(self) -> None:
        token = self.token(
            route="create_activity_export",
            capability=Capability.AUDIT_READ,
            purpose=Purpose.AUDIT_REVIEW,
        )
        missing = self.client.post(
            f"/v1/matters/{MATTER_ID}/activity-exports",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "validation_failed"

        marker = "PUBLIC-SYNTHETIC-SENSITIVE-MARKER"
        invalid = self.client.post(
            f"/v1/matters/{MATTER_ID}/activity-exports",
            headers={
                "Authorization": f"Bearer {self.token(route='create_activity_export', capability=Capability.AUDIT_READ, purpose=Purpose.AUDIT_REVIEW)}",
                "Idempotency-Key": "router-invalid",
            },
            json={"title": marker},
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "validation_failed"
        assert marker not in invalid.text
        Draft202012Validator(COMMON_ENVELOPE["$defs"]["error"]).validate(invalid.json())
        assert self.store.audit_facts == ()
        assert self.store.outbox_messages == ()

    def test_invalid_query_uses_the_closed_common_error_envelope(self) -> None:
        invalid = self.client.get(
            f"/v1/matters/{MATTER_ID}/activity?limit=not-an-integer",
            headers={
                "Authorization": "Bearer "
                + self.token(
                    route="list_activity",
                    capability=Capability.AUDIT_READ,
                    purpose=Purpose.AUDIT_REVIEW,
                )
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "validation_failed"
        Draft202012Validator(COMMON_ENVELOPE["$defs"]["error"]).validate(invalid.json())

    def test_openapi_freezes_only_two_feature_operations(self) -> None:
        document = self.client.get("/openapi.json").json()
        activity = {
            (method, path, operation["operationId"])
            for path, path_item in document["paths"].items()
            for method, operation in path_item.items()
            if "/activity" in path
        }
        assert activity == {
            (
                "get",
                "/v1/matters/{matter_id}/activity",
                "list_activity",
            ),
            (
                "post",
                "/v1/matters/{matter_id}/activity-exports",
                "create_activity_export",
            ),
        }
        assert (
            "/v1/matters/{matter_id}/activity/export-proposals" not in document["paths"]
        )
