"""One-transaction PostgreSQL write contract for artifact intake."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,254}$")
_CLASSIFICATIONS = {
    "public",
    "internal",
    "confidential",
    "privileged_work_product",
    "highly_restricted",
}
_SCAN_STATES = {"pending", "clean", "unsafe", "failed"}
_DERIVATION_KINDS = {"text_extraction", "ocr", "transcript", "human_correction"}
_LINK_TYPES = {
    "matter_event",
    "communication",
    "fact_assertion",
    "evidence_item",
    "issue",
    "claim",
    "element",
    "task",
    "work_product",
}


class ArtifactPersistenceUnavailable(RuntimeError):
    """The database did not complete an attributable atomic write."""


class ArtifactPersistenceConflict(RuntimeError):
    """An idempotency key was already bound to different request bytes."""


class ArtifactPersistencePrecondition(RuntimeError):
    """The durable artifact lifecycle precondition did not hold."""


def _require_sha256(name: str, value: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase sha256")


def _require_token(name: str, value: str) -> None:
    if _TOKEN.fullmatch(value) is None:
        raise ValueError(f"{name} is outside the bounded token contract")


def _require_json_object(name: str, value: str | None) -> None:
    if value is None:
        return
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")


@dataclass(frozen=True)
class DerivedArtifactRow:
    artifact_id: UUID
    scan_id: UUID
    custody_event_id: UUID
    filename: str
    media_type: str
    byte_count: int
    content_sha256: str
    storage_locator: str
    derivation_kind: Literal["text_extraction", "ocr", "transcript", "human_correction"]
    tool_name: str
    tool_version: str
    projection_document: str | None = None

    def __post_init__(self) -> None:
        if self.byte_count < 0:
            raise ValueError("derived byte_count must not be negative")
        _require_sha256("derived content_sha256", self.content_sha256)
        _require_token("derived storage_locator", self.storage_locator)
        _require_token("derived tool_name", self.tool_name)
        _require_token("derived tool_version", self.tool_version)
        if self.derivation_kind not in _DERIVATION_KINDS:
            raise ValueError("derived artifact kind is outside the closed contract")
        _require_json_object("derived projection_document", self.projection_document)


@dataclass(frozen=True)
class ProposedArtifactLinkRow:
    link_id: UUID
    target_type: Literal[
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
    target_id: UUID
    rationale: str

    def __post_init__(self) -> None:
        if self.target_type not in _LINK_TYPES:
            raise ValueError("link target type is outside the closed contract")
        if not 1 <= len(self.rationale) <= 512:
            raise ValueError("link rationale is outside bounds")


@dataclass(frozen=True)
class ArtifactPersistenceBatch:
    tenant_id: UUID
    matter_id: UUID
    artifact_id: UUID
    principal_id: UUID
    source_identity: str
    source_version: str
    source_identity_sha256: str
    filename: str
    media_type: str
    byte_count: int
    content_sha256: str
    storage_locator: str
    acquisition_method: str
    classification: str
    privilege_state: str
    retention_policy_id: UUID
    legal_hold_ids: tuple[UUID, ...]
    ethical_wall_ids: tuple[UUID, ...]
    policy_decision_id: UUID
    policy_revision: str
    scan_id: UUID
    scan_state: str
    scanner_name: str
    scanner_version: str
    signature_revision: str
    custody_event_id: UUID
    idempotency_key: str
    idempotency_key_sha256: str
    request_sha256: str
    audit_event_id: UUID
    outbox_id: UUID
    occurred_at: datetime
    derived: tuple[DerivedArtifactRow, ...]
    proposed_links: tuple[ProposedArtifactLinkRow, ...]
    projection_document: str | None = None
    response_document: str | None = None
    outbox_destination: str | None = None
    outbox_payload_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.byte_count < 0:
            raise ValueError("byte_count must not be negative")
        for name in (
            "source_identity_sha256",
            "content_sha256",
            "policy_revision",
            "signature_revision",
            "idempotency_key_sha256",
            "request_sha256",
        ):
            _require_sha256(name, str(getattr(self, name)))
        for name in (
            "source_identity",
            "source_version",
            "storage_locator",
            "acquisition_method",
            "privilege_state",
            "scanner_name",
            "scanner_version",
        ):
            _require_token(name, str(getattr(self, name)))
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError("classification is outside the closed contract")
        if self.scan_state not in _SCAN_STATES:
            raise ValueError("scan_state is outside the closed contract")
        if not self.idempotency_key or len(self.idempotency_key) > 512:
            raise ValueError("idempotency_key is outside bounds")
        if len(self.legal_hold_ids) != len(set(self.legal_hold_ids)):
            raise ValueError("legal hold identifiers must be unique")
        if len(self.ethical_wall_ids) != len(set(self.ethical_wall_ids)):
            raise ValueError("ethical wall identifiers must be unique")
        if len({row.artifact_id for row in self.derived}) != len(self.derived):
            raise ValueError("derived artifact identifiers must be unique")
        if self.artifact_id in {row.artifact_id for row in self.derived}:
            raise ValueError("derived artifact cannot reuse the original identifier")
        _require_json_object("projection_document", self.projection_document)
        _require_json_object("response_document", self.response_document)
        if self.outbox_destination is not None:
            _require_token("outbox_destination", self.outbox_destination)
        if self.outbox_payload_sha256 is not None:
            _require_sha256("outbox_payload_sha256", self.outbox_payload_sha256)


@dataclass(frozen=True)
class ArtifactPersistenceReceipt:
    artifact_id: UUID
    duplicate: bool
    replayed: bool
    projection_revision: int


@dataclass(frozen=True)
class ArtifactMutationContext:
    tenant_id: UUID
    matter_id: UUID
    artifact_id: UUID
    principal_id: UUID
    expected_projection_revision: int
    idempotency_key_sha256: str
    request_sha256: str
    policy_decision_id: UUID
    policy_revision: str
    audit_event_id: UUID
    outbox_id: UUID
    occurred_at: datetime
    outbox_destination: str | None = None
    outbox_payload_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.expected_projection_revision < 1:
            raise ValueError("expected projection revision must be positive")
        for name in ("idempotency_key_sha256", "request_sha256", "policy_revision"):
            _require_sha256(name, str(getattr(self, name)))
        if self.outbox_destination is not None:
            _require_token("outbox_destination", self.outbox_destination)
        if self.outbox_payload_sha256 is not None:
            _require_sha256("outbox_payload_sha256", self.outbox_payload_sha256)


@dataclass(frozen=True)
class ArtifactReadContext:
    tenant_id: UUID
    matter_id: UUID
    artifact_id: UUID
    principal_id: UUID
    policy_decision_id: UUID
    policy_revision: str
    request_sha256: str
    audit_event_id: UUID
    outbox_id: UUID
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_sha256("policy_revision", self.policy_revision)
        _require_sha256("request_sha256", self.request_sha256)


@dataclass(frozen=True)
class ArtifactReviewBatch:
    context: ArtifactMutationContext
    review_id: UUID
    decision: Literal["accepted", "changes_requested", "rejected"]
    rationale: str
    projection_document: str | None = None
    response_document: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in {"accepted", "changes_requested", "rejected"}:
            raise ValueError("review decision is outside the closed contract")
        if not 1 <= len(self.rationale) <= 512:
            raise ValueError("review rationale is outside bounds")
        _require_json_object("projection_document", self.projection_document)
        _require_json_object("response_document", self.response_document)


@dataclass(frozen=True)
class ArtifactCorrectionBatch:
    context: ArtifactMutationContext
    correction_id: UUID
    supersession_id: UUID
    corrected: DerivedArtifactRow
    reason: str
    target_projection_document: str | None = None
    response_document: str | None = None

    def __post_init__(self) -> None:
        if self.corrected.derivation_kind != "human_correction":
            raise ValueError("correction must use human_correction derivation")
        if self.corrected.artifact_id == self.context.artifact_id:
            raise ValueError("correction must create a new derived artifact")
        if not 1 <= len(self.reason) <= 512:
            raise ValueError("correction reason is outside bounds")
        _require_json_object(
            "target_projection_document", self.target_projection_document
        )
        _require_json_object("response_document", self.response_document)


@dataclass(frozen=True)
class ArtifactSupersessionBatch:
    context: ArtifactMutationContext
    supersession_id: UUID
    successor_artifact_id: UUID
    reason: str
    projection_document: str | None = None
    response_document: str | None = None

    def __post_init__(self) -> None:
        if self.successor_artifact_id == self.context.artifact_id:
            raise ValueError("supersession must name a different successor")
        if not 1 <= len(self.reason) <= 512:
            raise ValueError("supersession reason is outside bounds")
        _require_json_object("projection_document", self.projection_document)
        _require_json_object("response_document", self.response_document)


@dataclass(frozen=True)
class ArtifactDocumentReceipt:
    request_sha256: str
    response_document: str

    def __post_init__(self) -> None:
        _require_sha256("request_sha256", self.request_sha256)
        _require_json_object("response_document", self.response_document)


@dataclass(frozen=True)
class ArtifactReadAuditBatch:
    tenant_id: UUID
    matter_id: UUID
    artifact_id: UUID
    principal_id: UUID
    policy_decision_id: UUID
    policy_revision: str
    request_sha256: str
    audit_event_id: UUID
    outbox_id: UUID
    destination: str
    payload_sha256: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name in ("policy_revision", "request_sha256", "payload_sha256"):
            _require_sha256(name, str(getattr(self, name)))
        _require_token("destination", self.destination)


@dataclass(frozen=True)
class ArtifactPersistenceRead:
    artifact_id: UUID
    artifact_kind: Literal["original", "derived"]
    content_sha256: str
    original_sha256: str
    review_state: Literal[
        "proposed", "accepted", "changes_requested", "rejected", "superseded"
    ]
    projection_revision: int
    custody_count: int

    def __post_init__(self) -> None:
        _require_sha256("content_sha256", self.content_sha256)
        _require_sha256("original_sha256", self.original_sha256)
        if self.projection_revision < 1 or self.custody_count < 1:
            raise ValueError("durable artifact projection is incomplete")


class SqlSession(Protocol):
    def execute(
        self, statement: str, parameters: Mapping[str, object]
    ) -> Sequence[Mapping[str, object]]: ...


type SessionFactory = Callable[[], AbstractContextManager[SqlSession]]


ARTIFACT_STORE_READY_SQL = "SELECT true AS ready"

ARTIFACT_IDEMPOTENCY_DOCUMENT_SQL = """
SELECT request_sha256, response_document::text AS response_document
  FROM sklegal_artifact.artifact_idempotency_receipts
 WHERE tenant_id = %(tenant_id)s
   AND matter_id = %(matter_id)s
   AND idempotency_key_sha256 = %(idempotency_key_sha256)s
