"""Strict SKLegal capability and authorization value objects."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Never, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_serializer,
    model_validator,
)

SCHEMA_VERSION: Literal["sklegal-capability/v1"] = "sklegal-capability/v1"
VERIFIER_POLICY_VERSION: Literal["sklegal-authz/v1"] = "sklegal-authz/v1"
MAX_TTL_SECONDS = 3600
MAX_DELEGATION_DEPTH = 2

OpaqueName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:/@-]*$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Fingerprint = Annotated[
    str, StringConstraints(pattern=r"^[0-9A-F]{40}([0-9A-F]{24})?$")
]


class StrictValue(BaseModel):
    """Immutable, extra-forbid model used at the authorization boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class PrincipalType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"
    CONNECTOR = "connector"


class Audience(StrEnum):
    API = "sklegal.api"
    TOOL = "sklegal.tool"
    MODEL = "sklegal.model"
    CONNECTOR = "sklegal.connector"


class Capability(StrEnum):
    TENANT_READ = "tenant.read"
    TENANT_ADMIN = "tenant.admin"
    CLIENT_READ = "client.read"
    CLIENT_MANAGE = "client.manage"
    MATTER_READ = "matter.read"
    MATTER_MANAGE = "matter.manage"
    MATTER_CONFLICT_REVIEW = "matter.conflict.review"
    MATTER_WALL_MANAGE = "matter.wall.manage"
    EVIDENCE_READ = "evidence.read"
    EVIDENCE_MANAGE = "evidence.manage"
    CORPUS_SEARCH = "corpus.search"
    CORPUS_ARTIFACT_READ = "corpus.artifact.read"
    CORPUS_INGEST_SUBMIT = "corpus.ingest.submit"
    CLAIM_PROPOSE = "claim.propose"
    CLAIM_REVIEW = "claim.review"
    WORK_PRODUCT_DRAFT = "work_product.draft"
    WORK_PRODUCT_APPROVE = "work_product.approve"
    ACTION_EMAIL_PREPARE = "action.email.prepare"
    ACTION_EMAIL_DISPATCH = "action.email.dispatch"
    ACTION_FILING_PREPARE = "action.filing.prepare"
    ACTION_FILING_DISPATCH = "action.filing.dispatch"
    ACTION_SERVICE_PREPARE = "action.service.prepare"
    ACTION_SERVICE_DISPATCH = "action.service.dispatch"
    ACTION_CALENDAR_PREPARE = "action.calendar.prepare"
    ACTION_CALENDAR_DISPATCH = "action.calendar.dispatch"
    AUDIT_READ = "audit.read"


class Operation(StrEnum):
    READ = "read"
    ADMIN = "admin"
    MANAGE = "manage"
    REVIEW = "review"
    SEARCH = "search"
    SUBMIT = "submit"
    PROPOSE = "propose"
    DRAFT = "draft"
    APPROVE = "approve"
    PREPARE = "prepare"
    DISPATCH = "dispatch"


class Purpose(StrEnum):
    TENANT_ADMINISTRATION = "tenant_administration"
    CLIENT_SERVICE = "client_service"
    MATTER_MANAGEMENT = "matter_management"
    CONFLICT_REVIEW = "conflict_review"
    INFORMATION_BARRIER_ADMIN = "information_barrier_admin"
    EVIDENCE_REVIEW = "evidence_review"
    LEGAL_RESEARCH = "legal_research"
    ISOLATED_QUARANTINE_REVIEW = "isolated_quarantine_review"
    CORPUS_INGESTION = "corpus_ingestion"
    CLAIM_DEVELOPMENT = "claim_development"
    CLAIM_REVIEW = "claim_review"
    WORK_PRODUCT_PREPARATION = "work_product_preparation"
    HUMAN_APPROVAL = "human_approval"
    EXTERNAL_ACTION_PREPARATION = "external_action_preparation"
    EXTERNAL_ACTION_DISPATCH = "external_action_dispatch"
    AUDIT_REVIEW = "audit_review"


