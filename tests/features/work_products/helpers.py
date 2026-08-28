from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sklegal_api.features.work_products.contracts import (
    CreateWorkProductBody,
    GroundSentenceBody,
    RequestApprovalBody,
    ValidateWorkProductBody,
)
from sklegal_api.features.work_products.service import (
    WorkProductActor,
    WorkProductCapability,
    WorkProductPurpose,
    WorkProductService,
)
from sklegal_domain.claim_grounded import split_draft_sentences
from sklegal_persistence.features.work_products.models import (
    SourceBinding,
    WorkProductAggregate,
    content_sha256,
)
from sklegal_persistence.features.work_products.repository import (
    InMemoryWorkProductRepository,
)

T0 = datetime(2026, 8, 23, 18, 0, tzinfo=UTC)
TENANT = UUID("10000000-0000-4000-8000-000000000001")
MATTER = UUID("20000000-0000-4000-8000-000000000001")
OTHER_MATTER = UUID("20000000-0000-4000-8000-000000000002")
DRAFTER = UUID("30000000-0000-4000-8000-000000000001")
REVIEWER = UUID("30000000-0000-4000-8000-000000000002")
READER = UUID("30000000-0000-4000-8000-000000000003")
CLAIM = UUID("40000000-0000-4000-8000-000000000001")
POLICY = "sklegal-authz/v1"
DIGEST = "a" * 64
EMPTY_REVOCATION_REVISION = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"  # pragma: allowlist secret


def uid(value: int) -> UUID:
    return UUID(f"50000000-0000-4000-8000-{value:012d}")


class IdFactory:
    def __init__(self) -> None:
        self.value = 1

    def __call__(self) -> UUID:
        result = uid(self.value)
        self.value += 1
        return result


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def actor(
    *,
    principal_id: UUID = DRAFTER,
    capability: WorkProductCapability = "work_product.draft",
    purpose: WorkProductPurpose = "work_product_preparation",
    matter_id: UUID = MATTER,
    tenant_id: UUID = TENANT,
    decision_id: UUID | None = None,
    policy_revision: str = POLICY,
    ancestor_credential_digests: tuple[str, ...] = (),
) -> WorkProductActor:
    return WorkProductActor(
        tenant_id=tenant_id,
        principal_id=principal_id,
        authorized_matter_id=matter_id,
        decision_id=decision_id or uid(800 + int(str(principal_id)[-1])),
        correlation_id=uid(900 + int(str(principal_id)[-1])),
        capability=capability,
        purpose=purpose,
        policy_revision=policy_revision,
        revocation_revision=EMPTY_REVOCATION_REVISION,
        credential_digest=DIGEST,
        ancestor_credential_digests=ancestor_credential_digests,
        authorized_at=T0 - timedelta(seconds=1),
        expires_at=T0 + timedelta(hours=1),
    )


def approval_actor(**changes: object) -> WorkProductActor:
    values: dict[str, object] = {
        "principal_id": REVIEWER,
        "capability": "work_product.approve",
        "purpose": "human_approval",
    }
    values.update(changes)
    return actor(**values)  # type: ignore[arg-type]


def read_actor(**changes: object) -> WorkProductActor:
    values: dict[str, object] = {
        "principal_id": READER,
        "capability": "matter.read",
        "purpose": "matter_management",
    }
    values.update(changes)
    return actor(**values)  # type: ignore[arg-type]


def create_body(
    content: str = "The synthetic fact is supported.",
) -> CreateWorkProductBody:
    return CreateWorkProductBody(
        title="Public synthetic memorandum",
        work_product_kind="memo",
        content=content,
        content_sha256=content_sha256(content),
        source=SourceBinding(
            source_artifact_id=uid(700),
            source_artifact_version=1,
            source_content_sha256="c" * 64,
            source_locator="public-synthetic://wp-01/source-1",
        ),
    )


def make_service() -> tuple[
    WorkProductService, InMemoryWorkProductRepository, Clock, IdFactory
]:
    repository = InMemoryWorkProductRepository(
        memberships={
            (TENANT, MATTER, DRAFTER),
            (TENANT, MATTER, REVIEWER),
            (TENANT, MATTER, READER),
        },
        policy_revisions={(TENANT, MATTER): POLICY},
    )
    repository.add_claim(TENANT, MATTER, CLAIM)
    clock = Clock()
    ids = IdFactory()
    return (
        WorkProductService(repository=repository, clock=clock, id_factory=ids),
        repository,
        clock,
        ids,
    )


def create_product(
    service: WorkProductService,
    *,
    command: CreateWorkProductBody | None = None,
    idempotency_key: UUID = uid(100),
) -> WorkProductAggregate:
    return service.create(
        matter_id=MATTER,
        command=command or create_body(),
        idempotency_key=idempotency_key,
        actor=actor(),
    )


def ground_current(
    service: WorkProductService,
    aggregate: WorkProductAggregate,
    *,
    idempotency_key: UUID = uid(101),
) -> WorkProductAggregate:
    sentence = split_draft_sentences(aggregate.current_version.content)[0]
    return service.ground_sentence(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        command=GroundSentenceBody(
            expected_aggregate_version=aggregate.aggregate_version,
            binding=aggregate.current_version.binding,
            sentence_key=sentence.key,
            claim_id=CLAIM,
        ),
        idempotency_key=idempotency_key,
        actor=actor(),
    )


def validate_current(
    service: WorkProductService,
    aggregate: WorkProductAggregate,
    *,
    idempotency_key: UUID = uid(102),
) -> WorkProductAggregate:
    return service.validate(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        command=ValidateWorkProductBody(
            expected_aggregate_version=aggregate.aggregate_version,
            binding=aggregate.current_version.binding,
            check_ids=("content_hash", "sentence_grounding", "source_lineage"),
            rationale="Exact public synthetic checks completed.",
        ),
        idempotency_key=idempotency_key,
        actor=actor(),
    )


def request_current(
    service: WorkProductService,
    aggregate: WorkProductAggregate,
    *,
    idempotency_key: UUID = uid(103),
) -> WorkProductAggregate:
    validation = aggregate.validations[-1]
    return service.request_approval(
        matter_id=MATTER,
        work_product_id=aggregate.work_product_id,
        command=RequestApprovalBody(
            expected_aggregate_version=aggregate.aggregate_version,
            binding=aggregate.current_version.binding,
            validation_id=validation.validation_id,
        ),
        idempotency_key=idempotency_key,
        actor=actor(),
    )
