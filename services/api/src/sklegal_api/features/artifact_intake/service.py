"""Fail-closed service for idempotent Matter artifact intake and lineage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, Self, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .contracts import (
    ArtifactContractModel,
    ArtifactCorrectionCommand,
    ArtifactCorrectionRead,
    ArtifactCustodyEventRead,
    ArtifactDerivationRead,
    ArtifactGovernanceRead,
    ArtifactIntakeCommand,
    ArtifactIntakeReceipt,
    ArtifactLineageRead,
    ArtifactRead,
    ArtifactReviewCommand,
    ArtifactReviewReceipt,
    ArtifactScanRead,
    ArtifactSupersessionCommand,
    ArtifactSupersessionRead,
    DerivationRequest,
    OriginalArtifactInput,
    ProposedRecordLinkRead,
    Sha256,
    ToolEvidenceRead,
)
from .store import (
    ArtifactAuditFact,
    ArtifactOutboxMessage,
    ArtifactReceipt,
    ArtifactStore,
    ArtifactStoreUnavailable,
)
from .synthetic import (
    ArtifactAdapter,
    ArtifactAdapterUnavailable,
    ArtifactInspection,
    ArtifactStorageReceipt,
    DerivedArtifactMaterial,
)


class ArtifactServiceError(RuntimeError):
    """Sanitized error from the closed artifact operation vocabulary."""

    def __init__(
        self,
        code: Literal[
            "authentication_required",
            "access_denied",
            "validation_failed",
            "precondition_failed",
            "idempotency_conflict",
            "policy_unavailable",
            "dependency_unavailable",
            "resource_unavailable",
            "internal_error",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class ArtifactAccessContext(BaseModel):
    """Request-local trusted authorization result, never request body data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    capability: Literal["evidence.manage", "evidence.read"]
    purpose: Literal["evidence_review"]
    authorization_decision_id: UUID
    credential_expires_at: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def validate_utc(self) -> Self:
        if (
            self.credential_expires_at.tzinfo is None
            or self.credential_expires_at.utcoffset() is None
        ):
            raise ValueError("credential expiry must be timezone aware")
        return self


class ArtifactPolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    tenant_id: UUID
    matter_id: UUID
    principal_id: UUID
    operation: Literal["create", "read", "correct", "review", "supersede"]
    classification: str
    allowed: bool
    revision: Sha256
    evaluated_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (
            self.evaluated_at.tzinfo is None
            or self.evaluated_at.utcoffset() is None
            or self.valid_until.tzinfo is None
            or self.valid_until.utcoffset() is None
            or self.valid_until <= self.evaluated_at
        ):
            raise ValueError("policy validity window is invalid")
        return self


class ArtifactPolicyUnavailable(RuntimeError):
    """No current Matter policy decision can be produced."""


class ArtifactPolicy(Protocol):
    def authorize(
        self,
        *,
        context: ArtifactAccessContext,
        operation: Literal["create", "read", "correct", "review", "supersede"],
        classification: str,
        now: datetime,
    ) -> ArtifactPolicyDecision: ...


class StaticArtifactPolicy:
    """Deterministic public-synthetic policy used only by isolated composition."""

    def __init__(
        self,
        *,
        memberships: set[tuple[UUID, UUID, UUID]],
        allowed_classifications: set[str],
        revision: str,
        valid_until: datetime,
    ) -> None:
        self.memberships = memberships
        self.allowed_classifications = allowed_classifications
        self.revision = revision
        self.valid_until = valid_until
        self.available = True
        self._calls: list[tuple[str, UUID, UUID, UUID]] = []

    @property
    def calls(self) -> tuple[tuple[str, UUID, UUID, UUID], ...]:
        return tuple(self._calls)

    def authorize(
        self,
        *,
        context: ArtifactAccessContext,
        operation: Literal["create", "read", "correct", "review", "supersede"],
        classification: str,
        now: datetime,
    ) -> ArtifactPolicyDecision:
        if not self.available:
            raise ArtifactPolicyUnavailable("artifact policy unavailable")
        self._calls.append(
            (operation, context.tenant_id, context.matter_id, context.principal_id)
        )
        allowed = (
            context.tenant_id,
            context.matter_id,
            context.principal_id,
        ) in self.memberships and classification in self.allowed_classifications
        return ArtifactPolicyDecision(
            decision_id=uuid5(
                NAMESPACE_URL,
                f"artifact-policy:{self.revision}:{context.tenant_id}:"
                f"{context.matter_id}:{context.principal_id}:{operation}:"
                f"{classification}",
            ),
            tenant_id=context.tenant_id,
            matter_id=context.matter_id,
            principal_id=context.principal_id,
            operation=operation,
            classification=classification,
            allowed=allowed,
            revision=self.revision,
            evaluated_at=now,
            valid_until=self.valid_until,
        )