class ModelRoute(StrEnum):
    LOCAL_QWEN = "local_qwen"
    OPENAI = "openai"


class ResourceType(StrEnum):
    TENANT = "tenant"
    CLIENT = "client"
    MATTER = "matter"
    EVIDENCE_ITEM = "evidence_item"
    CORPUS_INDEX = "corpus_index"
    CORPUS_ARTIFACT = "corpus_artifact"
    CORPUS_INGEST_REQUEST = "corpus_ingest_request"
    CLAIM = "claim"
    WORK_PRODUCT = "work_product"
    ACTION = "action"
    AUDIT = "audit"


class DecisionReason(StrEnum):
    ALLOW = "allow"
    MISSING_CREDENTIAL = "missing_credential"
    MALFORMED_CREDENTIAL = "malformed_credential"
    UNSIGNED_CREDENTIAL = "unsigned_credential"
    INVALID_SIGNATURE = "invalid_signature"
    UNTRUSTED_ISSUER = "untrusted_issuer"
    POLICY_MISMATCH = "policy_mismatch"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    PRINCIPAL_UNBOUND = "principal_unbound"
    PRINCIPAL_INACTIVE = "principal_inactive"
    PRINCIPAL_REBOUND = "principal_rebound"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    TTL_EXCEEDED = "ttl_exceeded"
    REVOKED = "revoked"
    ANCESTOR_REVOKED = "ancestor_revoked"
    ANCESTOR_EXPIRED = "ancestor_expired"
    REPLAYED = "replayed"
    WRONG_PRINCIPAL = "wrong_principal"
    WRONG_PRINCIPAL_TYPE = "wrong_principal_type"
    WRONG_AUDIENCE = "wrong_audience"
    WRONG_CAPABILITY = "wrong_capability"
    WRONG_TENANT = "wrong_tenant"
    WRONG_MATTER = "wrong_matter"
    WRONG_RESOURCE = "wrong_resource"
    WRONG_OPERATION = "wrong_operation"
    WRONG_PURPOSE = "wrong_purpose"
    WRONG_MODEL_ROUTE = "wrong_model_route"
    WRONG_WORKFLOW = "wrong_workflow"
    WRONG_TARGET = "wrong_target"
    OVER_DELEGATED = "over_delegated"
    DELEGATION_CHAIN_INVALID = "delegation_chain_invalid"
    AUDIT_UNAVAILABLE = "audit_unavailable"


class PrincipalContext(StrictValue):
    """Identity proven by authentication, never by request content."""

    principal_id: UUID
    principal_type: PrincipalType
    subject: OpaqueName
    tenant_id: UUID


class CapabilityRule(StrictValue):
    operation: Operation
    resource_type: ResourceType
    purposes: frozenset[Purpose]
    matter_required: bool


