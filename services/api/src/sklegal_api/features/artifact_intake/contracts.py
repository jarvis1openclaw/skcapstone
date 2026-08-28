"""Frozen HTTP and service DTOs for the artifact-intake lane."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=512)]
ShortToken = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:@/-]{0,254}$")]

Classification = Literal[
    "public",
    "internal",
    "confidential",
    "privileged_work_product",
    "highly_restricted",
]
PrivilegeState = Literal["not_privileged", "privilege_claimed", "privilege_reviewed"]
ScanState = Literal["pending", "clean", "unsafe", "failed"]
QuarantineState = Literal["quarantined", "released", "rejected"]
ExtractionState = Literal["not_requested", "pending", "complete", "failed"]
ReviewState = Literal[
    "proposed", "accepted", "changes_requested", "rejected", "superseded"
]
ArtifactKind = Literal["original", "derived"]
DerivationKind = Literal["text_extraction", "ocr", "transcript", "human_correction"]
LinkTargetType = Literal[
    "matter_event",
    "communication",
    "fact_assertion",
    "evidence_item",
    "issue",
    "claim",
    "element",
    "task",
    "work_product",
]

_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,126}/[a-z0-9][a-z0-9.+-]{0,126}$")


class ArtifactContractModel(BaseModel):
    """Strict camel-case wire model shared by this feature only."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class ArtifactSourceInput(ArtifactContractModel):
    """Opaque source identity supplied by the bounded synthetic adapter."""

    source_system: Literal["public_synthetic", "hammertime_contract"]
    source_identity: ShortToken
    source_version: ShortToken
    observed_at: datetime


class OriginalArtifactInput(ArtifactContractModel):
    """Original bytes bound to exact request metadata.

    The base64 material is accepted only at the request boundary. Read models,
    audit facts, outbox messages, and persistence metadata never expose it.
    """

    filename: BoundedText
    media_type: BoundedText
    byte_count: int = Field(ge=0, le=100_000_000)
    content_sha256: Sha256
    content_base64: str = Field(min_length=1, max_length=140_000_000, repr=False)

    @model_validator(mode="after")
    def validate_original(self) -> Self:
        if self.filename != self.filename.rsplit("/", 1)[-1] or "\\" in self.filename:
            raise ValueError("filename must not contain a path")
        if self.filename in {".", ".."} or ".." in self.filename.split("/"):
            raise ValueError("filename must not contain a path")
        if _MEDIA_TYPE.fullmatch(self.media_type) is None:
            raise ValueError("media type is invalid")
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("content base64 is invalid") from None
        if len(content) != self.byte_count:
            raise ValueError("byte count does not match original bytes")
        if hashlib.sha256(content).hexdigest() != self.content_sha256:
            raise ValueError("content sha256 does not match original bytes")
        return self

    def decoded_bytes(self) -> bytes:
        """Return request-local bytes for the scanner and storage adapter."""

        return base64.b64decode(self.content_base64, validate=True)


class DerivationRequest(ArtifactContractModel):
    kind: Literal["text_extraction", "ocr", "transcript"]
    tool_name: ShortToken
    tool_version: ShortToken
    output_media_type: BoundedText

    @model_validator(mode="after")
    def validate_output_media_type(self) -> Self:
        if _MEDIA_TYPE.fullmatch(self.output_media_type) is None:
            raise ValueError("output media type is invalid")
        return self


class ToolEvidenceRead(ArtifactContractModel):
    tool_name: ShortToken
    tool_version: ShortToken
    operation: DerivationKind
    input_sha256: Sha256
    output_sha256: Sha256
    route_id: ShortToken | None = None
    agent_run_id: UUID | None = None


class ProposedRecordLinkInput(ArtifactContractModel):
    target_type: LinkTargetType
    target_id: UUID
    rationale: BoundedText
    review_state: Literal["proposed"] = "proposed"


class ProposedRecordLinkRead(ArtifactContractModel):
    link_id: UUID
    target_type: LinkTargetType
    target_id: UUID
    rationale: BoundedText
    proposed_by_principal_id: UUID
    proposed_at: datetime
    review_state: ReviewState = "proposed"
    reviewed_by_principal_id: UUID | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_review(self) -> Self:
        reviewed = self.review_state != "proposed"
        if reviewed != (
            self.reviewed_by_principal_id is not None and self.reviewed_at is not None
        ):
            raise ValueError("review attribution and state disagree")
        return self


