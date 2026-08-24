from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from sklegal_api.features.work_products.contracts import (
    CreateVersionBody,
    DecideApprovalBody,
    GroundSentenceBody,
    RequestApprovalBody,
    RevokeApprovalBody,
    SupersedeApprovalBody,
    ValidateWorkProductBody,
)
from sklegal_api.features.work_products.service import WorkProductServiceError
from sklegal_domain.claim_grounded import split_draft_sentences
from sklegal_persistence.features.work_products.models import (
    ApprovalStatus,
    ValidationOutcome,
    VersionBinding,
    WorkProductStatus,
    content_sha256,
)

from .helpers import (
    CLAIM,
    DIGEST,
    MATTER,
    OTHER_MATTER,
    POLICY,
    T0,
    TENANT,
    actor,
    approval_actor,
    create_body,
    create_product,
    ground_current,
    make_service,
    read_actor,
    request_current,
    uid,
    validate_current,
)


def assert_error(code: str, call: object) -> None:
    with pytest.raises(WorkProductServiceError) as caught:
        call()  # type: ignore[operator]
    assert caught.value.code == code


def approve_current(service: object, aggregate: object, *, key: int = 104):
    approval = aggregate.approvals[-1]  # type: ignore[attr-defined]
    return service.decide_approval(  # type: ignore[attr-defined]
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,  # type: ignore[attr-defined]
        approval_id=approval.approval_id,
        command=DecideApprovalBody(
            expected_aggregate_version=aggregate.aggregate_version,  # type: ignore[attr-defined]
            binding=aggregate.current_version.binding,  # type: ignore[attr-defined]
            decision="approved",
            rationale="Reviewed exact public synthetic bytes.",
        ),
        idempotency_key=uid(key),
        actor=approval_actor(),
    )


def approved_product():
    service, repository, clock, ids = make_service()
    aggregate = create_product(service)
    aggregate = ground_current(service, aggregate)
    aggregate = validate_current(service, aggregate)
    aggregate = request_current(service, aggregate)
    aggregate = approve_current(service, aggregate)
    return service, repository, clock, ids, aggregate


def test_create_is_immutable_idempotent_and_atomic() -> None:
    service, repository, _, _ = make_service()
    aggregate = create_product(service)
    replay = create_product(service)
    assert replay == aggregate
    assert aggregate.current_version.content_sha256 == content_sha256(
        aggregate.current_version.content
    )
    assert len(repository.audit_events()) == 1
    assert len(repository.outbox_events()) == 1
    assert (
        repository.audit_events()[0].subject_sha256
        == repository.outbox_events()[0].payload_sha256
    )


def test_idempotency_key_reuse_with_changed_bytes_is_denied() -> None:
    service, _, _, _ = make_service()
    create_product(service)
    assert_error(
        "idempotency_key_reused",
        lambda: create_product(service, command=create_body("Changed bytes.")),
    )


def test_content_hash_mismatch_denies_before_mutation() -> None:
    service, repository, _, _ = make_service()
    command = create_body().model_copy(update={"content_sha256": "f" * 64})
    assert_error(
        "content_hash_mismatch",
        lambda: create_product(service, command=command),
    )
    assert repository.list_for_matter(TENANT, MATTER) == ()


@pytest.mark.parametrize(
    ("changed", "code"),
    [
        ({"authorized_matter_id": OTHER_MATTER}, "authorization_scope_mismatch"),
        (
            {"capability": "matter.read", "purpose": "matter_management"},
            "operation_capability_mismatch",
        ),
        ({"policy_revision": "stale-policy"}, "stale_policy_evidence"),
    ],
)
def test_authorization_negatives_deny_before_create(
    changed: dict[str, object], code: str
) -> None:
    service, repository, _, _ = make_service()
    candidate = replace(actor(), **changed)
    assert_error(
        code,
        lambda: service.create(
            matter_id=MATTER,
            command=create_body(),
            idempotency_key=uid(110),
            actor=candidate,
        ),
    )
    assert repository.list_for_matter(TENANT, MATTER) == ()


def test_cross_tenant_and_unjoined_principal_are_denied() -> None:
    service, repository, _, _ = make_service()
    other_tenant = uid(999)
    candidate = replace(actor(), tenant_id=other_tenant)
    assert_error(
        "work_product_authorization_unavailable",
        lambda: service.create(
            matter_id=MATTER,
            command=create_body(),
            idempotency_key=uid(111),
            actor=candidate,
        ),
    )
    unjoined = replace(actor(), principal_id=uid(998), decision_id=uid(997))
    assert_error(
        "matter_membership_denied",
        lambda: service.create(
            matter_id=MATTER,
            command=create_body(),
            idempotency_key=uid(112),
            actor=unjoined,
        ),
    )
    assert repository.list_for_matter(TENANT, MATTER) == ()


