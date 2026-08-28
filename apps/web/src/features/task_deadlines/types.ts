export type TaskState =
  | "draft"
  | "ready"
  | "in_progress"
  | "blocked"
  | "completed"
  | "cancelled"
  | "failed"
  | "retry_pending"
  | "reconciliation_required"
  | "reconciled";

export type DeadlineState =
  | "blocked"
  | "uncertain"
  | "calculated"
  | "reviewed"
  | "operative"
  | "cancelled"
  | "failed"
  | "retry_pending"
  | "reconciliation_required"
  | "reconciled"
  | "superseded";

export interface ProvenanceReference {
  provenanceId: string;
  sourceKind: string;
  sourceId: string;
  sourceVersion: number;
  sourceSha256: string;
  recordedAt: string;
}

export interface TaskRead {
  schemaVersion: "sklegal.task/v1";
  tenantId: string;
  matterId: string;
  taskId: string;
  version: number;
  title: string;
  description: string;
  status: TaskState;
  assignedPrincipalId: string | null;
  deadlineId: string | null;
  dueAt: string | null;
  blockedReason: string | null;
  failureCode: string | null;
  retryOfVersion: number | null;
  reconciliationOfVersion: number | null;
  policyDecisionId: string;
  policyRevision: string;
  updatedAt: string;
  auditId: string;
  outboxId: string;
  provenance: ProvenanceReference[];
}

export interface DeadlineRead {
  schemaVersion: "sklegal.deadline/v1";
  tenantId: string;
  matterId: string;
  deadlineId: string;
  version: number;
  title: string;
  state: DeadlineState;
  reviewState: "pending" | "accepted" | "rejected";
  trigger: {
    state: "confirmed" | "missing" | "disputed";
    triggerEventId: string | null;
    factAssertionId: string | null;
    occurredAt: string | null;
    evidenceSha256: string | null;
  };
  rule: {
    state: "current" | "missing" | "stale" | "uncertain";
    ruleId: string;
    ruleVersion: string;
    ruleSha256: string;
    authorityId: string | null;
    authorityVersion: string | null;
    authorityContentSha256: string | null;
    authoritySpanSha256: string | null;
    intervalDays: number;
    convention: "calendar_days" | "business_days";
  };
  calendar: {
    state: "current" | "missing" | "stale" | "uncertain";
    calendarId: string | null;
    revision: string | null;
    timeZone: string;
    holidays: string[];
  };
  candidateDueAt: string | null;
  operativeDueAt: string | null;
  calculationSha256: string;
  uncertaintyCodes: string[];
  reminders: Array<{
    reminderId: string;
    scheduledFor: string;
    offsetDays: number;
    channel: "internal";
    state: string;
    externalEffect: false;
  }>;
  reviewedByPrincipalId: string | null;
  reviewedAt: string | null;
  reviewRationale: string | null;
  failureCode: string | null;
  retryOfVersion: number | null;
  reconciliationOfVersion: number | null;
  policyDecisionId: string;
  policyRevision: string;
  updatedAt: string;
  auditId: string;
  outboxId: string;
  provenance: ProvenanceReference[];
}

export interface SimulationReceipt {
  schemaVersion: "sklegal.action-simulation/v1";
  receiptId: string;
  taskId: string;
  deadlineId: string | null;
  state: "simulated";
  outcome:
    | "succeeded"
    | "cancelled"
    | "failed"
    | "retry_pending"
    | "reconciliation_required"
    | "reconciled";
  externalEffect: false;
  connectorInvoked: false;
  dispatchAttempted: false;
  workProductId: string;
  workProductVersionId: string;
  workProductVersionNumber: number;
  workProductContentSha256: string;
  approvalId: string;
  approvalSnapshotSha256: string;
  destinationSha256: string;
  policyDecisionId: string;
  policyRevision: string;
  auditId: string;
  outboxId: string;
}

export interface MutationReceipt<T> {
  idempotencyKeySha256: string;
  requestSha256: string;
  resourceVersion: number;
  auditId: string;
  outboxId: string;
  correlationId: string;
  task?: T;
  deadline?: T;
  simulation?: T;
}

export type TaskDeadlineErrorCode =
  | "authentication_required"
  | "access_denied"
  | "validation_failed"
  | "precondition_failed"
  | "idempotency_conflict"
  | "policy_unavailable"
  | "dependency_unavailable"
  | "resource_unavailable"
  | "internal_error";