class ArtifactIntakeCommand(ArtifactContractModel):
    source: ArtifactSourceInput
    original: OriginalArtifactInput
    acquisition_method: Literal[
        "synthetic_adapter", "user_upload", "document_import", "recording_import"
    ]
    classification: Literal["public"]
    privilege_state: Literal["not_privileged"]
    retention_policy_id: UUID
    legal_hold_ids: tuple[UUID, ...] = ()
    ethical_wall_ids: tuple[UUID, ...] = ()
    requested_derivations: tuple[DerivationRequest, ...] = ()
    proposed_links: tuple[ProposedRecordLinkInput, ...] = ()

    @model_validator(mode="after")
    def validate_public_synthetic_boundary(self) -> Self:
        if self.source.source_system == "hammertime_contract" and (
            self.acquisition_method != "synthetic_adapter"
        ):
            raise ValueError("HammerTime contract input must use the synthetic adapter")
        kinds = tuple(item.kind for item in self.requested_derivations)
        if len(kinds) != len(set(kinds)):
            raise ValueError("a derivation kind may be requested only once")
        if len(self.legal_hold_ids) != len(set(self.legal_hold_ids)):
            raise ValueError("legal hold identifiers must be unique")
        if len(self.ethical_wall_ids) != len(set(self.ethical_wall_ids)):
            raise ValueError("ethical wall identifiers must be unique")
        return self


class ArtifactCorrectionCommand(ArtifactContractModel):
    target_artifact_id: UUID
    expected_projection_revision: int = Field(ge=1)
    corrected: OriginalArtifactInput
    reason: BoundedText
    tool_name: ShortToken = "human-review"
    tool_version: ShortToken = "1"


class ArtifactReviewCommand(ArtifactContractModel):
    artifact_id: UUID
    expected_projection_revision: int = Field(ge=1)
    decision: Literal["accepted", "changes_requested", "rejected"]
    rationale: BoundedText


class ArtifactSupersessionCommand(ArtifactContractModel):
    artifact_id: UUID
    successor_artifact_id: UUID
    expected_projection_revision: int = Field(ge=1)
    reason: BoundedText

    @model_validator(mode="after")
    def validate_distinct_artifacts(self) -> Self:
        if self.artifact_id == self.successor_artifact_id:
            raise ValueError("a supersession must name a different successor")
        return self


class ArtifactCustodyEventRead(ArtifactContractModel):
    custody_event_id: UUID
    action: Literal["acquired", "duplicate_observed", "derived", "corrected"]
    custodian_principal_id: UUID
    occurred_at: datetime
    source_identity: ShortToken
    idempotency_key_sha256: Sha256


class ArtifactScanRead(ArtifactContractModel):
    scan_id: UUID
    scanner_name: ShortToken
    scanner_version: ShortToken
    signature_revision: Sha256
    state: ScanState
    scanned_at: datetime
    detail_code: Literal["clean", "pending", "unsafe", "scanner_failed"]


class ArtifactDerivationRead(ArtifactContractModel):
    parent_artifact_id: UUID
    child_artifact_id: UUID
    kind: DerivationKind
    tool_evidence: ToolEvidenceRead
    created_at: datetime

    @model_validator(mode="after")
    def validate_edge(self) -> Self:
        if self.parent_artifact_id == self.child_artifact_id:
            raise ValueError("artifact lineage cannot contain a self edge")
        return self


class ArtifactCorrectionRead(ArtifactContractModel):
    correction_id: UUID
    target_artifact_id: UUID
    corrected_artifact_id: UUID
    reason: BoundedText
    corrected_by_principal_id: UUID
    corrected_at: datetime

    @model_validator(mode="after")
    def validate_correction(self) -> Self:
        if self.target_artifact_id == self.corrected_artifact_id:
            raise ValueError("a correction must create a new derived artifact")
        return self


class ArtifactSupersessionRead(ArtifactContractModel):
    supersession_id: UUID
    superseded_artifact_id: UUID
    successor_artifact_id: UUID
    reason: BoundedText
    recorded_by_principal_id: UUID
    recorded_at: datetime

    @model_validator(mode="after")
    def validate_successor(self) -> Self:
        if self.superseded_artifact_id == self.successor_artifact_id:
            raise ValueError("a supersession must name a different successor")
        return self


class ArtifactGovernanceRead(ArtifactContractModel):
    classification: Classification
    privilege_state: PrivilegeState
    retention_policy_id: UUID
    legal_hold_ids: tuple[UUID, ...]
    ethical_wall_ids: tuple[UUID, ...]
    policy_decision_id: UUID
    policy_revision: Sha256