CAPABILITY_RULES: dict[Capability, CapabilityRule] = {
    Capability.TENANT_READ: CapabilityRule(
        operation=Operation.READ,
        resource_type=ResourceType.TENANT,
        purposes=frozenset({Purpose.TENANT_ADMINISTRATION}),
        matter_required=False,
    ),
    Capability.TENANT_ADMIN: CapabilityRule(
        operation=Operation.ADMIN,
        resource_type=ResourceType.TENANT,
        purposes=frozenset({Purpose.TENANT_ADMINISTRATION}),
        matter_required=False,
    ),
    Capability.CLIENT_READ: CapabilityRule(
        operation=Operation.READ,
        resource_type=ResourceType.CLIENT,
        purposes=frozenset({Purpose.CLIENT_SERVICE}),
        matter_required=False,
    ),
    Capability.CLIENT_MANAGE: CapabilityRule(
        operation=Operation.MANAGE,
        resource_type=ResourceType.CLIENT,
        purposes=frozenset({Purpose.CLIENT_SERVICE}),
        matter_required=False,
    ),
    Capability.MATTER_READ: CapabilityRule(
        operation=Operation.READ,
        resource_type=ResourceType.MATTER,
        purposes=frozenset({Purpose.MATTER_MANAGEMENT}),
        matter_required=True,
    ),
    Capability.MATTER_MANAGE: CapabilityRule(
        operation=Operation.MANAGE,
        resource_type=ResourceType.MATTER,
        purposes=frozenset({Purpose.MATTER_MANAGEMENT}),
        matter_required=True,
    ),
    Capability.MATTER_CONFLICT_REVIEW: CapabilityRule(
        operation=Operation.REVIEW,
        resource_type=ResourceType.MATTER,
        purposes=frozenset({Purpose.CONFLICT_REVIEW}),
        matter_required=True,
    ),
    Capability.MATTER_WALL_MANAGE: CapabilityRule(
        operation=Operation.MANAGE,
        resource_type=ResourceType.MATTER,
        purposes=frozenset({Purpose.INFORMATION_BARRIER_ADMIN}),
        matter_required=True,
    ),
    Capability.EVIDENCE_READ: CapabilityRule(
        operation=Operation.READ,
        resource_type=ResourceType.EVIDENCE_ITEM,
        purposes=frozenset({Purpose.EVIDENCE_REVIEW}),
        matter_required=True,
    ),
    Capability.EVIDENCE_MANAGE: CapabilityRule(
        operation=Operation.MANAGE,
        resource_type=ResourceType.EVIDENCE_ITEM,
        purposes=frozenset({Purpose.EVIDENCE_REVIEW}),
        matter_required=True,
    ),
    Capability.CORPUS_SEARCH: CapabilityRule(
        operation=Operation.SEARCH,
        resource_type=ResourceType.CORPUS_INDEX,
        purposes=frozenset({Purpose.LEGAL_RESEARCH}),
        matter_required=True,
    ),
    Capability.CORPUS_ARTIFACT_READ: CapabilityRule(
        operation=Operation.READ,
        resource_type=ResourceType.CORPUS_ARTIFACT,
        purposes=frozenset(
            {Purpose.LEGAL_RESEARCH, Purpose.ISOLATED_QUARANTINE_REVIEW}
        ),
        matter_required=True,
    ),
    Capability.CORPUS_INGEST_SUBMIT: CapabilityRule(
        operation=Operation.SUBMIT,
        resource_type=ResourceType.CORPUS_INGEST_REQUEST,
        purposes=frozenset({Purpose.CORPUS_INGESTION}),
        matter_required=True,
    ),
    Capability.CLAIM_PROPOSE: CapabilityRule(
        operation=Operation.PROPOSE,
        resource_type=ResourceType.CLAIM,
        purposes=frozenset({Purpose.CLAIM_DEVELOPMENT}),
        matter_required=True,
    ),
    Capability.CLAIM_REVIEW: CapabilityRule(
        operation=Operation.REVIEW,
        resource_type=ResourceType.CLAIM,
        purposes=frozenset({Purpose.CLAIM_REVIEW}),
        matter_required=True,
    ),
    Capability.WORK_PRODUCT_DRAFT: CapabilityRule(
        operation=Operation.DRAFT,
        resource_type=ResourceType.WORK_PRODUCT,
        purposes=frozenset({Purpose.WORK_PRODUCT_PREPARATION}),
        matter_required=True,
    ),
    Capability.WORK_PRODUCT_APPROVE: CapabilityRule(
        operation=Operation.APPROVE,
        resource_type=ResourceType.WORK_PRODUCT,
        purposes=frozenset({Purpose.HUMAN_APPROVAL}),
        matter_required=True,
    ),
    Capability.ACTION_EMAIL_PREPARE: CapabilityRule(
        operation=Operation.PREPARE,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_PREPARATION}),
        matter_required=True,
    ),
    Capability.ACTION_EMAIL_DISPATCH: CapabilityRule(
        operation=Operation.DISPATCH,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_DISPATCH}),
        matter_required=True,
    ),
    Capability.ACTION_FILING_PREPARE: CapabilityRule(
        operation=Operation.PREPARE,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_PREPARATION}),
        matter_required=True,
    ),
    Capability.ACTION_FILING_DISPATCH: CapabilityRule(
        operation=Operation.DISPATCH,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_DISPATCH}),
        matter_required=True,
    ),
    Capability.ACTION_SERVICE_PREPARE: CapabilityRule(
        operation=Operation.PREPARE,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_PREPARATION}),
        matter_required=True,
    ),
    Capability.ACTION_SERVICE_DISPATCH: CapabilityRule(
        operation=Operation.DISPATCH,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_DISPATCH}),
        matter_required=True,
    ),
    Capability.ACTION_CALENDAR_PREPARE: CapabilityRule(
        operation=Operation.PREPARE,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_PREPARATION}),
        matter_required=True,
    ),
    Capability.ACTION_CALENDAR_DISPATCH: CapabilityRule(
        operation=Operation.DISPATCH,
        resource_type=ResourceType.ACTION,
        purposes=frozenset({Purpose.EXTERNAL_ACTION_DISPATCH}),
        matter_required=True,
    ),
    Capability.AUDIT_READ: CapabilityRule(
        operation=Operation.READ,
        resource_type=ResourceType.AUDIT,
        purposes=frozenset({Purpose.AUDIT_REVIEW}),
        matter_required=False,
    ),
}