"""

ARTIFACT_PROJECTION_DOCUMENTS_SQL = """
SELECT DISTINCT ON (projection.artifact_id)
       projection.artifact_id,
       projection.projection_document::text AS projection_document
  FROM sklegal_artifact.artifact_projection_revisions AS projection
  JOIN sklegal_artifact.artifacts AS artifact
    ON artifact.tenant_id = projection.tenant_id
   AND artifact.matter_id = projection.matter_id
   AND artifact.id = projection.artifact_id
 WHERE projection.tenant_id = %(tenant_id)s
   AND projection.matter_id = %(matter_id)s
   AND (%(artifact_id)s::uuid IS NULL OR projection.artifact_id = %(artifact_id)s)
   AND (%(content_sha256)s::text IS NULL
        OR artifact.content_sha256 = %(content_sha256)s)
 ORDER BY projection.artifact_id, projection.revision DESC
"""

ATOMIC_ARTIFACT_READ_AUDIT_SQL = """
WITH scoped_artifact AS (
    SELECT id
      FROM sklegal_artifact.artifacts
     WHERE tenant_id = %(tenant_id)s
       AND matter_id = %(matter_id)s
       AND id = %(artifact_id)s
),
inserted_audit AS (
    INSERT INTO sklegal_artifact.artifact_audit_facts (
        tenant_id, matter_id, id, principal_id, action, resource_id,
        outcome, policy_decision_id, policy_revision, request_sha256, occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(audit_event_id)s, %(principal_id)s,
           'artifact.read', id, 'success', %(policy_decision_id)s,
           %(policy_revision)s, %(request_sha256)s, %(occurred_at)s
      FROM scoped_artifact
    RETURNING id
),
inserted_outbox AS (
    INSERT INTO sklegal_artifact.artifact_outbox (
        tenant_id, matter_id, id, audit_event_id, destination,
        payload_sha256, available_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(outbox_id)s, id,
           %(destination)s, %(payload_sha256)s, %(occurred_at)s
      FROM inserted_audit
    RETURNING id
)
SELECT id AS outbox_id FROM inserted_outbox
"""


ATOMIC_ARTIFACT_INTAKE_SQL = """
WITH prior AS (
    SELECT result_artifact_id, operation, request_sha256, duplicate,
           projection_revision
    FROM sklegal_artifact.artifact_idempotency_receipts
    WHERE tenant_id = %(tenant_id)s
      AND matter_id = %(matter_id)s
      AND idempotency_key_sha256 = %(idempotency_key_sha256)s
),
conflict AS (
    SELECT EXISTS (
        SELECT 1 FROM prior WHERE request_sha256 <> %(request_sha256)s
    ) AS value
),
canonical AS (
    SELECT artifact.id,
           COALESCE(MAX(projection.revision), artifact.projection_revision)
               AS projection_revision
    FROM sklegal_artifact.artifacts AS artifact
    LEFT JOIN sklegal_artifact.artifact_projection_revisions AS projection
      ON projection.tenant_id = artifact.tenant_id
     AND projection.matter_id = artifact.matter_id
     AND projection.artifact_id = artifact.id
    WHERE artifact.tenant_id = %(tenant_id)s
      AND artifact.matter_id = %(matter_id)s
      AND artifact.artifact_kind = 'original'
      AND artifact.content_sha256 = %(content_sha256)s
      AND NOT EXISTS (SELECT 1 FROM prior)
    GROUP BY artifact.id, artifact.projection_revision
),
inserted_original AS (
    INSERT INTO sklegal_artifact.artifacts (
        tenant_id, matter_id, id, artifact_kind, source_identity,
        source_version, source_identity_sha256, filename, media_type,
        byte_count, content_sha256, original_sha256, storage_locator,
        acquisition_method, quarantine_state, extraction_state,
        classification, privilege_state, retention_policy_id,
        legal_hold_ids, ethical_wall_ids, policy_decision_id,
        policy_revision, projection_revision, created_at
    )
    SELECT
        %(tenant_id)s, %(matter_id)s, %(artifact_id)s, 'original',
        %(source_identity)s, %(source_version)s, %(source_identity_sha256)s,
        %(filename)s, %(media_type)s, %(byte_count)s, %(content_sha256)s,
        %(content_sha256)s, %(storage_locator)s, %(acquisition_method)s,
        CASE WHEN %(scan_state)s = 'clean' THEN 'released' ELSE 'quarantined' END,
        CASE WHEN %(scan_state)s = 'clean' AND jsonb_array_length(%(derived)s::jsonb) > 0
             THEN 'complete'
             WHEN %(scan_state)s = 'pending' THEN 'pending'
             ELSE 'not_requested' END,
        %(classification)s, %(privilege_state)s, %(retention_policy_id)s,
        %(legal_hold_ids)s, %(ethical_wall_ids)s, %(policy_decision_id)s,
        %(policy_revision)s, 1, %(occurred_at)s
    WHERE NOT EXISTS (SELECT 1 FROM prior)
      AND NOT EXISTS (SELECT 1 FROM canonical)
      AND NOT (SELECT value FROM conflict)
    RETURNING id, projection_revision
),
selected_original_base AS (
    SELECT id, projection_revision, false AS duplicate FROM inserted_original
    UNION ALL
    SELECT id, projection_revision, true FROM canonical
),
inserted_original_projection AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state, recorded_at,
        projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, id, 1, 'proposed', %(occurred_at)s,
           %(projection_document)s::jsonb
      FROM selected_original_base
     WHERE duplicate = false
    RETURNING artifact_id, revision
),
inserted_duplicate_projection AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state, recorded_at,
        projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, id, projection_revision + 1,
           'proposed', %(occurred_at)s, %(projection_document)s::jsonb
      FROM selected_original_base
     WHERE duplicate = true
    RETURNING artifact_id, revision
),
selected_original AS (
    SELECT base.id, projection.revision AS projection_revision, false AS duplicate
      FROM selected_original_base AS base
      JOIN inserted_original_projection AS projection
        ON projection.artifact_id = base.id
     WHERE base.duplicate = false
    UNION ALL
    SELECT base.id, projection.revision, true
      FROM selected_original_base AS base
      JOIN inserted_duplicate_projection AS projection
        ON projection.artifact_id = base.id
     WHERE base.duplicate = true
),
inserted_scan AS (
    INSERT INTO sklegal_artifact.artifact_scan_events (
        tenant_id, matter_id, id, artifact_id, scanner_name, scanner_version,
        signature_revision, scan_state, detail_code, scanned_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(scan_id)s, id,
           %(scanner_name)s, %(scanner_version)s, %(signature_revision)s,
           %(scan_state)s,
           CASE %(scan_state)s WHEN 'clean' THEN 'clean'
                WHEN 'pending' THEN 'pending'
                WHEN 'unsafe' THEN 'unsafe' ELSE 'scanner_failed' END,
           %(occurred_at)s
    FROM selected_original WHERE duplicate = false
    RETURNING id
),
inserted_custody AS (
    INSERT INTO sklegal_artifact.artifact_custody_events (
        tenant_id, matter_id, id, artifact_id, action,
        custodian_principal_id, source_identity, idempotency_key_sha256,
        occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(custody_event_id)s, id,
           CASE WHEN duplicate THEN 'duplicate_observed' ELSE 'acquired' END,
           %(principal_id)s, %(source_identity)s, %(idempotency_key_sha256)s,
           %(occurred_at)s
    FROM selected_original
    RETURNING id
),
derived_rows AS (
    SELECT * FROM jsonb_to_recordset(%(derived)s::jsonb) AS row(
        artifact_id uuid, scan_id uuid, custody_event_id uuid,
        filename text, media_type text, byte_count bigint,
        content_sha256 text, storage_locator text, derivation_kind text,
        tool_name text, tool_version text, projection_document jsonb
    )
),
inserted_derived AS (
    INSERT INTO sklegal_artifact.artifacts (
        tenant_id, matter_id, id, artifact_kind, parent_artifact_id,
        source_identity, source_version, source_identity_sha256, filename,
        media_type, byte_count, content_sha256, original_sha256,
        storage_locator, acquisition_method, quarantine_state,
        extraction_state, classification, privilege_state,
        retention_policy_id, legal_hold_ids, ethical_wall_ids,
        policy_decision_id, policy_revision, projection_revision, created_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, row.artifact_id, 'derived', selected.id,
           %(source_identity)s, %(source_version)s, %(source_identity_sha256)s,
           row.filename, row.media_type, row.byte_count, row.content_sha256,
           %(content_sha256)s, row.storage_locator, 'derived', 'released',
           'complete', %(classification)s, %(privilege_state)s,
           %(retention_policy_id)s, %(legal_hold_ids)s, %(ethical_wall_ids)s,
           %(policy_decision_id)s, %(policy_revision)s, 1, %(occurred_at)s
    FROM derived_rows row CROSS JOIN selected_original selected
    WHERE selected.duplicate = false AND %(scan_state)s = 'clean'
    RETURNING id, parent_artifact_id
),
inserted_derived_projections AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state, recorded_at,
        projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, inserted.id, 1, 'proposed',
           %(occurred_at)s, row.projection_document
      FROM inserted_derived AS inserted
      JOIN derived_rows AS row ON row.artifact_id = inserted.id
    RETURNING artifact_id
),
inserted_derivations AS (
    INSERT INTO sklegal_artifact.artifact_derivations (
        tenant_id, matter_id, parent_artifact_id, child_artifact_id,
        derivation_kind, tool_name, tool_version, input_sha256,
        output_sha256, created_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, selected.id, row.artifact_id,
           row.derivation_kind, row.tool_name, row.tool_version,
           %(content_sha256)s, row.content_sha256, %(occurred_at)s
    FROM derived_rows row CROSS JOIN selected_original selected
    JOIN inserted_derived_projections AS projection
      ON projection.artifact_id = row.artifact_id
    WHERE selected.duplicate = false AND %(scan_state)s = 'clean'
    RETURNING child_artifact_id
),
inserted_derived_scans AS (
    INSERT INTO sklegal_artifact.artifact_scan_events (
        tenant_id, matter_id, id, artifact_id, scanner_name, scanner_version,
        signature_revision, scan_state, detail_code, scanned_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, row.scan_id, row.artifact_id,
           %(scanner_name)s, %(scanner_version)s, %(signature_revision)s,
           'clean', 'clean', %(occurred_at)s
    FROM derived_rows row
    JOIN inserted_derived inserted ON inserted.id = row.artifact_id
    RETURNING id
),
inserted_derived_custody AS (
    INSERT INTO sklegal_artifact.artifact_custody_events (
        tenant_id, matter_id, id, artifact_id, action,
        custodian_principal_id, source_identity, idempotency_key_sha256,
        occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, row.custody_event_id,
           row.artifact_id, 'derived', %(principal_id)s,
           %(source_identity)s, %(idempotency_key_sha256)s, %(occurred_at)s
    FROM derived_rows row
    JOIN inserted_derived inserted ON inserted.id = row.artifact_id
    RETURNING id
),
link_rows AS (
    SELECT * FROM jsonb_to_recordset(%(proposed_links)s::jsonb) AS row(
        link_id uuid, target_type text, target_id uuid, rationale text
    )
),
inserted_links AS (
    INSERT INTO sklegal_artifact.artifact_record_links (
        tenant_id, matter_id, id, artifact_id, target_type, target_id,
        rationale, proposed_by_principal_id, review_state, proposed_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, row.link_id, selected.id,
           row.target_type, row.target_id, row.rationale, %(principal_id)s,
           'proposed', %(occurred_at)s
    FROM link_rows row CROSS JOIN selected_original selected
    WHERE selected.duplicate = false
    RETURNING id
),
inserted_audit AS (
    INSERT INTO sklegal_artifact.artifact_audit_facts (
        tenant_id, matter_id, id, principal_id, action, resource_id,
        outcome, policy_decision_id, policy_revision, request_sha256,
        occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(audit_event_id)s,
           %(principal_id)s, 'artifact.intake.recorded', selected.id,
           'success', %(policy_decision_id)s, %(policy_revision)s,
           %(request_sha256)s, %(occurred_at)s
    FROM selected_original selected
    RETURNING id, resource_id
),
inserted_outbox AS (
    INSERT INTO sklegal_artifact.artifact_outbox (
        tenant_id, matter_id, id, audit_event_id, destination,
        payload_sha256, available_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(outbox_id)s, id,
           COALESCE(%(outbox_destination)s, 'artifact.activity.local'),
           COALESCE(%(outbox_payload_sha256)s, %(request_sha256)s),
           %(occurred_at)s
    FROM inserted_audit
    RETURNING id
),
inserted_receipt AS (
    INSERT INTO sklegal_artifact.artifact_idempotency_receipts (
        tenant_id, matter_id, idempotency_key_sha256, operation, request_sha256,
        result_artifact_id, duplicate, projection_revision, created_at,
        response_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(idempotency_key_sha256)s, 'create',
           %(request_sha256)s, id, duplicate, projection_revision,
           %(occurred_at)s, %(response_document)s::jsonb
    FROM selected_original
    RETURNING result_artifact_id, duplicate, projection_revision
)
SELECT result_artifact_id AS artifact_id, duplicate, false AS replayed,
       false AS conflict, projection_revision
FROM inserted_receipt
UNION ALL
SELECT result_artifact_id, duplicate, true, false, projection_revision
FROM prior WHERE request_sha256 = %(request_sha256)s
UNION ALL
SELECT NULL::uuid, false, false, true, NULL::bigint
WHERE (SELECT value FROM conflict)
"""

ATOMIC_ARTIFACT_READ_SQL = """
WITH current_artifact AS (
    SELECT artifact.id AS artifact_id, artifact.artifact_kind,
           artifact.content_sha256, artifact.original_sha256,
           projection.review_state,
           projection.revision AS projection_revision,
           (SELECT COUNT(*)
              FROM sklegal_artifact.artifact_custody_events AS custody
             WHERE custody.tenant_id = artifact.tenant_id
               AND custody.matter_id = artifact.matter_id
               AND custody.artifact_id = artifact.id) AS custody_count
      FROM sklegal_artifact.artifacts AS artifact
      JOIN LATERAL (
          SELECT revision, review_state
            FROM sklegal_artifact.artifact_projection_revisions
           WHERE tenant_id = artifact.tenant_id
             AND matter_id = artifact.matter_id
             AND artifact_id = artifact.id
           ORDER BY revision DESC
           LIMIT 1
      ) AS projection ON true
     WHERE artifact.tenant_id = %(tenant_id)s
       AND artifact.matter_id = %(matter_id)s
       AND artifact.id = %(artifact_id)s
),
inserted_audit AS (
    INSERT INTO sklegal_artifact.artifact_audit_facts (
        tenant_id, matter_id, id, principal_id, action, resource_id,
        outcome, policy_decision_id, policy_revision, request_sha256, occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(audit_event_id)s, %(principal_id)s,
           'artifact.read', artifact_id, 'success', %(policy_decision_id)s,
           %(policy_revision)s, %(request_sha256)s, %(occurred_at)s
      FROM current_artifact
    RETURNING id
),
inserted_outbox AS (
    INSERT INTO sklegal_artifact.artifact_outbox (
        tenant_id, matter_id, id, audit_event_id, destination,
        payload_sha256, available_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(outbox_id)s, id,
           'artifact.activity.local', %(request_sha256)s, %(occurred_at)s
      FROM inserted_audit
    RETURNING id
)
SELECT artifact_id, artifact_kind, content_sha256, original_sha256,
       review_state, projection_revision, custody_count
  FROM current_artifact
  JOIN inserted_outbox ON true
"""

ATOMIC_ARTIFACT_REVIEW_SQL = """
WITH prior AS (
    SELECT result_artifact_id, operation, request_sha256, projection_revision
      FROM sklegal_artifact.artifact_idempotency_receipts
     WHERE tenant_id = %(tenant_id)s
       AND matter_id = %(matter_id)s
       AND idempotency_key_sha256 = %(idempotency_key_sha256)s
),
conflict AS (
    SELECT EXISTS (
        SELECT 1 FROM prior
         WHERE operation <> 'review' OR request_sha256 <> %(request_sha256)s
    ) AS value
),
current_projection AS (
    SELECT revision, review_state
      FROM sklegal_artifact.artifact_projection_revisions
     WHERE tenant_id = %(tenant_id)s
       AND matter_id = %(matter_id)s
       AND artifact_id = %(artifact_id)s
     ORDER BY revision DESC
     LIMIT 1
),
eligible AS (
    SELECT artifact.id, projection.revision
      FROM sklegal_artifact.artifacts AS artifact
      JOIN current_projection AS projection ON true
     WHERE artifact.tenant_id = %(tenant_id)s
       AND artifact.matter_id = %(matter_id)s
       AND artifact.id = %(artifact_id)s
       AND projection.revision = %(expected_projection_revision)s
       AND projection.review_state <> 'superseded'
       AND NOT EXISTS (
           SELECT 1 FROM sklegal_artifact.artifact_supersessions
            WHERE tenant_id = artifact.tenant_id
              AND matter_id = artifact.matter_id
              AND superseded_artifact_id = artifact.id
       )
       AND NOT EXISTS (SELECT 1 FROM prior)
),
inserted_review AS (
    INSERT INTO sklegal_artifact.artifact_reviews (
        tenant_id, matter_id, id, artifact_id, artifact_projection_revision,
        decision, rationale, reviewer_principal_id, policy_decision_id,
        policy_revision, reviewed_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(review_id)s, id, revision,
           %(decision)s, %(rationale)s, %(principal_id)s,
           %(policy_decision_id)s, %(policy_revision)s, %(occurred_at)s
      FROM eligible
    RETURNING artifact_id, artifact_projection_revision
),
inserted_projection AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state,
        reviewed_by_principal_id, reviewed_at, recorded_at, projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, artifact_id,
           artifact_projection_revision + 1, %(decision)s, %(principal_id)s,
           %(occurred_at)s, %(occurred_at)s, %(projection_document)s::jsonb
      FROM inserted_review
    RETURNING artifact_id, revision
),
inserted_audit AS (
    INSERT INTO sklegal_artifact.artifact_audit_facts (
        tenant_id, matter_id, id, principal_id, action, resource_id,
        outcome, policy_decision_id, policy_revision, request_sha256, occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(audit_event_id)s,
           %(principal_id)s, 'artifact.review.recorded', artifact_id,
           'success', %(policy_decision_id)s, %(policy_revision)s,
           %(request_sha256)s, %(occurred_at)s
      FROM inserted_projection
    RETURNING id
),
inserted_outbox AS (
    INSERT INTO sklegal_artifact.artifact_outbox (
        tenant_id, matter_id, id, audit_event_id, destination,
        payload_sha256, available_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(outbox_id)s, id,
           COALESCE(%(outbox_destination)s, 'artifact.activity.local'),
           COALESCE(%(outbox_payload_sha256)s, %(request_sha256)s),
           %(occurred_at)s
      FROM inserted_audit
    RETURNING id
),
inserted_receipt AS (
    INSERT INTO sklegal_artifact.artifact_idempotency_receipts (
        tenant_id, matter_id, idempotency_key_sha256, operation,
        request_sha256, result_artifact_id, duplicate,
        projection_revision, created_at, response_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(idempotency_key_sha256)s,
           'review', %(request_sha256)s, artifact_id, false, revision,
           %(occurred_at)s, %(response_document)s::jsonb
      FROM inserted_projection
    RETURNING result_artifact_id, projection_revision
)
SELECT result_artifact_id AS artifact_id, projection_revision,
       false AS replayed, false AS conflict, false AS precondition
  FROM inserted_receipt
UNION ALL
SELECT result_artifact_id, projection_revision, true, false, false
  FROM prior
 WHERE operation = 'review' AND request_sha256 = %(request_sha256)s
UNION ALL
SELECT NULL::uuid, NULL::bigint, false, true, false
 WHERE (SELECT value FROM conflict)
UNION ALL
SELECT NULL::uuid, NULL::bigint, false, false, true
 WHERE NOT EXISTS (SELECT 1 FROM prior)
   AND NOT EXISTS (SELECT 1 FROM eligible)
"""

ATOMIC_ARTIFACT_SUPERSESSION_SQL = """
WITH prior AS (
    SELECT result_artifact_id, operation, request_sha256, projection_revision
      FROM sklegal_artifact.artifact_idempotency_receipts
     WHERE tenant_id = %(tenant_id)s AND matter_id = %(matter_id)s
       AND idempotency_key_sha256 = %(idempotency_key_sha256)s
),
conflict AS (
    SELECT EXISTS (
        SELECT 1 FROM prior
         WHERE operation <> 'supersede' OR request_sha256 <> %(request_sha256)s
    ) AS value
),
current_projection AS (
    SELECT revision, review_state
      FROM sklegal_artifact.artifact_projection_revisions
     WHERE tenant_id = %(tenant_id)s AND matter_id = %(matter_id)s
       AND artifact_id = %(artifact_id)s
     ORDER BY revision DESC LIMIT 1
),
eligible AS (
    SELECT target.id, projection.revision
      FROM sklegal_artifact.artifacts AS target
      JOIN sklegal_artifact.artifacts AS successor
        ON successor.tenant_id = target.tenant_id
       AND successor.matter_id = target.matter_id
       AND successor.id = %(successor_artifact_id)s
      JOIN current_projection AS projection ON true
     WHERE target.tenant_id = %(tenant_id)s
       AND target.matter_id = %(matter_id)s
       AND target.id = %(artifact_id)s
       AND target.artifact_kind = 'derived'
       AND successor.artifact_kind = 'derived'
       AND successor.original_sha256 = target.original_sha256
       AND projection.revision = %(expected_projection_revision)s
       AND projection.review_state <> 'superseded'
       AND NOT EXISTS (
           SELECT 1 FROM sklegal_artifact.artifact_supersessions
            WHERE tenant_id = target.tenant_id AND matter_id = target.matter_id
              AND superseded_artifact_id IN (target.id, successor.id)
       )
       AND NOT EXISTS (SELECT 1 FROM prior)
),
inserted_supersession AS (
    INSERT INTO sklegal_artifact.artifact_supersessions (
        tenant_id, matter_id, id, superseded_artifact_id,
        successor_artifact_id, reason, recorded_by_principal_id, recorded_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(supersession_id)s, id,
           %(successor_artifact_id)s, %(reason)s, %(principal_id)s, %(occurred_at)s
      FROM eligible
    RETURNING superseded_artifact_id
),
inserted_projection AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state,
        reviewed_by_principal_id, reviewed_at, recorded_at, projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, superseded_artifact_id,
           %(expected_projection_revision)s + 1, 'superseded', %(principal_id)s,
           %(occurred_at)s, %(occurred_at)s, %(projection_document)s::jsonb
      FROM inserted_supersession
    RETURNING artifact_id, revision
),
inserted_audit AS (
    INSERT INTO sklegal_artifact.artifact_audit_facts (
        tenant_id, matter_id, id, principal_id, action, resource_id,
        outcome, policy_decision_id, policy_revision, request_sha256, occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(audit_event_id)s, %(principal_id)s,
           'artifact.supersession.recorded', artifact_id, 'success',
           %(policy_decision_id)s, %(policy_revision)s, %(request_sha256)s,
           %(occurred_at)s
      FROM inserted_projection
    RETURNING id
),
inserted_outbox AS (
    INSERT INTO sklegal_artifact.artifact_outbox (
        tenant_id, matter_id, id, audit_event_id, destination,
        payload_sha256, available_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(outbox_id)s, id,
           COALESCE(%(outbox_destination)s, 'artifact.activity.local'),
           COALESCE(%(outbox_payload_sha256)s, %(request_sha256)s),
           %(occurred_at)s
      FROM inserted_audit RETURNING id
),
inserted_receipt AS (
    INSERT INTO sklegal_artifact.artifact_idempotency_receipts (
        tenant_id, matter_id, idempotency_key_sha256, operation, request_sha256,
        result_artifact_id, duplicate, projection_revision, created_at,
        response_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(idempotency_key_sha256)s,
           'supersede', %(request_sha256)s, artifact_id, false, revision,
           %(occurred_at)s, %(response_document)s::jsonb
      FROM inserted_projection
    RETURNING result_artifact_id, projection_revision
)
SELECT result_artifact_id AS artifact_id, projection_revision,
       false AS replayed, false AS conflict, false AS precondition
  FROM inserted_receipt
UNION ALL
SELECT result_artifact_id, projection_revision, true, false, false
  FROM prior
 WHERE operation = 'supersede' AND request_sha256 = %(request_sha256)s
UNION ALL
SELECT NULL::uuid, NULL::bigint, false, true, false
 WHERE (SELECT value FROM conflict)
UNION ALL
SELECT NULL::uuid, NULL::bigint, false, false, true
 WHERE NOT EXISTS (SELECT 1 FROM prior)
 AND NOT EXISTS (SELECT 1 FROM eligible)
"""

ATOMIC_ARTIFACT_CORRECTION_SQL = """
WITH prior AS (
    SELECT result_artifact_id, operation, request_sha256, projection_revision
      FROM sklegal_artifact.artifact_idempotency_receipts
     WHERE tenant_id = %(tenant_id)s AND matter_id = %(matter_id)s
       AND idempotency_key_sha256 = %(idempotency_key_sha256)s
),
conflict AS (
    SELECT EXISTS (
        SELECT 1 FROM prior
         WHERE operation <> 'correct' OR request_sha256 <> %(request_sha256)s
    ) AS value
),
current_projection AS (
    SELECT revision, review_state
      FROM sklegal_artifact.artifact_projection_revisions
     WHERE tenant_id = %(tenant_id)s AND matter_id = %(matter_id)s
       AND artifact_id = %(artifact_id)s
     ORDER BY revision DESC LIMIT 1
),
eligible AS (
    SELECT target.*, projection.revision
      FROM sklegal_artifact.artifacts AS target
      JOIN current_projection AS projection ON true
     WHERE target.tenant_id = %(tenant_id)s
       AND target.matter_id = %(matter_id)s
       AND target.id = %(artifact_id)s
       AND target.artifact_kind = 'derived'
       AND projection.revision = %(expected_projection_revision)s
       AND projection.review_state <> 'superseded'
       AND NOT EXISTS (
           SELECT 1 FROM sklegal_artifact.artifact_supersessions
            WHERE tenant_id = target.tenant_id AND matter_id = target.matter_id
              AND superseded_artifact_id = target.id
       )
       AND NOT EXISTS (SELECT 1 FROM prior)
),
inserted_corrected AS (
    INSERT INTO sklegal_artifact.artifacts (
        tenant_id, matter_id, id, artifact_kind, parent_artifact_id,
        source_identity, source_version, source_identity_sha256, filename,
        media_type, byte_count, content_sha256, original_sha256,
        storage_locator, acquisition_method, quarantine_state,
        extraction_state, classification, privilege_state,
        retention_policy_id, legal_hold_ids, ethical_wall_ids,
        policy_decision_id, policy_revision, projection_revision, created_at
    )
    SELECT tenant_id, matter_id, %(corrected_artifact_id)s, 'derived', id,
           source_identity, source_version, source_identity_sha256,
           %(corrected_filename)s, %(corrected_media_type)s,
           %(corrected_byte_count)s, %(corrected_content_sha256)s,
           original_sha256, %(corrected_storage_locator)s, 'human_correction',
           'released', 'complete', classification, privilege_state,
           retention_policy_id, legal_hold_ids, ethical_wall_ids,
           %(policy_decision_id)s, %(policy_revision)s, 1, %(occurred_at)s
      FROM eligible
    RETURNING id, parent_artifact_id, original_sha256
),
inserted_corrected_projection AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state,
        reviewed_by_principal_id, reviewed_at, recorded_at, projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, id, 1, 'accepted', %(principal_id)s,
           %(occurred_at)s, %(occurred_at)s, %(corrected_projection_document)s::jsonb
      FROM inserted_corrected
    RETURNING artifact_id, revision
),
inserted_derivation AS (
    INSERT INTO sklegal_artifact.artifact_derivations (
        tenant_id, matter_id, parent_artifact_id, child_artifact_id,
        derivation_kind, tool_name, tool_version, input_sha256,
        output_sha256, created_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, eligible.id, corrected.id,
           'human_correction', %(corrected_tool_name)s,
           %(corrected_tool_version)s, eligible.content_sha256,
           %(corrected_content_sha256)s, %(occurred_at)s
      FROM eligible
      JOIN inserted_corrected AS corrected ON corrected.parent_artifact_id = eligible.id
      JOIN inserted_corrected_projection AS projection
        ON projection.artifact_id = corrected.id
    RETURNING child_artifact_id
),
inserted_scan AS (
    INSERT INTO sklegal_artifact.artifact_scan_events (
        tenant_id, matter_id, id, artifact_id, scanner_name, scanner_version,
        signature_revision, scan_state, detail_code, scanned_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(corrected_scan_id)s,
           child_artifact_id, %(corrected_tool_name)s,
           %(corrected_tool_version)s, %(request_sha256)s,
           'clean', 'clean', %(occurred_at)s
      FROM inserted_derivation
    RETURNING artifact_id
),
inserted_custody AS (
    INSERT INTO sklegal_artifact.artifact_custody_events (
        tenant_id, matter_id, id, artifact_id, action,
        custodian_principal_id, source_identity, idempotency_key_sha256,
        occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(corrected_custody_event_id)s,
           scan.artifact_id, 'corrected', %(principal_id)s,
           eligible.source_identity, %(idempotency_key_sha256)s, %(occurred_at)s
      FROM inserted_scan AS scan
      JOIN eligible ON true
    RETURNING artifact_id
),
inserted_correction AS (
    INSERT INTO sklegal_artifact.artifact_corrections (
        tenant_id, matter_id, id, target_artifact_id, corrected_artifact_id,
        reason, corrected_by_principal_id, corrected_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(correction_id)s,
           %(artifact_id)s, child_artifact_id, %(reason)s,
           %(principal_id)s, %(occurred_at)s
      FROM inserted_derivation
    RETURNING target_artifact_id, corrected_artifact_id
),
inserted_supersession AS (
    INSERT INTO sklegal_artifact.artifact_supersessions (
        tenant_id, matter_id, id, superseded_artifact_id,
        successor_artifact_id, reason, recorded_by_principal_id, recorded_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(supersession_id)s,
           target_artifact_id, corrected_artifact_id, %(reason)s,
           %(principal_id)s, %(occurred_at)s
      FROM inserted_correction
    RETURNING superseded_artifact_id, successor_artifact_id
),
inserted_target_projection AS (
    INSERT INTO sklegal_artifact.artifact_projection_revisions (
        tenant_id, matter_id, artifact_id, revision, review_state,
        reviewed_by_principal_id, reviewed_at, recorded_at, projection_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, superseded_artifact_id,
           %(expected_projection_revision)s + 1, 'superseded', %(principal_id)s,
           %(occurred_at)s, %(occurred_at)s,
           %(target_projection_document)s::jsonb
      FROM inserted_supersession
    RETURNING artifact_id
),
inserted_audit AS (
    INSERT INTO sklegal_artifact.artifact_audit_facts (
        tenant_id, matter_id, id, principal_id, action, resource_id,
        outcome, policy_decision_id, policy_revision, request_sha256, occurred_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(audit_event_id)s, %(principal_id)s,
           'artifact.correction.recorded', supersession.successor_artifact_id,
           'success', %(policy_decision_id)s, %(policy_revision)s,
           %(request_sha256)s, %(occurred_at)s
      FROM inserted_supersession AS supersession
      JOIN inserted_target_projection ON true
    RETURNING id, resource_id
),
inserted_outbox AS (
    INSERT INTO sklegal_artifact.artifact_outbox (
        tenant_id, matter_id, id, audit_event_id, destination,
        payload_sha256, available_at
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(outbox_id)s, id,
           COALESCE(%(outbox_destination)s, 'artifact.activity.local'),
           COALESCE(%(outbox_payload_sha256)s, %(request_sha256)s),
           %(occurred_at)s
      FROM inserted_audit RETURNING id
),
inserted_receipt AS (
    INSERT INTO sklegal_artifact.artifact_idempotency_receipts (
        tenant_id, matter_id, idempotency_key_sha256, operation, request_sha256,
        result_artifact_id, duplicate, projection_revision, created_at,
        response_document
    )
    SELECT %(tenant_id)s, %(matter_id)s, %(idempotency_key_sha256)s,
           'correct', %(request_sha256)s, resource_id, false, 1,
           %(occurred_at)s, %(response_document)s::jsonb
      FROM inserted_audit
    RETURNING result_artifact_id, projection_revision
)
SELECT result_artifact_id AS artifact_id, projection_revision,
       false AS replayed, false AS conflict, false AS precondition
  FROM inserted_receipt
UNION ALL
SELECT result_artifact_id, projection_revision, true, false, false
  FROM prior
 WHERE operation = 'correct' AND request_sha256 = %(request_sha256)s
UNION ALL
SELECT NULL::uuid, NULL::bigint, false, true, false
 WHERE (SELECT value FROM conflict)
UNION ALL
SELECT NULL::uuid, NULL::bigint, false, false, true
 WHERE NOT EXISTS (SELECT 1 FROM prior)
   AND NOT EXISTS (SELECT 1 FROM eligible)
"""


class PostgresArtifactRepository:
    """Execute an artifact intake batch as one driver transaction and statement."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def ensure_ready(self) -> None:
        try:
            with self._session_factory() as session:
                rows = session.execute(ARTIFACT_STORE_READY_SQL, {})
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact persistence readiness unavailable"
            ) from exc
        if len(rows) != 1 or rows[0].get("ready") is not True:
            raise ArtifactPersistenceUnavailable(
                "artifact persistence readiness response is invalid"
            )

    def idempotency_document(
        self, tenant_id: UUID, matter_id: UUID, idempotency_key_sha256: str
    ) -> ArtifactDocumentReceipt | None:
        _require_sha256("idempotency_key_sha256", idempotency_key_sha256)
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    ARTIFACT_IDEMPOTENCY_DOCUMENT_SQL,
                    {
                        "tenant_id": tenant_id,
                        "matter_id": matter_id,
                        "idempotency_key_sha256": idempotency_key_sha256,
                    },
                )
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact idempotency read unavailable"
            ) from exc
        if not rows:
            return None
        if len(rows) != 1:
            raise ArtifactPersistenceUnavailable(
                "artifact idempotency read returned multiple rows"
            )
        request_sha256 = rows[0].get("request_sha256")
        response_document = rows[0].get("response_document")
        if not isinstance(request_sha256, str) or not isinstance(
            response_document, str
        ):
            raise ArtifactPersistenceUnavailable(
                "artifact idempotency document is incomplete"
            )
        return ArtifactDocumentReceipt(request_sha256, response_document)

    def projection_documents(
        self,
        tenant_id: UUID,
        matter_id: UUID,
        *,
        artifact_id: UUID | None = None,
        content_sha256: str | None = None,
    ) -> tuple[str, ...]:
        if content_sha256 is not None:
            _require_sha256("content_sha256", content_sha256)
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    ARTIFACT_PROJECTION_DOCUMENTS_SQL,
                    {
                        "tenant_id": tenant_id,
                        "matter_id": matter_id,
                        "artifact_id": artifact_id,
                        "content_sha256": content_sha256,
                    },
                )
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact projection read unavailable"
            ) from exc
        documents: list[str] = []
        for row in rows:
            document = row.get("projection_document")
            if not isinstance(document, str):
                raise ArtifactPersistenceUnavailable(
                    "artifact projection document is incomplete"
                )
            _require_json_object("projection_document", document)
            documents.append(document)
        return tuple(documents)

    def append_read_audit(self, batch: ArtifactReadAuditBatch) -> None:
        try:
            with self._session_factory() as session:
                rows = session.execute(ATOMIC_ARTIFACT_READ_AUDIT_SQL, asdict(batch))
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact read audit transaction unavailable"
            ) from exc
        if len(rows) != 1 or rows[0].get("outbox_id") != batch.outbox_id:
            raise ArtifactPersistenceUnavailable(
                "artifact read audit transaction returned an invalid receipt"
            )

    @staticmethod
    def _parameters(batch: ArtifactPersistenceBatch) -> dict[str, object]:
        values: dict[str, object] = {
            name: value
            for name, value in asdict(batch).items()
            if name not in {"derived", "proposed_links"}
        }
        derived: list[dict[str, object]] = []
        for item in batch.derived:
            row: dict[str, object] = asdict(item)
            document = row.get("projection_document")
            if isinstance(document, str):
                row["projection_document"] = json.loads(document)
            derived.append(row)
        values["derived"] = derived
        values["proposed_links"] = [asdict(item) for item in batch.proposed_links]
        return values

    @staticmethod
    def _mutation_parameters(context: ArtifactMutationContext) -> dict[str, object]:
        return asdict(context)

    @staticmethod
    def _lifecycle_receipt(
        rows: Sequence[Mapping[str, object]],
    ) -> ArtifactPersistenceReceipt:
        if len(rows) != 1:
            raise ArtifactPersistenceUnavailable(
                "artifact persistence returned an invalid lifecycle receipt"
            )
        row = rows[0]
        if row.get("conflict") is True:
            raise ArtifactPersistenceConflict("artifact idempotency conflict")
        if row.get("precondition") is True:
            raise ArtifactPersistencePrecondition(
                "artifact lifecycle precondition failed"
            )
        artifact_id = row.get("artifact_id")
        projection_revision = row.get("projection_revision")
        if not isinstance(artifact_id, UUID) or not isinstance(
            projection_revision, int
        ):
            raise ArtifactPersistenceUnavailable(
                "artifact lifecycle receipt is incomplete"
            )
        return ArtifactPersistenceReceipt(
            artifact_id=artifact_id,
            duplicate=False,
            replayed=row.get("replayed") is True,
            projection_revision=projection_revision,
        )

    def append_intake(
        self, batch: ArtifactPersistenceBatch
    ) -> ArtifactPersistenceReceipt:
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    ATOMIC_ARTIFACT_INTAKE_SQL, self._parameters(batch)
                )
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact persistence transaction unavailable"
            ) from exc
        if len(rows) != 1:
            raise ArtifactPersistenceUnavailable(
                "artifact persistence returned an invalid receipt"
            )
        row = rows[0]
        if row.get("conflict") is True:
            raise ArtifactPersistenceConflict("artifact idempotency conflict")
        artifact_id = row.get("artifact_id")
        projection_revision = row.get("projection_revision")
        if not isinstance(artifact_id, UUID) or not isinstance(
            projection_revision, int
        ):
            raise ArtifactPersistenceUnavailable(
                "artifact persistence receipt is incomplete"
            )
        return ArtifactPersistenceReceipt(
            artifact_id=artifact_id,
            duplicate=row.get("duplicate") is True,
            replayed=row.get("replayed") is True,
            projection_revision=projection_revision,
        )

    def read(self, context: ArtifactReadContext) -> ArtifactPersistenceRead | None:
        try:
            with self._session_factory() as session:
                rows = session.execute(ATOMIC_ARTIFACT_READ_SQL, asdict(context))
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact audited read unavailable"
            ) from exc
        if not rows:
            return None
        if len(rows) != 1:
            raise ArtifactPersistenceUnavailable(
                "artifact persistence returned an invalid audited read"
            )
        try:
            return ArtifactPersistenceRead(**dict(rows[0]))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact audited read is incomplete"
            ) from exc

    def append_review(self, batch: ArtifactReviewBatch) -> ArtifactPersistenceReceipt:
        parameters = self._mutation_parameters(batch.context)
        parameters.update(
            {
                "review_id": batch.review_id,
                "decision": batch.decision,
                "rationale": batch.rationale,
                "projection_document": batch.projection_document,
                "response_document": batch.response_document,
            }
        )
        try:
            with self._session_factory() as session:
                rows = session.execute(ATOMIC_ARTIFACT_REVIEW_SQL, parameters)
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact review transaction unavailable"
            ) from exc
        return self._lifecycle_receipt(rows)

    def append_supersession(
        self, batch: ArtifactSupersessionBatch
    ) -> ArtifactPersistenceReceipt:
        parameters = self._mutation_parameters(batch.context)
        parameters.update(
            {
                "supersession_id": batch.supersession_id,
                "successor_artifact_id": batch.successor_artifact_id,
                "reason": batch.reason,
                "projection_document": batch.projection_document,
                "response_document": batch.response_document,
            }
        )
        try:
            with self._session_factory() as session:
                rows = session.execute(ATOMIC_ARTIFACT_SUPERSESSION_SQL, parameters)
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact supersession transaction unavailable"
            ) from exc
        return self._lifecycle_receipt(rows)

    def append_correction(
        self, batch: ArtifactCorrectionBatch
    ) -> ArtifactPersistenceReceipt:
        parameters = self._mutation_parameters(batch.context)
        parameters.update(
            {
                "correction_id": batch.correction_id,
                "supersession_id": batch.supersession_id,
                "reason": batch.reason,
                "target_projection_document": batch.target_projection_document,
                "response_document": batch.response_document,
            }
        )
        parameters.update(
            {
                f"corrected_{name}": value
                for name, value in asdict(batch.corrected).items()
            }
        )
        try:
            with self._session_factory() as session:
                rows = session.execute(ATOMIC_ARTIFACT_CORRECTION_SQL, parameters)
        except Exception as exc:
            raise ArtifactPersistenceUnavailable(
                "artifact correction transaction unavailable"
            ) from exc
        return self._lifecycle_receipt(rows)
