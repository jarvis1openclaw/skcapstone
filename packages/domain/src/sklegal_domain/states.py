"""Legal-domain enumerations with no persistence or presentation coupling."""

from enum import StrEnum


class RecordCompleteness(StrEnum):
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"
    UNRESOLVED = "unresolved"


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PRIVILEGED_WORK_PRODUCT = "privileged_work_product"
    HIGHLY_RESTRICTED = "highly_restricted"


class TenantStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class ClientStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSED = "closed"


class EngagementStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class MatterStatus(StrEnum):
    PROPOSED = "proposed"
    OPEN = "open"
    ON_HOLD = "on_hold"
    CLOSED = "closed"
    ARCHIVED = "archived"


class VerificationStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"


class PartyRoleStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    INACTIVE = "inactive"


class ProceedingStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    STAYED = "stayed"
    DISPOSED = "disposed"
    CLOSED = "closed"


class MatterEventStatus(StrEnum):
    PROPOSED = "proposed"
    RECORDED = "recorded"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"


class TransactionStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"


class FactReviewStatus(StrEnum):
    SOURCE_ASSERTED = "source_asserted"
    AMBIGUOUS = "ambiguous"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"


class TensionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class EvidenceStatus(StrEnum):
    PROPOSED = "proposed"
    COLLECTED = "collected"
    VERIFIED = "verified"
    CHALLENGED = "challenged"
    EXCLUDED = "excluded"
    SUPERSEDED = "superseded"


class AuthorityStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    CHALLENGED = "challenged"
    NOT_APPLICABLE = "not_applicable"
    SUPERSEDED = "superseded"


class IssueStatus(StrEnum):
    IDENTIFIED = "identified"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DEFERRED = "deferred"


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    CHALLENGED = "challenged"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ElementStatus(StrEnum):
    ALLEGED = "alleged"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    NOT_ESTABLISHED = "not_established"


class RemedyStatus(StrEnum):
    PROPOSED = "proposed"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AWARDED = "awarded"
    DENIED = "denied"


class DeadlineStatus(StrEnum):
    CANDIDATE = "candidate"
    REVIEWED = "reviewed"
    OPERATIVE = "operative"
    SATISFIED = "satisfied"
    MISSED = "missed"
    WITHDRAWN = "withdrawn"


class TaskStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CommunicationStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RECEIPT_VERIFIED = "receipt_verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkProductStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    VALIDATED = "validated"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class WorkProductVersionStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"
    SUPERSEDED = "superseded"


class ValidationOutcome(StrEnum):
    INCOMPLETE = "incomplete"
    PASSED = "passed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ExecutionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RECEIPT_VERIFIED = "receipt_verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStep(StrEnum):
    VALIDATED = "validated"
    APPROVED = "approved"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RECEIPT_VERIFIED = "receipt_verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LegacyRecordKind(StrEnum):
    """Historical storage labels that never define canonical entity types."""

    CONTAINER = "problem"
    ACTIVITY = "incident"