NONDELEGABLE_CAPABILITIES = frozenset(
    {
        Capability.TENANT_ADMIN,
        Capability.WORK_PRODUCT_APPROVE,
        Capability.ACTION_EMAIL_DISPATCH,
        Capability.ACTION_FILING_DISPATCH,
        Capability.ACTION_SERVICE_DISPATCH,
        Capability.ACTION_CALENDAR_DISPATCH,
        Capability.AUDIT_READ,
    }
)

MODEL_CAPABILITIES = frozenset(
    {
        Capability.EVIDENCE_READ,
        Capability.CORPUS_SEARCH,
        Capability.CORPUS_ARTIFACT_READ,
        Capability.CLAIM_PROPOSE,
        Capability.CLAIM_REVIEW,
        Capability.WORK_PRODUCT_DRAFT,
    }
)

CONNECTOR_CAPABILITIES = frozenset(
    {
        Capability.ACTION_EMAIL_DISPATCH,
        Capability.ACTION_FILING_DISPATCH,
        Capability.ACTION_SERVICE_DISPATCH,
        Capability.ACTION_CALENDAR_DISPATCH,
    }
)


class CapabilityGrant(StrictValue):
    """One capability and every signed scope constraint for one invocation."""

    TARGET_PREFIX: ClassVar[dict[Audience, str]] = {
        Audience.API: "api:",
        Audience.TOOL: "tool:",
        Audience.MODEL: "model:",
        Audience.CONNECTOR: "connector:",
    }

    audience: Audience
    target: OpaqueName
    capability: Capability
    tenant_id: UUID
    matter_id: UUID | None = None
    resource_type: ResourceType
    resource_id: OpaqueName | None = None
    resource_version: int | None = Field(default=None, ge=1)
    resource_sha256: Sha256 | None = None
    operation: Operation
    purpose: Purpose
    model_route: ModelRoute | None = None
    workflow_run_id: OpaqueName | None = None

    @model_validator(mode="after")
    def validate_closed_contract(self) -> Self:
        rule = CAPABILITY_RULES[self.capability]
        if self.operation != rule.operation:
            raise ValueError("operation does not match capability")
        if self.resource_type != rule.resource_type:
            raise ValueError("resource type does not match capability")
        if self.purpose not in rule.purposes:
            raise ValueError("purpose is not valid for capability")
        if rule.matter_required != (self.matter_id is not None):
            raise ValueError("matter constraint presence does not match capability")
        if not self.target.startswith(self.TARGET_PREFIX[self.audience]):
            raise ValueError("target does not match audience")
        if self.audience == Audience.MODEL:
            if self.capability not in MODEL_CAPABILITIES or self.model_route is None:
                raise ValueError(
                    "model audience requires an allowed model capability and route"
                )
            if self.workflow_run_id is None:
                raise ValueError("model audience requires a workflow run")
        elif self.model_route is not None:
            raise ValueError("model route is valid only for the model audience")
        if self.audience in {Audience.TOOL, Audience.CONNECTOR}:
            if self.workflow_run_id is None:
                raise ValueError("tool and connector audiences require a workflow run")
        if self.audience == Audience.CONNECTOR:
            if self.capability not in CONNECTOR_CAPABILITIES:
                raise ValueError("connector audience requires a dispatch capability")
        if self.capability in CONNECTOR_CAPABILITIES | {
            Capability.WORK_PRODUCT_APPROVE
        }:
            if (
                self.resource_id is None
                or self.resource_version is None
                or self.resource_sha256 is None
            ):
                raise ValueError(
                    "approval and dispatch require an exact artifact version"
                )
        elif self.resource_sha256 is not None and self.resource_version is None:
            raise ValueError("resource digest requires a resource version")
        return self