def test_revoked_and_expired_capabilities_are_denied() -> None:
    service, repository, clock, _ = make_service()
    candidate = actor(decision_id=uid(950))
    repository.revoke_decision(candidate.decision_id)
    assert_error(
        "capability_inactive",
        lambda: service.create(
            matter_id=MATTER,
            command=create_body(),
            idempotency_key=uid(113),
            actor=candidate,
        ),
    )
    expired = replace(actor(), expires_at=T0)
    assert_error(
        "capability_inactive",
        lambda: service.create(
            matter_id=MATTER,
            command=create_body(),
            idempotency_key=uid(114),
            actor=expired,
        ),
    )
    assert clock.now == T0


def test_grounding_is_exact_to_sentence_claim_and_version() -> None:
    service, repository, _, _ = make_service()
    aggregate = create_product(service)
    sentence = split_draft_sentences(aggregate.current_version.content)[0]
    grounded = ground_current(service, aggregate)
    record = grounded.groundings[0]
    assert record.binding == aggregate.current_version.binding
    assert (
        aggregate.current_version.content[
            record.source_span_start : record.source_span_end
        ]
        == sentence.text
    )
    assert_error(
        "sentence_already_grounded",
        lambda: service.ground_sentence(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=GroundSentenceBody(
                expected_aggregate_version=grounded.aggregate_version,
                binding=grounded.current_version.binding,
                sentence_key=sentence.key,
                claim_id=CLAIM,
            ),
            idempotency_key=uid(115),
            actor=actor(),
        ),
    )
    missing_claim = uid(996)
    assert not repository.claim_exists(TENANT, MATTER, missing_claim)


def test_unknown_sentence_and_claim_are_denied() -> None:
    service, _, _, _ = make_service()
    aggregate = create_product(service)
    base = dict(
        expected_aggregate_version=aggregate.aggregate_version,
        binding=aggregate.current_version.binding,
        sentence_key="d" * 64,
        claim_id=CLAIM,
    )
    assert_error(
        "sentence_not_found",
        lambda: service.ground_sentence(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=GroundSentenceBody(**base),
            idempotency_key=uid(116),
            actor=actor(),
        ),
    )
    sentence = split_draft_sentences(aggregate.current_version.content)[0]
    base.update(sentence_key=sentence.key, claim_id=uid(995))
    assert_error(
        "claim_not_found",
        lambda: service.ground_sentence(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=GroundSentenceBody(**base),
            idempotency_key=uid(117),
            actor=actor(),
        ),
    )


def test_validation_fails_closed_until_all_sentences_are_grounded() -> None:
    service, _, _, _ = make_service()
    aggregate = create_product(service, command=create_body("One fact. Two facts."))
    failed = validate_current(service, aggregate)
    assert failed.status is WorkProductStatus.IN_REVIEW
    assert failed.validations[-1].outcome is ValidationOutcome.FAILED
    assert len(failed.validations[-1].missing_sentence_keys) == 2


def test_validation_requires_all_named_checks() -> None:
    service, _, _, _ = make_service()
    aggregate = ground_current(service, create_product(service))
    validated = service.validate(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        command=ValidateWorkProductBody(
            expected_aggregate_version=aggregate.aggregate_version,
            binding=aggregate.current_version.binding,
            check_ids=("content_hash",),
            rationale="Partial check only.",
        ),
        idempotency_key=uid(118),
        actor=actor(),
    )
    assert validated.validations[-1].outcome is ValidationOutcome.FAILED
    assert "missing checks" in validated.validations[-1].rationale


def test_request_approval_requires_exact_passing_validation() -> None:
    service, _, _, _ = make_service()
    aggregate = validate_current(
        service, ground_current(service, create_product(service))
    )
    wrong = RequestApprovalBody(
        expected_aggregate_version=aggregate.aggregate_version,
        binding=aggregate.current_version.binding.model_copy(
            update={"content_sha256": "e" * 64}
        ),
        validation_id=aggregate.validations[-1].validation_id,
    )
    assert_error(
        "work_product_binding_mismatch",
        lambda: service.request_approval(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=wrong,
            idempotency_key=uid(119),
            actor=actor(),
        ),
    )