def _canonical_sha256(value: object) -> str:
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _request_sha256(operation: str, command: ArtifactContractModel) -> str:
    body = command.model_dump(mode="json", by_alias=True)
    original = body.get("original")
    if isinstance(original, dict):
        original.pop("contentBase64", None)
    corrected = body.get("corrected")
    if isinstance(corrected, dict):
        corrected.pop("contentBase64", None)
    return _canonical_sha256({"operation": operation, "command": body})


def _key_sha256(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


class ArtifactIntakeService:
    """Authorize, inspect, and atomically append artifact lineage records."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        policy: ArtifactPolicy,
        adapter: ArtifactAdapter,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._policy = policy
        self._adapter = adapter
        self._clock = clock

    def _authorize(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        capability: Literal["evidence.manage", "evidence.read"],
        operation: Literal["create", "read", "correct", "review", "supersede"],
        classification: str,
    ) -> ArtifactPolicyDecision:
        now = self._clock()
        if (
            context.revoked
            or context.credential_expires_at <= now
            or context.matter_id != matter_id
            or context.capability != capability
            or context.purpose != "evidence_review"
        ):
            raise ArtifactServiceError("access_denied")
        try:
            decision = self._policy.authorize(
                context=context,
                operation=operation,
                classification=classification,
                now=now,
            )
        except (ArtifactPolicyUnavailable, ValidationError):
            raise ArtifactServiceError("policy_unavailable") from None
        if decision.valid_until <= now:
            raise ArtifactServiceError("policy_unavailable")
        if (
            decision.tenant_id != context.tenant_id
            or decision.matter_id != matter_id
            or decision.principal_id != context.principal_id
            or decision.operation != operation
            or decision.classification != classification
            or decision.evaluated_at > now
            or not decision.allowed
        ):
            raise ArtifactServiceError("access_denied")
        return decision

    def _ready(self) -> None:
        try:
            self._store.ensure_ready()
        except ArtifactStoreUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None

    def _inspect(
        self,
        original: OriginalArtifactInput,
        derivations: tuple[DerivationRequest, ...],
    ) -> ArtifactInspection:
        try:
            return self._adapter.inspect(original, derivations)
        except ArtifactAdapterUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None

    @staticmethod
    def _validate_storage(
        receipt: ArtifactStorageReceipt, *, content_sha256: str, byte_count: int
    ) -> ArtifactStorageReceipt:
        if (
            receipt.content_sha256 != content_sha256
            or receipt.byte_count != byte_count
            or receipt.storage_locator != f"synthetic:sha256:{content_sha256}"
        ):
            raise ArtifactServiceError("dependency_unavailable")
        return receipt

    def _preserve_original(
        self, original: OriginalArtifactInput
    ) -> ArtifactStorageReceipt:
        try:
            receipt = self._adapter.preserve_original(original)
        except ArtifactAdapterUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None
        return self._validate_storage(
            receipt,
            content_sha256=original.content_sha256,
            byte_count=original.byte_count,
        )

    def _preserve_derived(
        self, derived: DerivedArtifactMaterial
    ) -> ArtifactStorageReceipt:
        try:
            receipt = self._adapter.preserve_derived(derived)
        except ArtifactAdapterUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None
        return self._validate_storage(
            receipt,
            content_sha256=derived.content_sha256,
            byte_count=len(derived.content),
        )

    def _idempotency(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        request_sha256: str,
    ) -> ArtifactReceipt | None:
        if not idempotency_key or len(idempotency_key) > 512:
            raise ArtifactServiceError("validation_failed")
        try:
            existing = self._store.idempotency_record(
                context.tenant_id, matter_id, idempotency_key
            )
        except ArtifactStoreUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None
        if existing is None:
            return None
        if existing.request_sha256 != request_sha256:
            raise ArtifactServiceError("idempotency_conflict")
        return existing.response.model_copy(update={"replayed": True})

    @staticmethod
    def _event_ids(
        *, tenant_id: UUID, matter_id: UUID, action: str, idempotency_key: str
    ) -> tuple[UUID, UUID]:
        event = uuid5(
            NAMESPACE_URL,
            f"artifact-audit:{tenant_id}:{matter_id}:{action}:{idempotency_key}",
        )
        return event, uuid5(NAMESPACE_URL, f"artifact-outbox:{event}")

    @staticmethod
    def _scan(
        inspection: ArtifactInspection, *, artifact_id: UUID, at: datetime
    ) -> ArtifactScanRead:
        detail = cast(
            Literal["clean", "pending", "unsafe", "scanner_failed"],
            {
                "clean": "clean",
                "pending": "pending",
                "unsafe": "unsafe",
                "failed": "scanner_failed",
            }[inspection.scan_state],
        )
        return ArtifactScanRead(
            scan_id=uuid5(NAMESPACE_URL, f"artifact-scan:{artifact_id}"),
            scanner_name=inspection.scanner_name,
            scanner_version=inspection.scanner_version,
            signature_revision=inspection.signature_revision,
            state=inspection.scan_state,
            scanned_at=at,
            detail_code=detail,
        )

    @staticmethod
    def _custody(
        *,
        artifact_id: UUID,
        action: Literal["acquired", "duplicate_observed", "derived", "corrected"],
        context: ArtifactAccessContext,
        source_identity: str,
        at: datetime,
        idempotency_key: str,
    ) -> ArtifactCustodyEventRead:
        return ArtifactCustodyEventRead(
            custody_event_id=uuid5(
                NAMESPACE_URL,
                f"artifact-custody:{artifact_id}:{action}:{idempotency_key}",
            ),
            action=action,
            custodian_principal_id=context.principal_id,
            occurred_at=at,
            source_identity=source_identity,
            idempotency_key_sha256=_key_sha256(idempotency_key),
        )

    @staticmethod
    def _governance(
        command: ArtifactIntakeCommand, decision: ArtifactPolicyDecision
    ) -> ArtifactGovernanceRead:
        return ArtifactGovernanceRead(
            classification=command.classification,
            privilege_state=command.privilege_state,
            retention_policy_id=command.retention_policy_id,
            legal_hold_ids=command.legal_hold_ids,
            ethical_wall_ids=command.ethical_wall_ids,
            policy_decision_id=decision.decision_id,
            policy_revision=decision.revision,
        )

    @staticmethod
    def _custody_revision(events: tuple[ArtifactCustodyEventRead, ...]) -> str:
        return _canonical_sha256(
            [event.model_dump(mode="json", by_alias=True) for event in events]
        )

    @staticmethod
    def _lineage_revision(edges: tuple[ArtifactDerivationRead, ...]) -> str:
        return _canonical_sha256(
            [edge.model_dump(mode="json", by_alias=True) for edge in edges]
        )

    @staticmethod
    def _projection_sha256(artifact: ArtifactRead | dict[str, object]) -> str:
        if isinstance(artifact, ArtifactRead):
            value = artifact.model_dump(mode="json", by_alias=True)
        else:
            value = dict(artifact)
        value.pop("projectionSha256", None)
        return _canonical_sha256(value)

    @classmethod
    def _project(
        cls, artifact: ArtifactRead, updates: dict[str, object] | None = None
    ) -> ArtifactRead:
        values = artifact.model_dump()
        values.update(updates or {})
        values["projection_sha256"] = "0" * 64
        candidate = ArtifactRead.model_validate(values)
        values["projection_sha256"] = cls._projection_sha256(candidate)
        return ArtifactRead.model_validate(values)

    def _lineage(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        original_artifact_id: UUID,
        *,
        overlay: tuple[ArtifactRead, ...] = (),
    ) -> ArtifactLineageRead:
        current = {
            item.artifact_id: item
            for item in self._store.all_current(tenant_id, matter_id)
        }
        current.update({item.artifact_id: item for item in overlay})
        if original_artifact_id not in current:
            raise ArtifactServiceError("resource_unavailable")
        selected = {original_artifact_id}
        progressed = True
        while progressed:
            progressed = False
            for artifact in current.values():
                if (
                    artifact.parent_artifact_id in selected
                    and artifact.artifact_id not in selected
                ):
                    selected.add(artifact.artifact_id)
                    progressed = True
        artifacts = tuple(
            sorted(
                (current[artifact_id] for artifact_id in selected),
                key=lambda item: (item.created_at, str(item.artifact_id)),
            )
        )
        edges = tuple(
            artifact.derivation
            for artifact in artifacts
            if artifact.derivation is not None
        )
        return ArtifactLineageRead(
            original_artifact_id=original_artifact_id,
            artifacts=artifacts,
            edges=edges,
            lineage_revision=self._lineage_revision(edges),
        )

    def _atomic_records(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        request_sha256: str,
        action: str,
        resource_id: UUID,
        policy: ArtifactPolicyDecision,
    ) -> tuple[ArtifactAuditFact, ArtifactOutboxMessage]:
        event_id, outbox_id = self._event_ids(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            action=action,
            idempotency_key=idempotency_key,
        )
        occurred_at = self._clock()
        audit = ArtifactAuditFact(
            event_id=event_id,
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            principal_id=context.principal_id,
            action=action,
            resource_id=resource_id,
            outcome="success",
            policy_decision_id=policy.decision_id,
            policy_revision=policy.revision,
            request_sha256=request_sha256,
            occurred_at=occurred_at,
        )
        outbox = ArtifactOutboxMessage(
            outbox_id=outbox_id,
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            event_id=event_id,
            destination="artifact.activity.local",
            payload_sha256=_canonical_sha256(
                {
                    "event_id": str(event_id),
                    "action": action,
                    "resource_id": str(resource_id),
                    "request_sha256": request_sha256,
                }
            ),
        )
        return audit, outbox

    def intake(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: ArtifactIntakeCommand,
    ) -> ArtifactIntakeReceipt:
        policy = self._authorize(
            context=context,
            matter_id=matter_id,
            capability="evidence.manage",
            operation="create",
            classification=command.classification,
        )
        self._ready()
        request_sha256 = _request_sha256("create", command)
        existing = self._idempotency(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            if not isinstance(existing, ArtifactIntakeReceipt):
                raise ArtifactServiceError("idempotency_conflict")
            return existing

        inspection = self._inspect(command.original, command.requested_derivations)
        if inspection.scan_state == "unsafe":
            raise ArtifactServiceError("precondition_failed")
        if inspection.scan_state == "failed":
            raise ArtifactServiceError("dependency_unavailable")
        original_storage = self._preserve_original(command.original)
        at = self._clock()
        try:
            duplicate = self._store.by_content_sha256(
                context.tenant_id, matter_id, command.original.content_sha256
            )
        except ArtifactStoreUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None
        if duplicate is not None:
            custody = duplicate.custody + (
                self._custody(
                    artifact_id=duplicate.artifact_id,
                    action="duplicate_observed",
                    context=context,
                    source_identity=command.source.source_identity,
                    at=at,
                    idempotency_key=idempotency_key,
                ),
            )
            updates: dict[str, object] = {
                "custody": custody,
                "custody_revision": self._custody_revision(custody),
                "projection_revision": duplicate.projection_revision + 1,
            }
            updated = self._project(duplicate, updates)
            lineage = self._lineage(
                context.tenant_id,
                matter_id,
                duplicate.artifact_id,
                overlay=(updated,),
            )
            event_id, outbox_id = self._event_ids(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                action="artifact.intake.recorded",
                idempotency_key=idempotency_key,
            )
            receipt = ArtifactIntakeReceipt(
                artifact=updated,
                derived_artifacts=tuple(
                    item
                    for item in lineage.artifacts
                    if item.artifact_kind == "derived"
                ),
                lineage=lineage,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                replayed=False,
                duplicate=True,
                audit_event_id=event_id,
                outbox_id=outbox_id,
            )
            audit, outbox = self._atomic_records(
                context=context,
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                action="artifact.intake.recorded",
                resource_id=updated.artifact_id,
                policy=policy,
            )
            try:
                self._store.commit(
                    tenant_id=context.tenant_id,
                    matter_id=matter_id,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    revisions=(updated,),
                    command=command,
                    response=receipt,
                    audit=audit,
                    outbox=outbox,
                )
            except (ArtifactStoreUnavailable, ValueError):
                raise ArtifactServiceError("dependency_unavailable") from None
            return receipt

        original_id = uuid5(
            NAMESPACE_URL,
            f"artifact:{context.tenant_id}:{matter_id}:{command.original.content_sha256}",
        )
        scan = self._scan(inspection, artifact_id=original_id, at=at)
        custody = (
            self._custody(
                artifact_id=original_id,
                action="acquired",
                context=context,
                source_identity=command.source.source_identity,
                at=at,
                idempotency_key=idempotency_key,
            ),
        )
        governance = self._governance(command, policy)
        proposed_links = tuple(
            ProposedRecordLinkRead(
                link_id=uuid5(
                    NAMESPACE_URL,
                    f"artifact-link:{original_id}:{link.target_type}:{link.target_id}",
                ),
                target_type=link.target_type,
                target_id=link.target_id,
                rationale=link.rationale,
                proposed_by_principal_id=context.principal_id,
                proposed_at=at,
            )
            for link in command.proposed_links
        )
        derived: list[ArtifactRead] = []
        edges: list[ArtifactDerivationRead] = []
        if inspection.scan_state == "clean":
            for output in inspection.derived:
                derived_storage = self._preserve_derived(output)
                child_id = uuid5(
                    NAMESPACE_URL,
                    f"artifact-derived:{original_id}:{output.kind}:{output.content_sha256}",
                )
                edge = ArtifactDerivationRead(
                    parent_artifact_id=original_id,
                    child_artifact_id=child_id,
                    kind=output.kind,
                    tool_evidence=ToolEvidenceRead(
                        tool_name=output.tool_name,
                        tool_version=output.tool_version,
                        operation=output.kind,
                        input_sha256=command.original.content_sha256,
                        output_sha256=output.content_sha256,
                    ),
                    created_at=at,
                )
                edges.append(edge)
                child_custody = (
                    self._custody(
                        artifact_id=child_id,
                        action="derived",
                        context=context,
                        source_identity=command.source.source_identity,
                        at=at,
                        idempotency_key=idempotency_key,
                    ),
                )
                child_values: dict[str, object] = {
                    "artifact_id": child_id,
                    "tenant_id": context.tenant_id,
                    "matter_id": matter_id,
                    "artifact_kind": "derived",
                    "filename": command.original.filename + output.filename_suffix,
                    "media_type": output.media_type,
                    "byte_count": len(output.content),
                    "content_sha256": output.content_sha256,
                    "original_sha256": command.original.content_sha256,
                    "source": command.source,
                    "acquisition_method": "derived",
                    "storage_locator": derived_storage.storage_locator,
                    "quarantine_state": "released",
                    "scan": self._scan(inspection, artifact_id=child_id, at=at),
                    "extraction_state": "complete",
                    "parent_artifact_id": original_id,
                    "derivation": edge,
                    "custody": child_custody,
                    "governance": governance,
                    "projection_revision": 1,
                    "lineage_revision": self._lineage_revision((edge,)),
                    "custody_revision": self._custody_revision(child_custody),
                    "projection_sha256": "0" * 64,
                    "created_at": at,
                }
                child = self._project(ArtifactRead.model_validate(child_values))
                derived.append(child)
        edge_tuple = tuple(edges)
        root_values: dict[str, object] = {
            "artifact_id": original_id,
            "tenant_id": context.tenant_id,
            "matter_id": matter_id,
            "artifact_kind": "original",
            "filename": command.original.filename,
            "media_type": command.original.media_type,
            "byte_count": command.original.byte_count,
            "content_sha256": command.original.content_sha256,
            "original_sha256": command.original.content_sha256,
            "source": command.source,
            "acquisition_method": command.acquisition_method,
            "storage_locator": original_storage.storage_locator,
            "quarantine_state": (
                "released" if inspection.scan_state == "clean" else "quarantined"
            ),
            "scan": scan,
            "extraction_state": (
                "complete"
                if derived
                else "pending"
                if inspection.scan_state == "pending"
                else "not_requested"
            ),
            "child_artifact_ids": tuple(item.artifact_id for item in derived),
            "custody": custody,
            "governance": governance,
            "proposed_links": proposed_links,
            "projection_revision": 1,
            "lineage_revision": self._lineage_revision(edge_tuple),
            "custody_revision": self._custody_revision(custody),
            "projection_sha256": "0" * 64,
            "created_at": at,
        }
        root = self._project(ArtifactRead.model_validate(root_values))
        lineage = ArtifactLineageRead(
            original_artifact_id=original_id,
            artifacts=(root, *derived),
            edges=edge_tuple,
            lineage_revision=self._lineage_revision(edge_tuple),
        )
        event_id, outbox_id = self._event_ids(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            action="artifact.intake.recorded",
            idempotency_key=idempotency_key,
        )
        receipt = ArtifactIntakeReceipt(
            artifact=root,
            derived_artifacts=tuple(derived),
            lineage=lineage,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            replayed=False,
            duplicate=False,
            audit_event_id=event_id,
            outbox_id=outbox_id,
        )
        audit, outbox = self._atomic_records(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            action="artifact.intake.recorded",
            resource_id=root.artifact_id,
            policy=policy,
        )
        try:
            self._store.commit(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                revisions=(root, *derived),
                command=command,
                response=receipt,
                audit=audit,
                outbox=outbox,
            )
        except (ArtifactStoreUnavailable, ValueError):
            raise ArtifactServiceError("dependency_unavailable") from None
        return receipt

    def correct(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: ArtifactCorrectionCommand,
    ) -> ArtifactReviewReceipt:
        policy = self._authorize(
            context=context,
            matter_id=matter_id,
            capability="evidence.manage",
            operation="correct",
            classification="public",
        )
        self._ready()
        request_sha256 = _request_sha256("correct", command)
        existing = self._idempotency(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            if not isinstance(existing, ArtifactReviewReceipt):
                raise ArtifactServiceError("idempotency_conflict")
            return existing
        target = self._store.current(
            context.tenant_id, matter_id, command.target_artifact_id
        )
        if target is None:
            raise ArtifactServiceError("resource_unavailable")
        if (
            target.artifact_kind == "original"
            or target.supersession is not None
            or target.projection_revision != command.expected_projection_revision
        ):
            raise ArtifactServiceError("precondition_failed")
        inspection = self._inspect(command.corrected, ())
        if inspection.scan_state != "clean":
            raise ArtifactServiceError(
                "precondition_failed"
                if inspection.scan_state == "unsafe"
                else "dependency_unavailable"
            )
        corrected_storage = self._preserve_original(command.corrected)
        at = self._clock()
        successor_id = uuid5(
            NAMESPACE_URL,
            f"artifact-correction:{target.artifact_id}:{command.corrected.content_sha256}",
        )
        edge = ArtifactDerivationRead(
            parent_artifact_id=target.artifact_id,
            child_artifact_id=successor_id,
            kind="human_correction",
            tool_evidence=ToolEvidenceRead(
                tool_name=command.tool_name,
                tool_version=command.tool_version,
                operation="human_correction",
                input_sha256=target.content_sha256,
                output_sha256=command.corrected.content_sha256,
            ),
            created_at=at,
        )
        correction = ArtifactCorrectionRead(
            correction_id=uuid5(
                NAMESPACE_URL, f"artifact-correction-record:{successor_id}"
            ),
            target_artifact_id=target.artifact_id,
            corrected_artifact_id=successor_id,
            reason=command.reason,
            corrected_by_principal_id=context.principal_id,
            corrected_at=at,
        )
        supersession = ArtifactSupersessionRead(
            supersession_id=uuid5(
                NAMESPACE_URL, f"artifact-supersession:{successor_id}"
            ),
            superseded_artifact_id=target.artifact_id,
            successor_artifact_id=successor_id,
            reason=command.reason,
            recorded_by_principal_id=context.principal_id,
            recorded_at=at,
        )
        target_update = self._project(
            target,
            {
                "child_artifact_ids": target.child_artifact_ids + (successor_id,),
                "review_state": "superseded",
                "reviewed_by_principal_id": context.principal_id,
                "reviewed_at": at,
                "corrections": target.corrections + (correction,),
                "supersession": supersession,
                "projection_revision": target.projection_revision + 1,
            },
        )
        custody = (
            self._custody(
                artifact_id=successor_id,
                action="corrected",
                context=context,
                source_identity=target.source.source_identity,
                at=at,
                idempotency_key=idempotency_key,
            ),
        )
        successor_values = {
            "artifact_id": successor_id,
            "tenant_id": context.tenant_id,
            "matter_id": matter_id,
            "artifact_kind": "derived",
            "filename": command.corrected.filename,
            "media_type": command.corrected.media_type,
            "byte_count": command.corrected.byte_count,
            "content_sha256": command.corrected.content_sha256,
            "original_sha256": target.original_sha256,
            "source": target.source,
            "acquisition_method": "human_correction",
            "storage_locator": corrected_storage.storage_locator,
            "quarantine_state": "released",
            "scan": self._scan(inspection, artifact_id=successor_id, at=at),
            "extraction_state": "complete",
            "parent_artifact_id": target.artifact_id,
            "derivation": edge,
            "custody": custody,
            "governance": target.governance,
            "review_state": "accepted",
            "reviewed_by_principal_id": context.principal_id,
            "reviewed_at": at,
            "projection_revision": 1,
            "lineage_revision": self._lineage_revision((edge,)),
            "custody_revision": self._custody_revision(custody),
            "projection_sha256": "0" * 64,
            "created_at": at,
        }
        successor = self._project(ArtifactRead.model_validate(successor_values))
        event_id, outbox_id = self._event_ids(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            action="artifact.correction.recorded",
            idempotency_key=idempotency_key,
        )
        receipt = ArtifactReviewReceipt(
            artifact=successor,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            replayed=False,
            audit_event_id=event_id,
            outbox_id=outbox_id,
        )
        audit, outbox = self._atomic_records(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            action="artifact.correction.recorded",
            resource_id=successor_id,
            policy=policy,
        )
        try:
            self._store.commit(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                revisions=(target_update, successor),
                command=command,
                response=receipt,
                audit=audit,
                outbox=outbox,
            )
        except (ArtifactStoreUnavailable, ValueError):
            raise ArtifactServiceError("dependency_unavailable") from None
        return receipt

    def review(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: ArtifactReviewCommand,
    ) -> ArtifactReviewReceipt:
        policy = self._authorize(
            context=context,
            matter_id=matter_id,
            capability="evidence.manage",
            operation="review",
            classification="public",
        )
        self._ready()
        request_sha256 = _request_sha256("review", command)
        existing = self._idempotency(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            if not isinstance(existing, ArtifactReviewReceipt):
                raise ArtifactServiceError("idempotency_conflict")
            return existing
        artifact = self._store.current(
            context.tenant_id, matter_id, command.artifact_id
        )
        if artifact is None:
            raise ArtifactServiceError("resource_unavailable")
        if (
            artifact.projection_revision != command.expected_projection_revision
            or artifact.supersession is not None
            or artifact.review_state == "superseded"
        ):
            raise ArtifactServiceError("precondition_failed")
        at = self._clock()
        updated = self._project(
            artifact,
            {
                "review_state": command.decision,
                "reviewed_by_principal_id": context.principal_id,
                "reviewed_at": at,
                "projection_revision": artifact.projection_revision + 1,
            },
        )
        event_id, outbox_id = self._event_ids(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            action="artifact.review.recorded",
            idempotency_key=idempotency_key,
        )
        receipt = ArtifactReviewReceipt(
            artifact=updated,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            replayed=False,
            audit_event_id=event_id,
            outbox_id=outbox_id,
        )
        audit, outbox = self._atomic_records(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            action="artifact.review.recorded",
            resource_id=artifact.artifact_id,
            policy=policy,
        )
        try:
            self._store.commit(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                revisions=(updated,),
                command=command,
                response=receipt,
                audit=audit,
                outbox=outbox,
            )
        except (ArtifactStoreUnavailable, ValueError):
            raise ArtifactServiceError("dependency_unavailable") from None
        return receipt

    def supersede(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        idempotency_key: str,
        command: ArtifactSupersessionCommand,
    ) -> ArtifactReviewReceipt:
        policy = self._authorize(
            context=context,
            matter_id=matter_id,
            capability="evidence.manage",
            operation="supersede",
            classification="public",
        )
        self._ready()
        request_sha256 = _request_sha256("supersede", command)
        existing = self._idempotency(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            if not isinstance(existing, ArtifactReviewReceipt):
                raise ArtifactServiceError("idempotency_conflict")
            return existing
        artifact = self._store.current(
            context.tenant_id, matter_id, command.artifact_id
        )
        successor = self._store.current(
            context.tenant_id, matter_id, command.successor_artifact_id
        )
        if artifact is None or successor is None:
            raise ArtifactServiceError("resource_unavailable")
        if (
            artifact.artifact_kind != "derived"
            or successor.artifact_kind != "derived"
            or artifact.artifact_id == successor.artifact_id
            or artifact.original_sha256 != successor.original_sha256
            or artifact.projection_revision != command.expected_projection_revision
            or artifact.supersession is not None
            or artifact.review_state == "superseded"
            or successor.supersession is not None
            or successor.review_state == "superseded"
        ):
            raise ArtifactServiceError("precondition_failed")
        at = self._clock()
        supersession = ArtifactSupersessionRead(
            supersession_id=uuid5(
                NAMESPACE_URL,
                f"artifact-supersession:{artifact.artifact_id}:{successor.artifact_id}",
            ),
            superseded_artifact_id=artifact.artifact_id,
            successor_artifact_id=successor.artifact_id,
            reason=command.reason,
            recorded_by_principal_id=context.principal_id,
            recorded_at=at,
        )
        updated = self._project(
            artifact,
            {
                "review_state": "superseded",
                "reviewed_by_principal_id": context.principal_id,
                "reviewed_at": at,
                "supersession": supersession,
                "projection_revision": artifact.projection_revision + 1,
            },
        )
        event_id, outbox_id = self._event_ids(
            tenant_id=context.tenant_id,
            matter_id=matter_id,
            action="artifact.supersession.recorded",
            idempotency_key=idempotency_key,
        )
        receipt = ArtifactReviewReceipt(
            artifact=updated,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            replayed=False,
            audit_event_id=event_id,
            outbox_id=outbox_id,
        )
        audit, outbox = self._atomic_records(
            context=context,
            matter_id=matter_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            action="artifact.supersession.recorded",
            resource_id=artifact.artifact_id,
            policy=policy,
        )
        try:
            self._store.commit(
                tenant_id=context.tenant_id,
                matter_id=matter_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                revisions=(updated,),
                command=command,
                response=receipt,
                audit=audit,
                outbox=outbox,
            )
        except (ArtifactStoreUnavailable, ValueError):
            raise ArtifactServiceError("dependency_unavailable") from None
        return receipt

    def get(
        self,
        *,
        context: ArtifactAccessContext,
        matter_id: UUID,
        artifact_id: UUID,
    ) -> ArtifactRead:
        policy = self._authorize(
            context=context,
            matter_id=matter_id,
            capability="evidence.read",
            operation="read",
            classification="public",
        )
        self._ready()
        try:
            artifact = self._store.current(context.tenant_id, matter_id, artifact_id)
        except ArtifactStoreUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None
        if artifact is None:
            raise ArtifactServiceError("resource_unavailable")
        request_sha256 = _canonical_sha256(
            {
                "operation": "read",
                "matter_id": str(matter_id),
                "artifact_id": str(artifact_id),
            }
        )
        key = f"read:{context.authorization_decision_id}:{artifact_id}"
        audit, outbox = self._atomic_records(
            context=context,
            matter_id=matter_id,
            idempotency_key=key,
            request_sha256=request_sha256,
            action="artifact.read",
            resource_id=artifact.artifact_id,
            policy=policy,
        )
        try:
            self._store.append_read_audit(audit, outbox)
        except ArtifactStoreUnavailable:
            raise ArtifactServiceError("dependency_unavailable") from None
        return artifact
