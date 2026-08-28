export type ActivitySourceKind =
  | "audit_event"
  | "matter_event"
  | "workflow_reference"
  | "agent_run"
  | "tool_call"
  | "source_reference"
  | "work_product_version"
  | "approval"
  | "execution_event"
  | "receipt"
  | "correction";

export interface ActivitySource {
  kind: ActivitySourceKind;
  sourceId: string;
  sourceVersion: number;
  sourceSha256: string;
  status: string;
  recordedAt: string;
  correctsSourceId: string | null;
  supersededBySourceId: string | null;
  supersededBySourceVersion: number | null;
}

export interface ActivityTrace {
  correlationId: string;
  causationId: string | null;
  runId: string;
  workflowReferenceId: string | null;
  agentRunId: string | null;
  toolCallId: string | null;
}

export interface MatterActivityItem {
  activityId: string;
  tenantId: string;
  matterId: string;
  eventSequence: number;
  eventSha256: string;
  previousEventSha256: string | null;
  chainStatus: "verified";
  action: string;
  boundary: "api" | "workflow" | "tool" | "model" | "human" | "connector";
  outcome: "allow" | "deny" | "success" | "failure";
  reasonCode: string;
  occurredAt: string;
  recordedAt: string;
  actorPrincipalId: string;
  authorizationDecisionId: string | null;
  policyDecisionId: string | null;
  trace: ActivityTrace;
  source: ActivitySource;
}

export interface ActivityWatermark {
  projection: "matter_activity.v1";
  tenantHeadSequence: number;
  tenantHeadSha256: string;
  projectedSequence: number;
  projectedEventSha256: string;
  lagEvents: number;
  verifiedAt: string;
}

export interface MatterActivityPage {
  version: "sklegal-matter-activity/v1";
  tenantId: string;
  matterId: string;
  items: readonly MatterActivityItem[];
  nextCursor: string | null;
  snapshotSequence: number;
  snapshotSha256: string;
  watermark: ActivityWatermark;
}

export interface ActivityExportProposal {
  proposalId: string;
  status: "proposed";
  title: string;
  itemCount: number;
  selectionSha256: string;
  contentSha256: string;
  approvalId: null;
  dispatchState: "not_requested";
}

export type MatterActivityState =
  | { status: "loading" }
  | { status: "unavailable"; correlationId: string }
  | { status: "success"; page: MatterActivityPage };
