from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sklegal_persistence.features.work_products.models import (
    ApprovalRecord,
    ApprovalStatus,
    AuthorizationEvidence,
    SentenceGroundingRecord,
    VersionStatus,
    WorkProductAggregate,
    WorkProductVersionRecord,
    canonical_sha256,
    content_sha256,
)

from .helpers import DRAFTER, MATTER, T0, TENANT, actor, create_body, uid


def version(**changes: object) -> WorkProductVersionRecord:
    content = str(changes.pop("content", "Public synthetic sentence."))
    values: dict[str, object] = {
        "version_id": uid(1),
        "version_number": 1,
        "content": content,
        "content_sha256": content_sha256(content),
        "source": create_body().source,
        "created_by_principal_id": DRAFTER,
        "created_at": T0,
    }
    values.update(changes)
    return WorkProductVersionRecord(**values)  # type: ignore[arg-type]


def aggregate(**changes: object) -> WorkProductAggregate:
    item = version()
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "matter_id": MATTER,
        "work_product_id": uid(2),
        "aggregate_version": 1,
        "title": "Synthetic memorandum",
        "work_product_kind": "memo",
        "status": "draft",
        "current_version_id": item.version_id,
        "versions": (item,),
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(changes)
    return WorkProductAggregate(**values)  # type: ignore[arg-type]


def authorization() -> AuthorizationEvidence:
    return actor().evidence


def test_models_are_frozen_and_extra_forbid() -> None:
    item = aggregate()
    with pytest.raises(ValidationError):
        WorkProductAggregate.model_validate(
            {**item.model_dump(), "unexpected": "value"}
        )
    with pytest.raises(ValidationError):
        item.title = "Changed"  # type: ignore[misc]


def test_exact_content_digest_is_enforced() -> None:
    with pytest.raises(ValidationError, match="content hash"):
        version(content_sha256="f" * 64)


def test_one_active_current_version_is_required() -> None:
    current = version()
    second = version(
        version_id=uid(3),
        version_number=2,
        content="Replacement.",
        content_sha256=content_sha256("Replacement."),
    )
    with pytest.raises(ValidationError, match="exactly one active"):
        aggregate(versions=(current, second), current_version_id=second.version_id)


def test_superseded_version_requires_transition_time() -> None:
    with pytest.raises(ValidationError, match="transition time"):
        version(status=VersionStatus.SUPERSEDED)
    item = version(status=VersionStatus.SUPERSEDED, superseded_at=T0)
    assert item.status is VersionStatus.SUPERSEDED


def test_version_numbers_must_be_contiguous() -> None:
    old = version(status=VersionStatus.SUPERSEDED, superseded_at=T0)
    current = version(
        version_id=uid(4),
        version_number=3,
        content="Current.",
        content_sha256=content_sha256("Current."),
    )
    with pytest.raises(ValidationError, match="contiguous"):
        aggregate(versions=(old, current), current_version_id=current.version_id)


def test_aggregate_rejects_dangling_exact_version_bindings() -> None:
    item = version()
    grounding = SentenceGroundingRecord(
        grounding_id=uid(20),
        binding=item.binding.model_copy(update={"work_product_version_id": uid(999)}),
        sentence_key=item.content_sha256,
        claim_id=uid(21),
        source_span_start=0,
        source_span_end=len(item.content),
        grounded_by_principal_id=DRAFTER,
        grounded_at=T0,
    )
    with pytest.raises(ValidationError, match="exact Work Product version"):
        aggregate(groundings=(grounding,))


def test_authorization_evidence_rejects_mismatched_purpose_and_window() -> None:
    evidence = authorization()
    with pytest.raises(ValidationError, match="capability and purpose"):
        AuthorizationEvidence.model_validate(
            {**evidence.model_dump(), "purpose": "human_approval"}
        )
    with pytest.raises(ValidationError, match="expiry"):
        AuthorizationEvidence.model_validate(
            {**evidence.model_dump(), "expires_at": evidence.authorized_at}
        )
    with pytest.raises(ValidationError, match="duplicates"):
        AuthorizationEvidence.model_validate(
            {
                **evidence.model_dump(),
                "ancestor_credential_digests": (evidence.credential_digest,),
            }
        )


def test_pending_approval_cannot_carry_decision_evidence() -> None:
    item = version()
    with pytest.raises(ValidationError, match="pending Approval"):
        ApprovalRecord(
            approval_id=uid(10),
            binding=item.binding,
            validation_id=uid(11),
            status=ApprovalStatus.PENDING,
            requested_by_principal_id=DRAFTER,
            requested_at=T0,
            request_policy_revision="sklegal-authz/v1",
            request_authorization=authorization(),
            reviewer_principal_id=DRAFTER,
        )


def test_decided_approval_requires_complete_evidence() -> None:
    item = version()
    with pytest.raises(ValidationError, match="exact reviewer evidence"):
        ApprovalRecord(
            approval_id=uid(10),
            binding=item.binding,
            validation_id=uid(11),
            status=ApprovalStatus.APPROVED,
            requested_by_principal_id=DRAFTER,
            requested_at=T0,
            request_policy_revision="sklegal-authz/v1",
            request_authorization=authorization(),
            reviewer_principal_id=DRAFTER,
        )


def test_revoked_approval_requires_attributable_revocation() -> None:
    item = version()
    decision = authorization().model_copy(
        update={"capability": "work_product.approve", "purpose": "human_approval"}
    )
    with pytest.raises(ValidationError, match="revocation evidence"):
        ApprovalRecord(
            approval_id=uid(10),
            binding=item.binding,
            validation_id=uid(11),
            status=ApprovalStatus.REVOKED,
            requested_by_principal_id=DRAFTER,
            requested_at=T0,
            request_policy_revision="sklegal-authz/v1",
            request_authorization=authorization(),
            reviewer_principal_id=DRAFTER,
            decided_at=T0,
            rationale="Approved exact bytes.",
            decision_policy_revision="sklegal-authz/v1",
            decision_authorization=decision,
        )


def test_canonical_digest_is_order_independent() -> None:
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    assert len(canonical_sha256(aggregate())) == 64


def test_source_binding_survives_round_trip() -> None:
    item = aggregate()
    restored = WorkProductAggregate.model_validate_json(
        item.model_dump_json(by_alias=True)
    )
    assert restored == item
    assert restored.current_version.source.source_locator.startswith(
        "public-synthetic://"
    )


def test_public_synthetic_fixture_is_the_python_and_react_contract() -> None:
    path = (
        Path(__file__).parents[2]
        / "fixtures/mvp/fragments/work_products/public-synthetic-work-product-v1.json"
    )
    aggregate = WorkProductAggregate.model_validate_json(path.read_text())

    assert aggregate.status.value == "approved"
    assert aggregate.current_version.content_sha256 == content_sha256(
        aggregate.current_version.content
    )
    assert (
        aggregate.groundings[0].sentence_key == aggregate.current_version.content_sha256
    )
    assert aggregate.approvals[0].request_authorization.capability == (
        "work_product.draft"
    )
    assert aggregate.approvals[0].decision_authorization is not None
    assert aggregate.approvals[0].decision_authorization.capability == (
        "work_product.approve"
    )


def test_times_must_be_monotonic() -> None:
    with pytest.raises(ValidationError, match="cannot precede"):
        aggregate(updated_at=T0 - timedelta(seconds=1))
