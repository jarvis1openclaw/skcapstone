export type Sha256 = string;

export interface SnapshotPins {
  matterSnapshotSha256: Sha256;
  corpusReleaseId: string;
  corpusSnapshotSha256: Sha256;
  authoritySnapshotSha256: Sha256;
  policySnapshotSha256: Sha256;
}

export interface AgentSpecificationEvidence {
  specId: string;
  specVersion: number;
  specSha256: Sha256;
  deploymentRevision: string;
  deploymentSha256: Sha256;
}

export interface ModelRouteEvidence {
  logicalRouteId: string;
  transportProfileRevision: string;
  transportProfileSha256: Sha256;
  gatewayRevision: string;
  gatewayConfigSha256: Sha256;
  catalogRevision: string;
  routePolicyRevision: string;
  gatewayRequestId: string;
  backend: string;
  capacityDomain: string;
  requestedModelOrBucket: string;
  bucketMember: string;
  servedModelName: string;
  servedModelRevision: string;
  retryCount: number;
  failoverCount: number;
  saturated: boolean;
  promptTokens: number;
  completionTokens: number;
  latencyMs: number;
}

export interface AuthorizationEvidence {
  decisionId: string;
  correlationId: string;
  principalId: string;
  capability: string;
  purpose: string;
  verifierPolicyVersion: string;
  revocationRevision: string;
  credentialDigest: Sha256;
  allowed: true;
}

export interface AnalysisRequestRecord {
  schemaVersion: "sklegal.agent-analysis-request/v1";
  tenantId: string;
  matterId: string;
  principalId: string;
  requestId: string;
  idempotencyKey: string;
  analysisKind:
    "matter_analysis" | "recommendation_refresh" | "blind_challenge";
  purpose: string;
  classification: "public";
  publicSynthetic: true;
  promptTemplateId: string;
  promptTemplateSha256: Sha256;
  outputSchemaId: string;
  outputSchemaSha256: Sha256;
  scoringPolicyId: string;
  scoringPolicySha256: Sha256;
  retryOfRunId: string | null;
  snapshots: SnapshotPins;
  agentSpecification: AgentSpecificationEvidence;
  requestedLogicalRouteId: string;
}

export interface ScoreDimension {
  dimension: "evidentiary_support" | "procedural_fit" | "urgency" | "readiness";
  value: number;
  rationaleSha256: Sha256;
}

export interface SourceRoleEvidence {
  sourceRole:
    | "course_instruction"
    | "current_authority"
    | "matter_record"
    | "model_inference";
  sourceId: string;
  sourceVersion: string;
  sourceSha256: Sha256;
  exactLocator: string;
  retrievalTraceSha256: Sha256;
  verificationState:
    | "source_derived"
    | "official_verified"
    | "matter_recorded"
    | "model_proposed";
}

export interface RecommendationRecord {
  recommendationId: string;
  version: number;
  targetKind: "issue" | "claim" | "defense" | "element" | "proceeding";
  targetId: string;
  proceedingPhase: string;
  proposedOutput: "task" | "work_product";
  reason: string;
  urgency: "low" | "medium" | "high" | "critical";
  prerequisites: string[];
  prohibitedSequencing: string[];
  scoreDimensions: ScoreDimension[];
  scoreTotal: number;
  confidenceBasisPoints: number;
  evidence: SourceRoleEvidence[];
  requiredCapability: string;
  downstreamHumanGate: string;
  reviewState: "pending" | "challenged" | "disposed";
}

export interface ChallengeDefectRecord {
  defectKind:
    | "source_mismatch"
    | "contrary_authority"
    | "missing_fact"
    | "procedural_sequence"
    | "scoring_error"
    | "unsupported_inference";
  description: string;
  evidenceSha256: Sha256;
}

export interface BlindChallengeRecord {
  challengeId: string;
  version: number;
  recommendationId: string;
  recommendationVersion: number;
  challengerSpecId: string;
  challengerSpecVersion: number;
  challengerSpecSha256: Sha256;
  challengerRoute: ModelRouteEvidence;
  blindInputSha256: Sha256;
  independentOutputSha256: Sha256;
  authorization: AuthorizationEvidence;
  sawChallengedConclusion: false;
  outcome: "no_defect" | "defect_found";
  defects: ChallengeDefectRecord[];
  createdAt: string;
}

export type HumanDispositionDecision =
  | "accept_as_proposed_task"
  | "request_work_product_proposal"
  | "reject"
  | "changes_requested";

export interface HumanDispositionRecord {
  dispositionId: string;
  version: number;
  recommendationId: string;
  recommendationVersion: number;
  decision: HumanDispositionDecision;
  reviewerPrincipalId: string;
  rationale: string;
  policyRevision: string;
  capabilityDecisionId: string;
  authorization: AuthorizationEvidence;
  decidedAt: string;
  createsDomainRecord: false;
  externalEffect: false;
}

export interface AgentRunAttemptRecord {
  attemptId: string;
  attemptNumber: number;
  startedAt: string;
  completedAt: string;
  outcome: "completed" | "failed" | "cancelled" | "timed_out";
  errorCode: string | null;
  retryable: boolean;
}

export interface ToolCallEvidence {
  toolCallId: string;
  sequence: number;
  toolId: string;
  capabilityDecisionId: string;
  argumentsSha256: Sha256;
  resultSha256: Sha256;
  startedAt: string;
  completedAt: string;
  outcome: "completed" | "denied" | "failed";
  errorCode: string | null;
}

export interface AgentRunRecord {
  schemaVersion: "sklegal.agent-run/v1";
  runId: string;
  version: number;
  tenantId: string;
  matterId: string;
  request: AnalysisRequestRecord;
  status: "completed" | "failed" | "cancelled" | "timed_out";
  workflowId: string;
  workflowRevision: string;
  authorization: AuthorizationEvidence;
  route: ModelRouteEvidence | null;
  attempts: AgentRunAttemptRecord[];
  toolCalls: ToolCallEvidence[];
  recommendations: RecommendationRecord[];
  challenges: BlindChallengeRecord[];
  dispositions: HumanDispositionRecord[];
  proposalPayloadSha256: Sha256 | null;
  auditEventIds: string[];
  createdAt: string;
  updatedAt: string;
}

export interface AnalysisRequestCommand {
  schemaVersion: "sklegal.agent-analysis-command/v1";
  analysisKind: "matter_analysis" | "recommendation_refresh";
  purpose: string;
  classification: "public";
  publicSynthetic: true;
  promptTemplateId: string;
  promptTemplateSha256: Sha256;
  outputSchemaId: string;
  outputSchemaSha256: Sha256;
  scoringPolicyId: string;
  scoringPolicySha256: Sha256;
  retryOfRunId: string | null;
  snapshots: SnapshotPins;
  agentSpecification: AgentSpecificationEvidence;
  requestedLogicalRouteId: string;
}

export interface BlindChallengeCommand {
  schemaVersion: "sklegal.agent-blind-challenge-command/v1";
  expectedRunVersion: number;
  recommendationId: string;
  recommendationVersion: number;
  challengerSpecId: string;
  challengerSpecVersion: number;
  challengerSpecSha256: Sha256;
  requestedLogicalRouteId: string;
}

export interface HumanDispositionCommand {
  schemaVersion: "sklegal.agent-human-disposition-command/v1";
  expectedRunVersion: number;
  recommendationId: string;
  recommendationVersion: number;
  decision: HumanDispositionDecision;
  rationale: string;
  policyRevision: string;
}
