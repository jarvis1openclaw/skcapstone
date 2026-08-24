export type WorkProductStatus =
  "draft" | "in_review" | "validated" | "approved" | "withdrawn";

export type ApprovalStatus =
  "pending" | "approved" | "rejected" | "revoked" | "superseded";

export interface VersionBinding {
  workProductVersionId: string;
  versionNumber: number;
  contentSha256: string;
}

export interface WorkProductVersion {
  versionId: string;
  versionNumber: number;
  content: string;
  contentSha256: string;
  status: "active" | "superseded";
  createdAt: string;
}

export interface SentenceGrounding {
  groundingId: string;
  binding: VersionBinding;
  sentenceKey: string;
  claimId: string;
  sourceSpanStart: number;
  sourceSpanEnd: number;
}

export interface ValidationRecord {
  validationId: string;
  binding: VersionBinding;
  outcome: "passed" | "failed";
  checkIds: string[];
  missingSentenceKeys: string[];
  rationale: string;
  policyRevision: string;
}

export interface ApprovalRecord {
  approvalId: string;
  binding: VersionBinding;
  validationId: string;
  status: ApprovalStatus;
  requestedAt: string;
  reviewerPrincipalId?: string | null;
  decidedAt?: string | null;
  rationale?: string | null;
  revokedAt?: string | null;
  revocationRationale?: string | null;
  supersededAt?: string | null;
  supersededByApprovalId?: string | null;
  supersedingVersionId?: string | null;
}

export interface WorkProductAggregate {
  schemaVersion: "sklegal.work-product-aggregate/v1";
  tenantId: string;
  matterId: string;
  workProductId: string;
  aggregateVersion: number;
  title: string;
  workProductKind: string;
  status: WorkProductStatus;
  currentVersionId: string;
  versions: WorkProductVersion[];
  groundings: SentenceGrounding[];
  validations: ValidationRecord[];
  approvals: ApprovalRecord[];
  createdAt: string;
  updatedAt: string;
}

export interface WorkProductComparison {
  workProductId: string;
  left: VersionBinding;
  right: VersionBinding;
  unifiedDiff: string[];
  changed: boolean;
}

export interface ApprovalValidity {
  approvalId: string;
  binding: VersionBinding;
  valid: boolean;
  reasonCode: string;
}

const digest = /^[0-9a-f]{64}$/;

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function integer(value: unknown, label: string): number {
  if (!Number.isInteger(value) || Number(value) < 1) {
    throw new Error(`${label} must be a positive integer`);
  }
  return Number(value);
}

