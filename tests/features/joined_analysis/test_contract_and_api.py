"""Public-synthetic contract and CapAuth-first API tests for JOIN-01."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sklegal_api.features.joined_analysis.contract import (
    JoinedAnalysisSnapshotProjection,
)
from sklegal_api.features.joined_analysis.router import build_joined_analysis_router
from sklegal_api.features.joined_analysis.service import (
    projection_provenance_sha256,
    projection_sha256,
)
from sklegal_api.features.joined_analysis.stores import (
    PublicSyntheticJoinedAnalysisStore,
    seal_public_synthetic_projection,
)
from sklegal_capauth import BoundaryScope, Capability, PrincipalContext, Purpose

from tests.support.capauth_contract import CapabilityTestRig, raw_leaf

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/mvp/fragments/joined_analysis/public-synthetic-v1.json"
TENANT_ID = UUID("91000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("91000000-0000-4000-8000-000000000002")
OTHER_MATTER_ID = UUID("91000000-0000-4000-8000-000000000099")


def fixture_payload() -> dict[str, object]:
    projection = seal_public_synthetic_projection(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    return projection.model_dump(mode="json", by_alias=False)


class RecordingStore(PublicSyntheticJoinedAnalysisStore):
    def __init__(self) -> None:
        super().__init__(FIXTURE)
        self.calls: list[str] = []

    def is_matter_member(
        self, tenant_id: UUID, matter_id: UUID, principal_id: UUID
    ) -> bool:
        self.calls.append("membership")
        return super().is_matter_member(tenant_id, matter_id, principal_id)

    def projection(
        self, tenant_id: UUID, matter_id: UUID
    ) -> JoinedAnalysisSnapshotProjection | None:
        self.calls.append("projection")
        return super().projection(tenant_id, matter_id)


@pytest.fixture
def api() -> tuple[TestClient, CapabilityTestRig, RecordingStore, PrincipalContext]:
    rig = CapabilityTestRig()
    principal = rig.principal(tenant_id=TENANT_ID)
    store = RecordingStore()
    store.set_matter_members(TENANT_ID, MATTER_ID, frozenset({principal.principal_id}))

    def principal_resolver(_: Request) -> PrincipalContext:
        return principal

    def scope_resolver(request: Request) -> BoundaryScope:
        matter_id = UUID(str(request.path_params["matter_id"]))
        return BoundaryScope(
            tenant_id=principal.tenant_id,
            matter_id=matter_id,
            resource_id=str(matter_id),
        )

    app = FastAPI()
    app.include_router(
        build_joined_analysis_router(
            store=store,
            authorizer=rig.authorizer,
            principal_resolver=principal_resolver,
            scope_resolver=scope_resolver,
        )
    )
    client = TestClient(app)
    try:
        yield client, rig, store, principal
    finally:
        client.close()
        rig.close()


def token(
    rig: CapabilityTestRig,
    principal: PrincipalContext,
    matter_id: UUID = MATTER_ID,
) -> str:
    grant = rig.grant(
        capability=Capability.CLAIM_REVIEW,
        purpose=Purpose.CLAIM_REVIEW,
        target="api:joined_analysis.read",
        tenant_id=TENANT_ID,
        matter_id=matter_id,
        resource_id=str(matter_id),
    )
    return raw_leaf(rig.issue(principal, grant))


def get(
    api: tuple[TestClient, CapabilityTestRig, RecordingStore, PrincipalContext],
    *,
    matter_id: UUID = MATTER_ID,
    query: str = "",
    bearer: str | None = None,
):
    client, rig, _, principal = api
    selected = bearer or token(rig, principal, matter_id)
    return client.get(
        f"/v1/matters/{matter_id}/analysis{query}",
        headers={"Authorization": f"Bearer {selected}"},
    )


def test_fixture_is_a_closed_joined_graph_with_exact_digest() -> None:
    projection = JoinedAnalysisSnapshotProjection.model_validate(fixture_payload())
    assert projection_sha256(projection) == projection.snapshot.projection_sha256
    for name, digest in projection_provenance_sha256(projection).items():
        assert getattr(projection.snapshot, name) == digest
    assert projection.forum is not None
    assert len(projection.proceedings) == 1
    assert {item.theory_kind for item in projection.theories} == {"claim", "defense"}
    claim = next(item for item in projection.theories if item.theory_kind == "claim")
    assert {item.role for item in claim.evidence} == {"support", "counter_support"}
    assert {item.role for item in claim.authorities} == {
        "support",
        "contrary_authority",
    }
    assert any(item.blocking for item in claim.gaps)
    assert claim.status == "accepted"
    assert claim.ledger_projection is not None
    assert claim.ledger_projection.projected_claim_status == "accepted"
    migration = claim.ledger_projection.legacy_migration
    assert migration is not None
    assert migration.legacy_status == "supported"
    assert migration.mapped_claim_status == "accepted"
    assert migration.mapping_revision == "ledger-claim-to-claim-v1"


def test_canonical_claim_without_legacy_migration_preserves_direct_status() -> None:
    payload = fixture_payload()
    claim = payload["theories"][0]  # type: ignore[index]
    claim["status"] = "under_review"
    ledger = claim["ledger_projection"]
    ledger["projected_claim_status"] = "under_review"
    ledger["legacy_migration"] = None
    projection = JoinedAnalysisSnapshotProjection.model_validate(payload)
    assert projection.theories[0].status == "under_review"
    assert projection.theories[0].ledger_projection is not None
    assert projection.theories[0].ledger_projection.legacy_migration is None


@pytest.mark.parametrize("claim_status", ["accepted", "rejected"])
def test_terminal_canonical_claim_status_requires_legacy_mapping(
    claim_status: str,
) -> None:
    payload = fixture_payload()
    claim = payload["theories"][0]  # type: ignore[index]
    claim["status"] = claim_status
    ledger = claim["ledger_projection"]
    ledger["projected_claim_status"] = claim_status
    ledger["legacy_migration"] = None
    with pytest.raises(ValidationError, match="legacy ledger status mapping"):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


@pytest.mark.parametrize("ledger_status", ["accepted", "rejected"])
def test_canonical_only_status_is_rejected_as_legacy_ledger_status(
    ledger_status: str,
) -> None:
    payload = fixture_payload()
    payload["theories"][0]["ledger_projection"]["legacy_migration"][  # type: ignore[index]
        "legacy_status"
    ] = ledger_status
    with pytest.raises(ValidationError):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


@pytest.mark.parametrize(
    ("legacy_status", "mapped_claim_status"),
    [("under_review", "accepted"), ("supported", "challenged")],
)
def test_legacy_ledger_mapping_must_be_exact(
    legacy_status: str, mapped_claim_status: str
) -> None:
    payload = fixture_payload()
    migration = payload["theories"][0]["ledger_projection"][  # type: ignore[index]
        "legacy_migration"
    ]
    migration["legacy_status"] = legacy_status
    migration["mapped_claim_status"] = mapped_claim_status
    with pytest.raises(ValidationError, match="legacy ledger status mapping"):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


def test_issue_edges_must_be_reciprocal() -> None:
    payload = fixture_payload()
    payload["issues"].append(  # type: ignore[union-attr]
        {
            "issue_id": "91000000-0000-4000-8000-000000000034",
            "question": "A second synthetic Issue must not claim the same Claim.",
            "status": "identified",
            "version": 1,
            "theory_ids": ["91000000-0000-4000-8000-000000000031"],
        }
    )
    with pytest.raises(ValidationError, match="Issue and Claim or Defense mapping"):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


def test_element_edges_must_be_reciprocal() -> None:
    payload = fixture_payload()
    payload["theories"][1]["element_ids"].append(  # type: ignore[index]
        "91000000-0000-4000-8000-000000000040"
    )
    with pytest.raises(ValidationError, match="Element and Claim or Defense mapping"):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


def test_fact_assertion_subject_must_exist_in_joined_graph() -> None:
    payload = fixture_payload()
    payload["fact_assertions"][0][  # type: ignore[index]
        "subject_ref"
    ] = "91000000-0000-4000-8000-000000000099"
    with pytest.raises(ValidationError, match="Fact Assertion references an unknown"):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (
            ("theories", 0, "ledger_projection", "claim_id"),
            "91000000-0000-4000-8000-000000000099",
        ),
        (("theories", 0, "ledger_projection", "projected_claim_version"), 3),
        (("theories", 0, "ledger_projection", "projected_claim_status"), "rejected"),
        (("theories", 1, "ledger_projection"), {}),
        (("theories", 0, "issue_id"), "91000000-0000-4000-8000-000000000099"),
        (("theories", 0, "element_ids", 0), "91000000-0000-4000-8000-000000000099"),
        (("elements", 0, "theory_kind"), "defense"),
        (
            ("elements", 0, "burden", "authority_id"),
            "91000000-0000-4000-8000-000000000099",
        ),
        (("theories", 0, "authorities", 1, "reasons"), []),
        (
            ("theories", 0, "authorities", 0, "authority_id"),
            "91000000-0000-4000-8000-000000000071",
        ),
        (("issues", 0, "theory_ids"), ["91000000-0000-4000-8000-000000000032"]),
        (
            ("theories", 0, "evidence", 0, "source", "source_locator"),
            "fixture://joined-analysis/mutated",
        ),
        (("evidence_items", 0, "content_sha256"), "8" * 64),
        (("authorities", 0, "source", "origin"), "course_instruction"),
        (("theories", 0, "evidence", 0, "source", "span_end"), 10),
    ],
)
def test_graph_mutations_fail_closed(path: tuple[object, ...], value: object) -> None:
    payload = deepcopy(fixture_payload())
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises((ValidationError, ValueError)):
        JoinedAnalysisSnapshotProjection.model_validate(payload)


def test_joined_analysis_returns_request_policy_and_source_roles(api) -> None:
    response = get(api)
    assert response.status_code == 200
    body = response.json()
    assert body["schemaRevision"] == "sklegal-matter-analysis/v1"
    assert body["matterId"] == str(MATTER_ID)
    assert body["classification"] == {
        "value": "public",
        "purpose": "claim_review",
        "egress": "local_only",
        "sourceRights": "public_synthetic",
    }
    assert body["policyIdentity"]["capability"] == "claim.review"
    assert body["policyIdentity"]["purpose"] == "claim_review"
    assert len(body["policyIdentity"]["trustedIssuerPolicyRevision"]) == 64
    roles = {item["role"] for theory in body["theories"] for item in theory["evidence"]}
    assert roles == {"support", "counter_support"}
    authority_roles = {
        item["role"] for theory in body["theories"] for item in theory["authorities"]
    }
    assert "contrary_authority" in authority_roles
    assert api[2].calls == ["membership", "projection"]


def test_stable_snapshot_bound_pagination(api) -> None:
    first = get(api, query="?limit=1")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["page"]["returned"] == 1
    assert first_body["page"]["hasMore"] is True
    assert first_body["issues"][0]["theoryIds"] == [
        first_body["theories"][0]["theoryId"]
    ]
    assert {item["theoryId"] for item in first_body["elements"]} == {
        first_body["theories"][0]["theoryId"]
    }
    assert {item["evidenceItemId"] for item in first_body["evidenceItems"]} == {
        link["evidenceItemId"] for link in first_body["theories"][0]["evidence"]
    }
    cursor = first_body["page"]["nextCursor"]
    second = get(api, query=f"?limit=1&cursor={cursor}")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["page"]["hasMore"] is False
    assert (
        first_body["theories"][0]["theoryId"] != second_body["theories"][0]["theoryId"]
    )
    stale = get(api, query="?limit=1&cursor=amExOmJhZA")
    assert stale.status_code == 400
    assert stale.json() == {"detail": {"code": "invalid_cursor"}}
    stale_raw = (
        "ja1:91000000-0000-4000-8000-000000000099:"
        f"{first_body['snapshot']['projectionSha256']}:1"
    ).encode("ascii")
    stale_cursor = base64.urlsafe_b64encode(stale_raw).decode("ascii").rstrip("=")
    stale_snapshot = get(api, query=f"?limit=1&cursor={stale_cursor}")
    assert stale_snapshot.status_code == 400
    assert stale_snapshot.json() == {"detail": {"code": "invalid_cursor"}}


def test_membership_denial_occurs_before_projection_read(api) -> None:
    client, rig, store, principal = api
    store.set_matter_members(TENANT_ID, MATTER_ID, frozenset())
    response = client.get(
        f"/v1/matters/{MATTER_ID}/analysis",
        headers={"Authorization": f"Bearer {token(rig, principal)}"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "matter_membership_denied"}}
    assert store.calls == ["membership"]


def test_capauth_scope_denial_occurs_before_store(api) -> None:
    client, rig, store, principal = api
    wrong_scope = token(rig, principal, OTHER_MATTER_ID)
    response = client.get(
        f"/v1/matters/{MATTER_ID}/analysis",
        headers={"Authorization": f"Bearer {wrong_scope}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "capability_denied"
    assert store.calls == []


def test_wrong_capability_policy_denial_occurs_before_store(api) -> None:
    client, rig, store, principal = api
    grant = rig.grant(
        capability=Capability.MATTER_READ,
        purpose=Purpose.MATTER_MANAGEMENT,
        target="api:joined_analysis.read",
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        resource_id=str(MATTER_ID),
    )
    wrong_capability = raw_leaf(rig.issue(principal, grant))
    response = client.get(
        f"/v1/matters/{MATTER_ID}/analysis",
        headers={"Authorization": f"Bearer {wrong_capability}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "capability_denied"
    assert store.calls == []


def test_page_limit_is_bounded_before_store(api) -> None:
    response = get(api, query="?limit=101")
    assert response.status_code == 422
    assert api[2].calls == []


def test_store_outage_is_sanitized_and_does_not_fall_back(api) -> None:
    api[2].available = False
    response = get(api)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "joined_analysis_unavailable"}}
    assert "store" not in response.text.lower()


def test_unknown_matter_is_not_revealed_without_membership(api) -> None:
    response = get(api, matter_id=OTHER_MATTER_ID)
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "matter_membership_denied"}}


def test_projection_byte_mutation_fails_digest_before_response(
    api, tmp_path: Path
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["theories"][0]["statement"] = "Mutated after sealing."  # type: ignore[index]
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    store = PublicSyntheticJoinedAnalysisStore(tampered)
    store.set_matter_members(TENANT_ID, MATTER_ID, frozenset({api[3].principal_id}))
    with pytest.raises(Exception, match="digest"):
        store.projection(TENANT_ID, MATTER_ID)