def test_revoked_validation_capability_denies_approval_request_without_mutation() -> (
    None
):
    service, repository, _, _ = make_service()
    aggregate = ground_current(service, create_product(service))
    validator = actor(decision_id=uid(960))
    aggregate = service.validate(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        command=ValidateWorkProductBody(
            expected_aggregate_version=aggregate.aggregate_version,
            binding=aggregate.current_version.binding,
            check_ids=("content_hash", "sentence_grounding", "source_lineage"),
            rationale="Exact public synthetic checks completed.",
        ),
        idempotency_key=uid(961),
        actor=validator,
    )
    repository.revoke_decision(validator.decision_id)
    before = len(repository.audit_events())
    assert_error(
        "validation_not_current",
        lambda: service.request_approval(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=RequestApprovalBody(
                expected_aggregate_version=aggregate.aggregate_version,
                binding=aggregate.current_version.binding,
                validation_id=aggregate.validations[-1].validation_id,
            ),
            idempotency_key=uid(962),
            actor=actor(),
        ),
    )
    assert len(repository.audit_events()) == before


def test_revoked_request_capability_denies_decision_without_mutation() -> None:
    service, repository, _, _ = make_service()
    aggregate = request_current(
        service,
        validate_current(service, ground_current(service, create_product(service))),
    )
    approval = aggregate.approvals[-1]
    repository.revoke_decision(approval.request_authorization.decision_id)
    before = len(repository.audit_events())
    assert_error(
        "approval_preconditions_stale",
        lambda: service.decide_approval(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            approval_id=approval.approval_id,
            command=DecideApprovalBody(
                expected_aggregate_version=aggregate.aggregate_version,
                binding=aggregate.current_version.binding,
                decision="approved",
                rationale="Reviewed exact public synthetic bytes.",
            ),
            idempotency_key=uid(963),
            actor=approval_actor(),
        ),
    )
    assert len(repository.audit_events()) == before


def test_approval_accept_reject_and_revocation_semantics() -> None:
    service, repository, _, _, approved = approved_product()
    approval = approved.approvals[-1]
    assert approval.status is ApprovalStatus.APPROVED
    validity = service.approval_validity(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        approval_id=approval.approval_id,
        binding=approved.current_version.binding,
        actor=read_actor(),
    )
    assert validity.valid is True
    revoked = service.revoke_approval(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        approval_id=approval.approval_id,
        command=RevokeApprovalBody(
            expected_aggregate_version=approved.aggregate_version,
            binding=approved.current_version.binding,
            rationale="Approval withdrawn after review.",
        ),
        idempotency_key=uid(120),
        actor=approval_actor(),
    )
    assert revoked.status is WorkProductStatus.VALIDATED
    assert revoked.approvals[-1].status is ApprovalStatus.REVOKED
    invalid = service.approval_validity(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        approval_id=approval.approval_id,
        binding=approved.current_version.binding,
        actor=read_actor(),
    )
    assert invalid.reason_code == "approval_revoked"
    assert len(repository.audit_events()) == len(repository.outbox_events())


def test_rejected_approval_never_becomes_valid() -> None:
    service, _, _, _ = make_service()
    aggregate = request_current(
        service,
        validate_current(service, ground_current(service, create_product(service))),
    )
    approval = aggregate.approvals[-1]
    rejected = service.decide_approval(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        approval_id=approval.approval_id,
        command=DecideApprovalBody(
            expected_aggregate_version=aggregate.aggregate_version,
            binding=aggregate.current_version.binding,
            decision="rejected",
            rationale="A specific correction is required.",
        ),
        idempotency_key=uid(121),
        actor=approval_actor(),
    )
    assert rejected.approvals[-1].status is ApprovalStatus.REJECTED
    result = service.approval_validity(
        matter_id=MATTER,
        work_product_id=rejected.work_product_id,
        approval_id=approval.approval_id,
        binding=rejected.current_version.binding,
        actor=read_actor(),
    )
    assert result.reason_code == "approval_rejected"


def test_new_version_invalidates_prior_approval_and_preserves_lineage() -> None:
    service, _, _, _, approved = approved_product()
    prior = approved.current_version
    body = CreateVersionBody(
        expected_aggregate_version=approved.aggregate_version,
        expected_current=prior.binding,
        content="The corrected synthetic fact is supported.",
        content_sha256=content_sha256("The corrected synthetic fact is supported."),
        source=create_body().source.model_copy(
            update={"source_artifact_version": 2, "source_content_sha256": "d" * 64}
        ),
    )
    edited = service.create_version(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        command=body,
        idempotency_key=uid(122),
        actor=actor(),
    )
    assert edited.versions[0].status.value == "superseded"
    assert edited.versions[0].source == prior.source
    assert edited.current_version.source.source_artifact_version == 2
    assert edited.approvals[-1].status is ApprovalStatus.SUPERSEDED
    result = service.approval_validity(
        matter_id=MATTER,
        work_product_id=edited.work_product_id,
        approval_id=approved.approvals[-1].approval_id,
        binding=prior.binding,
        actor=read_actor(),
    )
    assert result.reason_code == "approval_superseded"


