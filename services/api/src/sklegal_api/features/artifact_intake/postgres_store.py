"""Durable ArtifactStore adapter for the feature-local PostgreSQL repository."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sklegal_persistence.features.artifact_intake.repository import (
    ArtifactCorrectionBatch,
    ArtifactMutationContext,
    ArtifactPersistenceBatch,
    ArtifactPersistenceConflict,
    ArtifactPersistencePrecondition,
    ArtifactPersistenceReceipt,
    ArtifactPersistenceUnavailable,
    ArtifactReadAuditBatch,
    ArtifactReviewBatch,
    ArtifactSupersessionBatch,
    DerivedArtifactRow,
    PostgresArtifactRepository,
    ProposedArtifactLinkRow,
)

from .contracts import (
    ArtifactContractModel,
    ArtifactCorrectionCommand,
    ArtifactIntakeCommand,
    ArtifactIntakeReceipt,
    ArtifactRead,
    ArtifactReviewCommand,
    ArtifactReviewReceipt,
    ArtifactSupersessionCommand,
)
from .store import (
    ArtifactAuditFact,
    ArtifactCommand,
    ArtifactIdempotencyRecord,
    ArtifactOutboxMessage,
    ArtifactReceipt,
    ArtifactStore,
    ArtifactStoreUnavailable,
)


def _document(model: ArtifactContractModel) -> str:
    return json.dumps(
        model.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _model(document: str, model: type[ArtifactContractModel]) -> ArtifactContractModel:
    try:
        return model.model_validate_json(document)
    except (TypeError, ValueError) as exc:
        raise ArtifactStoreUnavailable("artifact durable document is invalid") from exc


def _receipt(document: str) -> ArtifactReceipt:
    try:
        value = json.loads(document)
    except (TypeError, ValueError) as exc:
        raise ArtifactStoreUnavailable("artifact durable receipt is invalid") from exc
    if not isinstance(value, dict):
        raise ArtifactStoreUnavailable("artifact durable receipt is invalid")
    model = ArtifactIntakeReceipt if "lineage" in value else ArtifactReviewReceipt
    return _model(document, model)  # type: ignore[return-value]


def _key_sha256(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _source_sha256(artifact: ArtifactRead) -> str:
    return hashlib.sha256(_document(artifact.source).encode("utf-8")).hexdigest()


def _derived_row(artifact: ArtifactRead) -> DerivedArtifactRow:
    if (
        artifact.artifact_kind != "derived"
        or artifact.derivation is None
        or not artifact.custody
    ):
        raise ValueError("durable derived artifact projection is incomplete")
    evidence = artifact.derivation.tool_evidence
    return DerivedArtifactRow(
        artifact_id=artifact.artifact_id,
        scan_id=artifact.scan.scan_id,
        custody_event_id=artifact.custody[-1].custody_event_id,
        filename=artifact.filename,
        media_type=artifact.media_type,
        byte_count=artifact.byte_count,
        content_sha256=artifact.content_sha256,
        storage_locator=artifact.storage_locator,
        derivation_kind=artifact.derivation.kind,
        tool_name=evidence.tool_name,
        tool_version=evidence.tool_version,
        projection_document=_document(artifact),
    )


class PostgresArtifactStore(ArtifactStore):
    """Adapt normalized durable operations to the exact ArtifactStore protocol."""

    def __init__(self, repository: PostgresArtifactRepository) -> None:
        self._repository = repository

    def ensure_ready(self) -> None:
        try:
            self._repository.ensure_ready()
        except ArtifactPersistenceUnavailable:
            raise ArtifactStoreUnavailable(
                "artifact durable store unavailable"
            ) from None

    def idempotency_record(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key: str
    ) -> ArtifactIdempotencyRecord | None:
        try:
            record = self._repository.idempotency_document(
                tenant_id, matter_id, _key_sha256(idempotency_key)
            )
        except (ArtifactPersistenceUnavailable, ValueError):
            raise ArtifactStoreUnavailable(
                "artifact durable idempotency unavailable"
            ) from None
        if record is None:
            return None
        return ArtifactIdempotencyRecord(
            request_sha256=record.request_sha256,
            response=_receipt(record.response_document),
        )

    def _projections(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        *,
        artifact_id: UUID | None = None,
        content_sha256: str | None = None,
    ) -> tuple[ArtifactRead, ...]:
        try:
            documents = self._repository.projection_documents(
                tenant_id,
                matter_id,
                artifact_id=artifact_id,
                content_sha256=content_sha256,
            )
        except (ArtifactPersistenceUnavailable, ValueError):
            raise ArtifactStoreUnavailable(
                "artifact durable projection unavailable"
            ) from None
        return tuple(
            _model(document, ArtifactRead)  # type: ignore[misc]
            for document in documents
        )

    def by_content_sha256(
        self, tenant_id: UUID, matter_id: UUID, content_sha256: str
    ) -> ArtifactRead | None:
        matches = tuple(
            artifact
            for artifact in self._projections(
                tenant_id, matter_id, content_sha256=content_sha256
            )
            if artifact.artifact_kind == "original"
        )
        if len(matches) > 1:
            raise ArtifactStoreUnavailable("artifact durable hash is ambiguous")
        return matches[0] if matches else None

    def current(
        self, tenant_id: UUID, matter_id: UUID, artifact_id: UUID
    ) -> ArtifactRead | None:
        matches = self._projections(tenant_id, matter_id, artifact_id=artifact_id)
        if len(matches) > 1:
            raise ArtifactStoreUnavailable("artifact durable identity is ambiguous")
        return matches[0] if matches else None

    def all_current(self, tenant_id: UUID, matter_id: UUID) -> tuple[ArtifactRead, ...]:
        return self._projections(tenant_id, matter_id)

    @staticmethod
    def _context(
        *,
        artifact_id: UUID,
        expected_projection_revision: int,
        idempotency_key: str,
        request_sha256: str,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> ArtifactMutationContext:
        return ArtifactMutationContext(
            tenant_id=audit.tenant_id,
            matter_id=audit.matter_id,
            artifact_id=artifact_id,
            principal_id=audit.principal_id,
            expected_projection_revision=expected_projection_revision,
            idempotency_key_sha256=_key_sha256(idempotency_key),
            request_sha256=request_sha256,
            policy_decision_id=audit.policy_decision_id,
            policy_revision=audit.policy_revision,
            audit_event_id=audit.event_id,
            outbox_id=outbox.outbox_id,
            occurred_at=audit.occurred_at,
            outbox_destination=outbox.destination,
            outbox_payload_sha256=outbox.payload_sha256,
        )

    @staticmethod
    def _validate_receipt(
        persisted: ArtifactPersistenceReceipt, expected: ArtifactRead
    ) -> None:
        if (
            persisted.replayed
            or persisted.artifact_id != expected.artifact_id
            or persisted.projection_revision != expected.projection_revision
        ):
            raise ArtifactStoreUnavailable(
                "artifact durable transaction receipt disagrees with projection"
            )

    def commit(
        self,
        *,
        tenant_id: UUID,
        matter_id: UUID,
        idempotency_key: str,
        request_sha256: str,
        revisions: tuple[ArtifactRead, ...],
        command: ArtifactCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> None:
        if (
            not revisions
            or audit.tenant_id != tenant_id
            or audit.matter_id != matter_id
            or outbox.tenant_id != tenant_id
            or outbox.matter_id != matter_id
            or audit.event_id != outbox.event_id
            or any(
                item.tenant_id != tenant_id or item.matter_id != matter_id
                for item in revisions
            )
        ):
            raise ValueError("artifact durable transaction escaped scope")
        try:
            if isinstance(command, ArtifactIntakeCommand):
                persisted, expected = self._commit_intake(
                    idempotency_key,
                    request_sha256,
                    revisions,
                    command,
                    response,
                    audit,
                    outbox,
                )
            elif isinstance(command, ArtifactReviewCommand):
                persisted, expected = self._commit_review(
                    idempotency_key, request_sha256, command, response, audit, outbox
                )
            elif isinstance(command, ArtifactCorrectionCommand):
                persisted, expected = self._commit_correction(
                    idempotency_key,
                    request_sha256,
                    revisions,
                    command,
                    response,
                    audit,
                    outbox,
                )
            elif isinstance(command, ArtifactSupersessionCommand):
                persisted, expected = self._commit_supersession(
                    idempotency_key, request_sha256, command, response, audit, outbox
                )
            else:
                raise ValueError("artifact durable command is unsupported")
        except ArtifactPersistenceUnavailable:
            raise ArtifactStoreUnavailable(
                "artifact durable transaction unavailable"
            ) from None
        except (ArtifactPersistenceConflict, ArtifactPersistencePrecondition):
            raise ValueError(
                "artifact durable transaction precondition failed"
            ) from None
        self._validate_receipt(persisted, expected)

    def _commit_intake(
        self,
        idempotency_key: str,
        request_sha256: str,
        revisions: tuple[ArtifactRead, ...],
        command: ArtifactIntakeCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> tuple[ArtifactPersistenceReceipt, ArtifactRead]:
        if (
            not isinstance(response, ArtifactIntakeReceipt)
            or not response.artifact.custody
        ):
            raise ValueError("artifact durable intake receipt is incomplete")
        root = response.artifact
        derived = tuple(
            _derived_row(item) for item in revisions if item.artifact_kind == "derived"
        )
        batch = ArtifactPersistenceBatch(
            tenant_id=root.tenant_id,
            matter_id=root.matter_id,
            artifact_id=root.artifact_id,
            principal_id=audit.principal_id,
            source_identity=(
                root.custody[-1].source_identity
                if response.duplicate
                else root.source.source_identity
            ),
            source_version=root.source.source_version,
            source_identity_sha256=_source_sha256(root),
            filename=root.filename,
            media_type=root.media_type,
            byte_count=root.byte_count,
            content_sha256=root.content_sha256,
            storage_locator=root.storage_locator,
            acquisition_method=command.acquisition_method,
            classification=root.governance.classification,
            privilege_state=root.governance.privilege_state,
            retention_policy_id=root.governance.retention_policy_id,
            legal_hold_ids=root.governance.legal_hold_ids,
            ethical_wall_ids=root.governance.ethical_wall_ids,
            policy_decision_id=audit.policy_decision_id,
            policy_revision=audit.policy_revision,
            scan_id=root.scan.scan_id,
            scan_state=root.scan.state,
            scanner_name=root.scan.scanner_name,
            scanner_version=root.scan.scanner_version,
            signature_revision=root.scan.signature_revision,
            custody_event_id=root.custody[-1].custody_event_id,
            idempotency_key=idempotency_key,
            idempotency_key_sha256=_key_sha256(idempotency_key),
            request_sha256=request_sha256,
            audit_event_id=audit.event_id,
            outbox_id=outbox.outbox_id,
            occurred_at=audit.occurred_at,
            derived=derived,
            proposed_links=tuple(
                ProposedArtifactLinkRow(
                    link_id=link.link_id,
                    target_type=link.target_type,
                    target_id=link.target_id,
                    rationale=link.rationale,
                )
                for link in root.proposed_links
            ),
            projection_document=_document(root),
            response_document=_document(response),
            outbox_destination=outbox.destination,
            outbox_payload_sha256=outbox.payload_sha256,
        )
        return self._repository.append_intake(batch), root

    def _commit_review(
        self,
        idempotency_key: str,
        request_sha256: str,
        command: ArtifactReviewCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> tuple[ArtifactPersistenceReceipt, ArtifactRead]:
        if not isinstance(response, ArtifactReviewReceipt):
            raise ValueError("artifact durable review receipt is incomplete")
        artifact = response.artifact
        batch = ArtifactReviewBatch(
            context=self._context(
                artifact_id=artifact.artifact_id,
                expected_projection_revision=command.expected_projection_revision,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                audit=audit,
                outbox=outbox,
            ),
            review_id=uuid5(
                NAMESPACE_URL,
                f"artifact-review:{artifact.artifact_id}:{artifact.projection_revision}",
            ),
            decision=command.decision,
            rationale=command.rationale,
            projection_document=_document(artifact),
            response_document=_document(response),
        )
        return self._repository.append_review(batch), artifact

    def _commit_correction(
        self,
        idempotency_key: str,
        request_sha256: str,
        revisions: tuple[ArtifactRead, ...],
        command: ArtifactCorrectionCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> tuple[ArtifactPersistenceReceipt, ArtifactRead]:
        if not isinstance(response, ArtifactReviewReceipt):
            raise ValueError("artifact durable correction receipt is incomplete")
        targets = tuple(
            item for item in revisions if item.artifact_id == command.target_artifact_id
        )
        if len(targets) != 1:
            raise ValueError("artifact durable correction target is incomplete")
        target = targets[0]
        corrected = response.artifact
        correction = target.corrections[-1] if target.corrections else None
        supersession = target.supersession
        if correction is None or supersession is None:
            raise ValueError("artifact durable correction lineage is incomplete")
        batch = ArtifactCorrectionBatch(
            context=self._context(
                artifact_id=target.artifact_id,
                expected_projection_revision=command.expected_projection_revision,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                audit=audit,
                outbox=outbox,
            ),
            correction_id=correction.correction_id,
            supersession_id=supersession.supersession_id,
            corrected=_derived_row(corrected),
            reason=command.reason,
            target_projection_document=_document(target),
            response_document=_document(response),
        )
        return self._repository.append_correction(batch), corrected

    def _commit_supersession(
        self,
        idempotency_key: str,
        request_sha256: str,
        command: ArtifactSupersessionCommand,
        response: ArtifactReceipt,
        audit: ArtifactAuditFact,
        outbox: ArtifactOutboxMessage,
    ) -> tuple[ArtifactPersistenceReceipt, ArtifactRead]:
        if not isinstance(response, ArtifactReviewReceipt):
            raise ValueError("artifact durable supersession receipt is incomplete")
        artifact = response.artifact
        supersession = artifact.supersession
        if supersession is None:
            raise ValueError("artifact durable supersession is incomplete")
        batch = ArtifactSupersessionBatch(
            context=self._context(
                artifact_id=artifact.artifact_id,
                expected_projection_revision=command.expected_projection_revision,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                audit=audit,
                outbox=outbox,
            ),
            supersession_id=supersession.supersession_id,
            successor_artifact_id=command.successor_artifact_id,
            reason=command.reason,
            projection_document=_document(artifact),
            response_document=_document(response),
        )
        return self._repository.append_supersession(batch), artifact

    def append_read_audit(
        self, audit: ArtifactAuditFact, outbox: ArtifactOutboxMessage
    ) -> None:
        if (
            audit.action != "artifact.read"
            or audit.event_id != outbox.event_id
            or audit.tenant_id != outbox.tenant_id
            or audit.matter_id != outbox.matter_id
            or audit.resource_id.int == 0
        ):
            raise ValueError("artifact read audit escaped scope")
        try:
            self._repository.append_read_audit(
                ArtifactReadAuditBatch(
                    tenant_id=audit.tenant_id,
                    matter_id=audit.matter_id,
                    artifact_id=audit.resource_id,
                    principal_id=audit.principal_id,
                    policy_decision_id=audit.policy_decision_id,
                    policy_revision=audit.policy_revision,
                    request_sha256=audit.request_sha256,
                    audit_event_id=audit.event_id,
                    outbox_id=outbox.outbox_id,
                    destination=outbox.destination,
                    payload_sha256=outbox.payload_sha256,
                    occurred_at=audit.occurred_at,
                )
            )
        except ArtifactPersistenceUnavailable:
            raise ArtifactStoreUnavailable(
                "artifact durable read audit unavailable"
            ) from None