class DelegationClaims(StrictValue):
    depth: int = Field(ge=0, le=MAX_DELEGATION_DEPTH)
    max_depth: int = Field(ge=0, le=MAX_DELEGATION_DEPTH)
    parent_credential_digest: Sha256 | None = None

    @model_validator(mode="after")
    def validate_lineage_shape(self) -> Self:
        if self.depth == 0:
            if self.parent_credential_digest is not None:
                raise ValueError("root capability cannot name a parent")
        elif self.parent_credential_digest is None:
            raise ValueError("delegated capability requires a parent digest")
        if self.depth > self.max_depth:
            raise ValueError("delegation depth exceeds the signed maximum")
        return self


class CapabilityClaims(StrictValue):
    """The only metadata shape accepted inside a CapAuth payload."""

    schema_version: Literal["sklegal-capability/v1"] = SCHEMA_VERSION
    verifier_policy_version: Literal["sklegal-authz/v1"] = VERIFIER_POLICY_VERSION
    credential_nonce: UUID = Field(default_factory=uuid4)
    principal: PrincipalContext
    grant: CapabilityGrant
    delegation: DelegationClaims
    use_limit: Literal[1] = 1

    @model_validator(mode="after")
    def validate_principal_scope(self) -> Self:
        if self.principal.tenant_id != self.grant.tenant_id:
            raise ValueError("principal tenant and grant tenant must match")
        if self.principal.principal_type == PrincipalType.HUMAN:
            if self.grant.audience != Audience.API:
                raise ValueError("human capabilities are limited to the API audience")
        elif self.principal.principal_type == PrincipalType.AGENT:
            if self.grant.audience not in {Audience.TOOL, Audience.MODEL}:
                raise ValueError("agent capabilities are limited to tools and models")
        elif self.principal.principal_type == PrincipalType.CONNECTOR:
            if self.grant.audience != Audience.CONNECTOR:
                raise ValueError("connector principals require the connector audience")
        if self.grant.capability in NONDELEGABLE_CAPABILITIES:
            if self.delegation.depth != 0 or self.delegation.max_depth != 0:
                raise ValueError(
                    "nondelegable capability cannot carry delegation authority"
                )
        if self.principal.principal_type == PrincipalType.CONNECTOR:
            if self.delegation.max_depth != self.delegation.depth:
                raise ValueError("connector principals cannot delegate")
        return self


class AuthorizationRequest(StrictValue):
    """Exact protected invocation derived by trusted boundary code."""

    principal: PrincipalContext
    grant: CapabilityGrant
    correlation_id: UUID

    @model_validator(mode="after")
    def validate_request_scope(self) -> Self:
        if self.principal.tenant_id != self.grant.tenant_id:
            raise ValueError("request principal and grant tenant must match")
        return self