def test_wrong_version_and_hash_are_denied_before_edit() -> None:
    service, repository, _, _ = make_service()
    aggregate = create_product(service)
    body = CreateVersionBody(
        expected_aggregate_version=aggregate.aggregate_version,
        expected_current=VersionBinding(
            work_product_version_id=aggregate.current_version_id,
            version_number=99,
            content_sha256=aggregate.current_version.content_sha256,
        ),
        content="Changed public synthetic content.",
        content_sha256=content_sha256("Changed public synthetic content."),
        source=create_body().source,
    )
    assert_error(
        "work_product_binding_mismatch",
        lambda: service.create_version(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=body,
            idempotency_key=uid(123),
            actor=actor(),
        ),
    )
    assert repository.get(TENANT, MATTER, aggregate.work_product_id) == aggregate


def test_comparison_is_read_only_and_exact() -> None:
    service, repository, _, _ = make_service()
    aggregate = create_product(service)
    content = "The changed synthetic fact is supported."
    edited = service.create_version(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        command=CreateVersionBody(
            expected_aggregate_version=aggregate.aggregate_version,
            expected_current=aggregate.current_version.binding,
            content=content,
            content_sha256=content_sha256(content),
            source=create_body().source,
        ),
        idempotency_key=uid(124),
        actor=actor(),
    )
    audit_count = len(repository.audit_events())
    comparison = service.compare(
        matter_id=MATTER,
        work_product_id=edited.work_product_id,
        left_version_id=edited.versions[0].version_id,
        right_version_id=edited.versions[1].version_id,
        actor=read_actor(),
    )
    assert comparison.changed is True
    assert any(line.startswith("-") for line in comparison.unified_diff)
    assert any(line.startswith("+") for line in comparison.unified_diff)
    assert len(repository.audit_events()) == audit_count


def test_policy_change_and_capability_revocation_invalidate_approval() -> None:
    service, repository, _, _, approved = approved_product()
    approval = approved.approvals[-1]
    assert approval.decision_authorization is not None
    repository.revoke_decision(approval.decision_authorization.decision_id)
    result = service.approval_validity(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        approval_id=approval.approval_id,
        binding=approved.current_version.binding,
        actor=read_actor(),
    )
    assert result.reason_code == "approval_capability_revoked"
    repository.restore_decision(approval.decision_authorization.decision_id)
    repository.revoke_decision(approval.request_authorization.decision_id)
    result = service.approval_validity(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        approval_id=approval.approval_id,
        binding=approved.current_version.binding,
        actor=read_actor(),
    )
    assert result.reason_code == "approval_capability_revoked"
    repository.restore_decision(approval.request_authorization.decision_id)
    repository.set_policy_revision(TENANT, MATTER, "new-policy")
    stale_reader = replace(read_actor(), policy_revision="new-policy")
    result = service.approval_validity(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        approval_id=approval.approval_id,
        binding=approved.current_version.binding,
        actor=stale_reader,
    )
    assert result.reason_code == "approval_policy_stale"


@pytest.mark.parametrize("boundary", ["store", "audit", "outbox"])
def test_repository_outages_leave_no_partial_create(boundary: str) -> None:
    service, repository, _, _ = make_service()
    repository.fail_on = boundary
    assert_error(
        "work_product_store_unavailable"
        if boundary != "store"
        else "work_product_store_unavailable",
        lambda: create_product(service),
    )
    repository.fail_on = None
    assert repository.list_for_matter(TENANT, MATTER) == ()
    assert repository.audit_events() == ()
    assert repository.outbox_events() == ()


def test_authorization_outage_denies_before_mutation() -> None:
    service, repository, _, _ = make_service()
    repository.fail_on = "authorization"
    assert_error(
        "work_product_authorization_unavailable",
        lambda: create_product(service),
    )
    repository.fail_on = None
    assert repository.list_for_matter(TENANT, MATTER) == ()