class ArtifactRead(ArtifactContractModel):
    artifact_id: UUID
    tenant_id: UUID
    matter_id: UUID
    artifact_kind: ArtifactKind
    filename: BoundedText
    media_type: BoundedText
    byte_count: int = Field(ge=0)
    content_sha256: Sha256
    original_sha256: Sha256
    source: ArtifactSourceInput
    acquisition_method: str
    storage_locator: ShortToken
    quarantine_state: QuarantineState
    scan: ArtifactScanRead
    extraction_state: ExtractionState
    parent_artifact_id: UUID | None = None
    duplicate_of_artifact_id: UUID | None = None
    derivation: ArtifactDerivationRead | None = None
    child_artifact_ids: tuple[UUID, ...] = ()
    custody: tuple[ArtifactCustodyEventRead, ...]
    governance: ArtifactGovernanceRead
    proposed_links: tuple[ProposedRecordLinkRead, ...] = ()
    review_state: ReviewState = "proposed"
    reviewed_by_principal_id: UUID | None = None
    reviewed_at: datetime | None = None
    corrections: tuple[ArtifactCorrectionRead, ...] = ()
    supersession: ArtifactSupersessionRead | None = None
    projection_revision: int = Field(ge=1)
    lineage_revision: Sha256
    custody_revision: Sha256
    projection_sha256: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.artifact_kind == "original":
            if self.parent_artifact_id is not None or self.derivation is not None:
                raise ValueError("an original artifact cannot have a parent")
            if self.content_sha256 != self.original_sha256:
                raise ValueError("an original artifact owns its original hash")
        else:
            if self.parent_artifact_id is None or self.derivation is None:
                raise ValueError("a derived artifact requires lineage")
            if self.derivation.parent_artifact_id != self.parent_artifact_id:
                raise ValueError("derivation parent does not match the artifact parent")
            if self.derivation.child_artifact_id != self.artifact_id:
                raise ValueError("derivation child does not match the artifact")
            if self.derivation.tool_evidence.output_sha256 != self.content_sha256:
                raise ValueError("derivation output hash does not match the artifact")
        if self.duplicate_of_artifact_id == self.artifact_id:
            raise ValueError("an artifact cannot duplicate itself")
        reviewed = self.review_state != "proposed"
        if reviewed != (
            self.reviewed_by_principal_id is not None and self.reviewed_at is not None
        ):
            raise ValueError("artifact review attribution and state disagree")
        return self


class ArtifactLineageRead(ArtifactContractModel):
    original_artifact_id: UUID
    artifacts: tuple[ArtifactRead, ...]
    edges: tuple[ArtifactDerivationRead, ...]
    lineage_revision: Sha256

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        artifacts = {artifact.artifact_id: artifact for artifact in self.artifacts}
        identifiers = set(artifacts)
        if len(identifiers) != len(self.artifacts):
            raise ValueError("lineage artifact identifiers must be unique")
        if self.original_artifact_id not in identifiers:
            raise ValueError("lineage does not contain its original artifact")
        original = artifacts[self.original_artifact_id]
        if original.artifact_kind != "original":
            raise ValueError("lineage root must be an original artifact")
        if any(
            artifact.original_sha256 != original.content_sha256
            for artifact in self.artifacts
        ):
            raise ValueError("lineage original hash is disconnected from its root")
        parents: dict[UUID, UUID] = {}
        for edge in self.edges:
            if edge.parent_artifact_id not in identifiers:
                raise ValueError("lineage edge parent is absent")
            if edge.child_artifact_id not in identifiers:
                raise ValueError("lineage edge child is absent")
            if edge.child_artifact_id in parents:
                raise ValueError("a derived artifact must have one parent")
            parent_artifact = artifacts[edge.parent_artifact_id]
            child_artifact = artifacts[edge.child_artifact_id]
            if (
                child_artifact.artifact_kind != "derived"
                or child_artifact.derivation != edge
            ):
                raise ValueError("lineage edge does not match its derived artifact")
            if edge.tool_evidence.input_sha256 != parent_artifact.content_sha256:
                raise ValueError("lineage input hash does not match its parent")
            if edge.tool_evidence.output_sha256 != child_artifact.content_sha256:
                raise ValueError("lineage output hash does not match its child")
            parents[edge.child_artifact_id] = edge.parent_artifact_id
        derived_ids = {
            artifact.artifact_id
            for artifact in self.artifacts
            if artifact.artifact_kind == "derived"
        }
        if set(parents) != derived_ids:
            raise ValueError("every derived artifact must have one lineage edge")
        for child_id in parents:
            seen: set[UUID] = set()
            cursor = child_id
            while cursor in parents:
                if cursor in seen:
                    raise ValueError("artifact lineage contains a cycle")
                seen.add(cursor)
                cursor = parents[cursor]
            if cursor != self.original_artifact_id:
                raise ValueError("derived artifact is disconnected from the root")
        return self


class ArtifactIntakeReceipt(ArtifactContractModel):
    artifact: ArtifactRead
    derived_artifacts: tuple[ArtifactRead, ...] = ()
    lineage: ArtifactLineageRead
    idempotency_key: BoundedText
    request_sha256: Sha256
    replayed: bool
    duplicate: bool
    audit_event_id: UUID
    outbox_id: UUID


class ArtifactReviewReceipt(ArtifactContractModel):
    artifact: ArtifactRead
    idempotency_key: BoundedText
    request_sha256: Sha256
    replayed: bool
    audit_event_id: UUID
    outbox_id: UUID