class PrincipalPolicyReference(StrictValue):
    """Safe current-policy evidence for one distinct chain principal."""

    principal_id: UUID
    revision: Sha256


class AuthorizationDecision(StrictValue):
    """Sanitized decision safe for audit transport and workflow references."""

    decision_id: UUID
    correlation_id: UUID
    allow: bool
    reason_code: DecisionReason
    credential_digest: Sha256 | None = None
    principal_id: UUID
    tenant_id: UUID
    matter_id: UUID | None = None
    capability: Capability
    audience: Audience
    target: OpaqueName
    resource_type: ResourceType
    resource_id: OpaqueName | None = None
    resource_version: int | None = Field(default=None, ge=1)
    resource_sha256: Sha256 | None = None
    operation: Operation
    purpose: Purpose
    model_route: ModelRoute | None = None
    workflow_run_id: OpaqueName | None = None
    delegation_depth: int | None = Field(default=None, ge=0, le=MAX_DELEGATION_DEPTH)
    ancestor_credential_digests: tuple[Sha256, ...] = ()
    verifier_policy_version: Literal["sklegal-authz/v1"] = VERIFIER_POLICY_VERSION
    trusted_issuer_policy_revision: Sha256 | None = None
    principal_policy_revisions: tuple[PrincipalPolicyReference, ...] = ()
    revocation_revision: Sha256 | None = None
    signature_cache_hit: bool = False

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.allow != (self.reason_code == DecisionReason.ALLOW):
            raise ValueError("allow flag and reason code disagree")
        return self


class AuthorizedContext(StrictValue):
    """Safe result passed to a protected handler after one-time reservation."""

    decision: AuthorizationDecision
    principal: PrincipalContext
    grant: CapabilityGrant
    credential_expires_at: datetime
    principal_chain: tuple[PrincipalContext, ...]

    @model_validator(mode="after")
    def validate_request_local_context(self) -> Self:
        if (
            self.credential_expires_at.tzinfo is None
            or self.credential_expires_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("authorized-context expiry must use UTC offset zero")
        if not self.decision.allow or self.decision.reason_code != DecisionReason.ALLOW:
            raise ValueError("authorized context requires an allow decision")
        if self.decision.credential_digest is None:
            raise ValueError("authorized context requires a credential digest")
        if not self.principal_chain or self.principal_chain[-1] != self.principal:
            raise ValueError("authorized context principal chain is incomplete")
        principal_ids = tuple(item.principal_id for item in self.principal_chain)
        if len(principal_ids) != len(set(principal_ids)):
            raise ValueError("authorized context principal chain must be unique")
        if (
            tuple(
                item.principal_id for item in self.decision.principal_policy_revisions
            )
            != principal_ids
        ):
            raise ValueError("authorized context principal evidence is incomplete")
        if (
            self.decision.principal_id != self.principal.principal_id
            or self.decision.tenant_id != self.principal.tenant_id
            or self.decision.matter_id != self.grant.matter_id
            or self.decision.capability != self.grant.capability
            or self.decision.audience != self.grant.audience
            or self.decision.target != self.grant.target
            or self.decision.resource_type != self.grant.resource_type
            or self.decision.resource_id != self.grant.resource_id
            or self.decision.resource_version != self.grant.resource_version
            or self.decision.resource_sha256 != self.grant.resource_sha256
            or self.decision.operation != self.grant.operation
            or self.decision.purpose != self.grant.purpose
            or self.decision.model_route != self.grant.model_route
            or self.decision.workflow_run_id != self.grant.workflow_run_id
        ):
            raise ValueError("authorized context disagrees with its exact grant")
        return self

    @model_serializer
    def reject_serialization(self) -> Any:
        raise TypeError("authorized context is request-local and nonserializable")

    def __reduce__(self) -> Never:
        raise TypeError("authorized context is request-local and nonserializable")
