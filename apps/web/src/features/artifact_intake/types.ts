export type ArtifactClassification =
  | "public"
  | "internal"
  | "confidential"
  | "privileged_work_product"
  | "highly_restricted";

export type ArtifactErrorCode =
  | "authentication_required"
  | "access_denied"
  | "validation_failed"
  | "precondition_failed"
  | "idempotency_conflict"
  | "policy_unavailable"
  | "dependency_unavailable"
  | "resource_unavailable"
  | "internal_error";

export interface ArtifactSource {
  sourceSystem: "public_synthetic" | "hammertime_contract";
  sourceIdentity: string;
  sourceVersion: string;
  observedAt: string;
}

export interface OriginalArtifactCommand {
  filename: string;
  mediaType: string;
  byteCount: number;
  contentSha256: string;
  contentBase64: string;
}

export interface ArtifactDerivationRequest {
  kind: "text_extraction" | "ocr" | "transcript";
  toolName: string;
  toolVersion: string;
  outputMediaType: string;
}

export interface ProposedArtifactLinkCommand {
  targetType:
    | "matter_event"
    | "communication"
    | "fact_assertion"
    | "evidence_item"
    | "issue"
    | "claim"
    | "element"
    | "task"
    | "work_product";
  targetId: string;
  rationale: string;
  reviewState: "proposed";
}

export interface ArtifactIntakeCommand {
  source: ArtifactSource;
  original: OriginalArtifactCommand;
  acquisitionMethod: "synthetic_adapter";
  classification: "public";
  privilegeState: "not_privileged";
  retentionPolicyId: string;
  legalHoldIds: readonly string[];
  ethicalWallIds: readonly string[];
  requestedDerivations: readonly ArtifactDerivationRequest[];
  proposedLinks: readonly ProposedArtifactLinkCommand[];
}

export interface ArtifactScan {
  scanId: string;
  scannerName: string;
  scannerVersion: string;
  signatureRevision: string;
  state: "pending" | "clean" | "unsafe" | "failed";
  scannedAt: string;
  detailCode: "clean" | "pending" | "unsafe" | "scanner_failed";
}

export interface ArtifactCustodyEvent {
  custodyEventId: string;
  action: "acquired" | "duplicate_observed" | "derived" | "corrected";
  custodianPrincipalId: string;
  occurredAt: string;
  sourceIdentity: string;
  idempotencyKeySha256: string;
}

export interface ArtifactToolEvidence {
  toolName: string;
  toolVersion: string;
  operation: "text_extraction" | "ocr" | "transcript" | "human_correction";
  inputSha256: string;
  outputSha256: string;
  routeId: string | null;
  agentRunId: string | null;
}

export interface ArtifactDerivation {
  parentArtifactId: string;
  childArtifactId: string;
  kind: "text_extraction" | "ocr" | "transcript" | "human_correction";
  toolEvidence: ArtifactToolEvidence;
  createdAt: string;
}

export interface ProposedArtifactLink {
  linkId: string;
  targetType: ProposedArtifactLinkCommand["targetType"];
  targetId: string;
  rationale: string;
  proposedByPrincipalId: string;
  proposedAt: string;
  reviewState:
    "proposed" | "accepted" | "changes_requested" | "rejected" | "superseded";
  reviewedByPrincipalId: string | null;
  reviewedAt: string | null;
}

export interface ArtifactCorrection {
  correctionId: string;
  targetArtifactId: string;
  correctedArtifactId: string;
  reason: string;
  correctedByPrincipalId: string;
  correctedAt: string;
}

export interface ArtifactSupersession {
  supersessionId: string;
  supersededArtifactId: string;
  successorArtifactId: string;
  reason: string;
  recordedByPrincipalId: string;
  recordedAt: string;
}

export interface ArtifactGovernance {
  classification: ArtifactClassification;
  privilegeState: "not_privileged" | "privilege_claimed" | "privilege_reviewed";
  retentionPolicyId: string;
  legalHoldIds: readonly string[];
  ethicalWallIds: readonly string[];
  policyDecisionId: string;
  policyRevision: string;
}

export interface ArtifactRead {
  artifactId: string;
  tenantId: string;
  matterId: string;
  artifactKind: "original" | "derived";
  filename: string;
  mediaType: string;
  byteCount: number;
  contentSha256: string;
  originalSha256: string;
  source: ArtifactSource;
  acquisitionMethod: string;
  storageLocator: string;
  quarantineState: "quarantined" | "released" | "rejected";
  scan: ArtifactScan;
  extractionState: "not_requested" | "pending" | "complete" | "failed";
  parentArtifactId: string | null;
  duplicateOfArtifactId: string | null;
  derivation: ArtifactDerivation | null;
  childArtifactIds: readonly string[];
  custody: readonly ArtifactCustodyEvent[];
  governance: ArtifactGovernance;
  proposedLinks: readonly ProposedArtifactLink[];
  reviewState:
    "proposed" | "accepted" | "changes_requested" | "rejected" | "superseded";
  reviewedByPrincipalId: string | null;
  reviewedAt: string | null;
  corrections: readonly ArtifactCorrection[];
  supersession: ArtifactSupersession | null;
  projectionRevision: number;
  lineageRevision: string;
  custodyRevision: string;
  projectionSha256: string;
  createdAt: string;
}

export interface ArtifactLineageRead {
  originalArtifactId: string;
  artifacts: readonly ArtifactRead[];
  edges: readonly ArtifactDerivation[];
  lineageRevision: string;
}

export interface ArtifactIntakeReceipt {
  artifact: ArtifactRead;
  derivedArtifacts: readonly ArtifactRead[];
  lineage: ArtifactLineageRead;
  idempotencyKey: string;
  requestSha256: string;
  replayed: boolean;
  duplicate: boolean;
  auditEventId: string;
  outboxId: string;
}
