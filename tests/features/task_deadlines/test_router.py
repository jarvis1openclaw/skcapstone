from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sklegal_api.features.task_deadlines.contracts import UpsertTaskCommand
from sklegal_api.features.task_deadlines.router import build_task_deadline_router
from sklegal_api.features.task_deadlines.service import (
    StaticSimulationGateVerifier,
    StaticTaskDeadlinePolicy,
    TaskDeadlineService,
)
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose
from sklegal_persistence.features.task_deadlines.repository import (
    InMemoryTaskDeadlineRepository,
)

from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

from .helpers import HASH_D, MATTER, TENANT


def test_real_capauth_router_enforces_frozen_routes_and_sanitized_errors() -> None:
    rig = CapabilityTestRig()
    principal = rig.principal(tenant_id=TENANT)
    repository = InMemoryTaskDeadlineRepository()
    repository.set_matter_members(TENANT, MATTER, {principal.principal_id})
    policy = StaticTaskDeadlinePolicy(
        grants={
            (
                TENANT,
                MATTER,
                principal.principal_id,
                "matter.manage",
                "matter_management",
            ),
            (
                TENANT,
                MATTER,
                principal.principal_id,
                "matter.read",
                "matter_management",
            ),
        },
        revision=HASH_D,
        valid_until=rig.clock() + timedelta(hours=1),
    )
    service = TaskDeadlineService(
        repository=repository,
        policy=policy,
        simulation_gate=StaticSimulationGateVerifier(set()),
        clock=rig.clock,
    )

    def principal_resolver(_: Request) -> PrincipalContext:
        return principal

    def scope_resolver(request: Request) -> BoundaryScope:
        matter_id = UUID(str(request.path_params["matter_id"]))
        resource_id = next(
            (
                str(request.path_params[name])
                for name in ("task_id", "deadline_id")
                if name in request.path_params
            ),
            str(matter_id),
        )
        return BoundaryScope(
            tenant_id=TENANT,
            matter_id=matter_id,
            resource_id=resource_id,
        )

    app = FastAPI()
    app.include_router(
        build_task_deadline_router(
            service=service,
            authorizer=rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
    )
    client = TestClient(app)

    def token(capability: Capability, route: str, purpose: Purpose) -> str:
        grant = rig.grant(
            capability=capability,
            purpose=purpose,
            target=f"api:task_deadlines.{route}",
            tenant_id=TENANT,
            matter_id=MATTER,
            resource_id=str(MATTER),
        )
        return raw_leaf(rig.issue(principal, grant))

    try:
        command = UpsertTaskCommand(
            title="Router Task", description="Public-synthetic router proof."
        )
        created = client.post(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": f"Bearer {token(Capability.MATTER_MANAGE, 'manage', Purpose.MATTER_MANAGEMENT)}",
                "Idempotency-Key": "router-task-1",
            },
            json=command.model_dump(mode="json", by_alias=True),
        )
        assert created.status_code == 201, created.text
        assert created.json()["task"]["title"] == "Router Task"

        listed = client.get(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": f"Bearer {token(Capability.MATTER_READ, 'read', Purpose.MATTER_MANAGEMENT)}"
            },
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["tasks"][0]["title"] == "Router Task"

        wrong = client.post(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": f"Bearer {token(Capability.MATTER_READ, 'manage', Purpose.MATTER_MANAGEMENT)}",
                "Idempotency-Key": "wrong-capability",
            },
            json=command.model_dump(mode="json", by_alias=True),
        )
        assert wrong.status_code == 403
        assert wrong.json()["detail"]["code"] == "capability_denied"

        malformed = client.post(
            f"/v1/matters/{MATTER}/tasks",
            headers={
                "Authorization": f"Bearer {token(Capability.MATTER_MANAGE, 'manage', Purpose.MATTER_MANAGEMENT)}",
                "Idempotency-Key": "malformed-body",
            },
            json={"description": "PRIVATE-INTERNAL-DETAIL"},
        )
        assert malformed.status_code == 422
        assert malformed.json() == {"detail": {"code": "validation_failed"}}
        assert "PRIVATE-INTERNAL-DETAIL" not in malformed.text
    finally:
        client.close()
        rig.close()


def test_openapi_freezes_all_owned_task_deadline_operations() -> None:
    app = FastAPI()
    app.include_router(
        build_task_deadline_router(
            service=object(),  # type: ignore[arg-type]
            authorizer=object(),  # type: ignore[arg-type]
            principal_resolver=lambda request: None,  # type: ignore[arg-type]
            scope_resolver=lambda request: None,  # type: ignore[arg-type]
        )
    )
    operations = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
    }
    assert operations == {
        "upsert_task",
        "list_tasks",
        "get_task",
        "transition_task",
        "compute_deadline",
        "list_deadlines",
        "get_deadline",
        "review_deadline",
        "transition_deadline",
        "create_simulation_handoff",
    }