function binding(value: unknown, label: string): VersionBinding {
  const row = object(value, label);
  const contentSha256 = string(row.contentSha256, `${label}.contentSha256`);
  if (!digest.test(contentSha256)) {
    throw new Error(`${label}.contentSha256 must be a SHA-256 digest`);
  }
  return {
    workProductVersionId: string(
      row.workProductVersionId,
      `${label}.workProductVersionId`,
    ),
    versionNumber: integer(row.versionNumber, `${label}.versionNumber`),
    contentSha256,
  };
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array`);
  }
  return value;
}

export function parseWorkProduct(value: unknown): WorkProductAggregate {
  const row = object(value, "Work Product");
  if (row.schemaVersion !== "sklegal.work-product-aggregate/v1") {
    throw new Error("unsupported Work Product schema version");
  }
  const versions = array(row.versions, "versions").map((item, index) => {
    const version = object(item, `versions[${index}]`);
    const contentSha256 = string(
      version.contentSha256,
      `versions[${index}].contentSha256`,
    );
    if (!digest.test(contentSha256)) {
      throw new Error(
        `versions[${index}].contentSha256 must be a SHA-256 digest`,
      );
    }
    if (version.status !== "active" && version.status !== "superseded") {
      throw new Error(`versions[${index}].status is invalid`);
    }
    return {
      versionId: string(version.versionId, `versions[${index}].versionId`),
      versionNumber: integer(
        version.versionNumber,
        `versions[${index}].versionNumber`,
      ),
      content: string(version.content, `versions[${index}].content`),
      contentSha256,
      status: version.status,
      createdAt: string(version.createdAt, `versions[${index}].createdAt`),
    } satisfies WorkProductVersion;
  });
  const currentVersionId = string(row.currentVersionId, "currentVersionId");
  if (
    versions.filter(
      (item) => item.versionId === currentVersionId && item.status === "active",
    ).length !== 1
  ) {
    throw new Error(
      "current Work Product version is not exactly one active version",
    );
  }
  const statuses: WorkProductStatus[] = [
    "draft",
    "in_review",
    "validated",
    "approved",
    "withdrawn",
  ];
  if (!statuses.includes(row.status as WorkProductStatus)) {
    throw new Error("Work Product status is invalid");
  }
  const groundings = array(row.groundings, "groundings").map((item, index) => {
    const grounding = object(item, `groundings[${index}]`);
    return {
      groundingId: string(grounding.groundingId, "groundingId"),
      binding: binding(grounding.binding, "grounding.binding"),
      sentenceKey: string(grounding.sentenceKey, "grounding.sentenceKey"),
      claimId: string(grounding.claimId, "grounding.claimId"),
      sourceSpanStart: Number(grounding.sourceSpanStart),
      sourceSpanEnd: Number(grounding.sourceSpanEnd),
    } satisfies SentenceGrounding;
  });
  const validations = array(row.validations, "validations").map(
    (item, index) => {
      const validation = object(item, `validations[${index}]`);
      if (validation.outcome !== "passed" && validation.outcome !== "failed") {
        throw new Error("validation outcome is invalid");
      }
      return {
        validationId: string(validation.validationId, "validationId"),
        binding: binding(validation.binding, "validation.binding"),
        outcome: validation.outcome,
        checkIds: array(validation.checkIds, "validation.checkIds").map(
          (item) => string(item, "validation.checkId"),
        ),
        missingSentenceKeys: array(
          validation.missingSentenceKeys,
          "validation.missingSentenceKeys",
        ).map((item) => string(item, "validation.missingSentenceKey")),
        rationale: string(validation.rationale, "validation.rationale"),
        policyRevision: string(
          validation.policyRevision,
          "validation.policyRevision",
        ),
      } satisfies ValidationRecord;
    },
  );
  const approvals = array(row.approvals, "approvals").map((item, index) => {
    const approval = object(item, `approvals[${index}]`);
    const approvalStatuses: ApprovalStatus[] = [
      "pending",
      "approved",
      "rejected",
      "revoked",
      "superseded",
    ];
    if (!approvalStatuses.includes(approval.status as ApprovalStatus)) {
      throw new Error("Approval status is invalid");
    }
    return {
      approvalId: string(approval.approvalId, "approvalId"),
      binding: binding(approval.binding, "approval.binding"),
      validationId: string(approval.validationId, "approval.validationId"),
      status: approval.status as ApprovalStatus,
      requestedAt: string(approval.requestedAt, "approval.requestedAt"),
      reviewerPrincipalId: approval.reviewerPrincipalId as string | null,
      decidedAt: approval.decidedAt as string | null,
      rationale: approval.rationale as string | null,
      revokedAt: approval.revokedAt as string | null,
      revocationRationale: approval.revocationRationale as string | null,
      supersededAt: approval.supersededAt as string | null,
      supersededByApprovalId: approval.supersededByApprovalId as string | null,
      supersedingVersionId: approval.supersedingVersionId as string | null,
    } satisfies ApprovalRecord;
  });
  return {
    schemaVersion: "sklegal.work-product-aggregate/v1",
    tenantId: string(row.tenantId, "tenantId"),
    matterId: string(row.matterId, "matterId"),
    workProductId: string(row.workProductId, "workProductId"),
    aggregateVersion: integer(row.aggregateVersion, "aggregateVersion"),
    title: string(row.title, "title"),
    workProductKind: string(row.workProductKind, "workProductKind"),
    status: row.status as WorkProductStatus,
    currentVersionId,
    versions,
    groundings,
    validations,
    approvals,
    createdAt: string(row.createdAt, "createdAt"),
    updatedAt: string(row.updatedAt, "updatedAt"),
  };
}

export function parseWorkProducts(value: unknown): WorkProductAggregate[] {
  return array(value, "Work Products").map(parseWorkProduct);
}

export function parseComparison(value: unknown): WorkProductComparison {
  const row = object(value, "comparison");
  if (typeof row.changed !== "boolean") {
    throw new Error("comparison.changed must be boolean");
  }
  return {
    workProductId: string(row.workProductId, "comparison.workProductId"),
    left: binding(row.left, "comparison.left"),
    right: binding(row.right, "comparison.right"),
    unifiedDiff: array(row.unifiedDiff, "comparison.unifiedDiff").map((item) =>
      String(item),
    ),
    changed: row.changed,
  };
}

export function parseApprovalValidity(value: unknown): ApprovalValidity {
  const row = object(value, "Approval validity");
  if (typeof row.valid !== "boolean") {
    throw new Error("Approval validity must be boolean");
  }
  return {
    approvalId: string(row.approvalId, "approvalId"),
    binding: binding(row.binding, "binding"),
    valid: row.valid,
    reasonCode: string(row.reasonCode, "reasonCode"),
  };
}