def test_leaf_ancestor_and_stale_snapshot_deny_before_mutation() -> None:
    ancestor = "c" * 64
    for candidate, revoked in (
        (actor(), DIGEST),
        (actor(ancestor_credential_digests=(ancestor,)), ancestor),
    ):
        service, repository, _, _ = make_service()
        repository.revoke_credential(revoked)
        assert_error(
            "capability_inactive",
            lambda candidate=candidate, service=service: service.create(
                matter_id=MATTER,
                command=create_body(),
                idempotency_key=uid(710),
                actor=candidate,
            ),
        )
        assert repository.list_for_matter(TENANT, MATTER) == ()
    service, repository, _, _ = make_service()
    assert_error(
        "capability_inactive",
        lambda: service.create(
            matter_id=MATTER,
            command=create_body(),
            idempotency_key=uid(711),
            actor=replace(actor(), revocation_revision="d" * 64),
        ),
    )
    assert repository.list_for_matter(TENANT, MATTER) == ()


def test_stale_aggregate_version_denies_mutation() -> None:
    service, repository, _, _ = make_service()
    aggregate = create_product(service)
    command = GroundSentenceBody(
        expected_aggregate_version=aggregate.aggregate_version + 1,
        binding=aggregate.current_version.binding,
        sentence_key=split_draft_sentences(aggregate.current_version.content)[0].key,
        claim_id=CLAIM,
    )
    assert_error(
        "work_product_version_conflict",
        lambda: service.ground_sentence(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            command=command,
            idempotency_key=uid(126),
            actor=actor(),
        ),
    )
    assert len(repository.audit_events()) == 1


def test_approval_supersession_links_old_and_new_exact_versions() -> None:
    service, repository, _, _, approved = approved_product()
    old_approval = approved.approvals[-1]
    content = "The replacement synthetic fact is grounded."
    aggregate = service.create_version(
        matter_id=MATTER,
        work_product_id=approved.work_product_id,
        command=CreateVersionBody(
            expected_aggregate_version=approved.aggregate_version,
            expected_current=approved.current_version.binding,
            content=content,
            content_sha256=content_sha256(content),
            source=create_body().source,
        ),
        idempotency_key=uid(130),
        actor=actor(),
    )
    aggregate = ground_current(service, aggregate, idempotency_key=uid(131))
    aggregate = validate_current(service, aggregate, idempotency_key=uid(132))
    aggregate = request_current(service, aggregate, idempotency_key=uid(133))
    aggregate = approve_current(service, aggregate, key=134)
    new_approval = aggregate.approvals[-1]
    before = len(repository.audit_events())
    assert_error(
        "superseding_approval_not_valid",
        lambda: service.supersede_approval(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            approval_id=old_approval.approval_id,
            command=SupersedeApprovalBody(
                expected_aggregate_version=aggregate.aggregate_version,
                superseding_approval_id=new_approval.approval_id,
                superseding_binding=new_approval.binding.model_copy(
                    update={"content_sha256": "e" * 64}
                ),
            ),
            idempotency_key=uid(136),
            actor=approval_actor(),
        ),
    )
    assert len(repository.audit_events()) == before
    result = service.supersede_approval(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        approval_id=old_approval.approval_id,
        command=SupersedeApprovalBody(
            expected_aggregate_version=aggregate.aggregate_version,
            superseding_approval_id=new_approval.approval_id,
            superseding_binding=new_approval.binding,
        ),
        idempotency_key=uid(135),
        actor=approval_actor(),
    )
    old = next(
        item
        for item in result.approvals
        if item.approval_id == old_approval.approval_id
    )
    assert old.status is ApprovalStatus.SUPERSEDED
    assert old.superseded_by_approval_id == new_approval.approval_id
    assert old.superseding_version_id == new_approval.binding.work_product_version_id


def test_wrong_capability_never_reaches_repository_get() -> None:
    service, repository, _, _ = make_service()
    aggregate = create_product(service)
    repository.fail_on = "store"
    assert_error(
        "operation_capability_mismatch",
        lambda: service.get(
            matter_id=MATTER,
            work_product_id=aggregate.work_product_id,
            actor=actor(),
        ),
    )


def test_credential_digest_is_not_exposed_in_audit_or_outbox() -> None:
    service, repository, _, _ = make_service()
    create_product(service)
    assert DIGEST not in repository.audit_events()[0].model_dump_json()
    assert DIGEST not in repository.outbox_events()[0].model_dump_json()


def test_clock_outside_authorization_window_denies() -> None:
    service, repository, clock, _ = make_service()
    clock.now = T0 + timedelta(hours=2)
    assert_error("capability_inactive", lambda: create_product(service))
    assert repository.list_for_matter(TENANT, MATTER) == ()


def test_actor_tenant_cannot_be_supplied_by_request_body() -> None:
    fields = type(create_body()).model_fields
    assert "tenant_id" not in fields
    assert "matter_id" not in fields
    assert "principal_id" not in fields
    assert "capability" not in fields
    assert POLICY not in create_body().model_dump_json()
