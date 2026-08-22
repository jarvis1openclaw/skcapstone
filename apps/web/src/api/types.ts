/**
 * Typed shapes returned by the SKLegal API for the application shell and
 * the client and matter workspace. All records are tenant-scoped; matter
 * records are matter-scoped. Status strings use the legal-domain
 * vocabularies (matter lifecycle, review, tension, evidence,
 * communication) and never legacy storage labels.
 */

/** Domain matter lifecycle values. */
export type MatterLifecycleStatus =
  "proposed" | "open" | "on_hold" | "closed" | "archived";

export interface ClientSummary {
  id: string;
  tenantId: string;
  displayName: string;
  matterCount: number;
}

export interface MatterSummary {
  id: string;
  tenantId: string;
  clientId: string;
  clientDisplayName: string;
  title: string;
  status: MatterLifecycleStatus;
}

export interface ClientDetail extends ClientSummary {
  matters: readonly MatterSummary[];
}

export interface MatterDetail extends MatterSummary {
  engagementId: string | null;
  summary: string;
  openedOn: string | null;
  legacyAliases: readonly string[];
}

export interface WorkspaceMatter {
  matterId: string;
  clientId: string;
  clientDisplayName: string;
  engagementId: string | null;
  title: string;
  summary: string;
  status: MatterLifecycleStatus;
  openedAt: string | null;
  legacyAliases: readonly string[];
}

export interface WorkspaceParty {
  partyId: string;
  displayName: string;
  partyKind: string;
  roles: readonly string[];
  status: string;
}

export interface WorkspaceTimelineEvent {
  eventId: string;
  eventType: string;
  description: string;
  occurredAt: string | null;
  observedAt: string;
  status: string;
  sourcePath: string | null;
  legacyAliases: readonly string[];
}

export interface WorkspaceFactAssertion {
  factAssertionId: string;
  predicate: string;
  assertedValue: unknown;
  valueType: string;
  reviewStatus: string;
  sourcePath: string | null;
  sourceLocator: string;
  sourceMissing: boolean;
  tensionGroupKey: string | null;
}

export interface WorkspaceTensionGroup {
  tensionKey: string;
  status: string;
  assertionIds: readonly string[];
  reviewRequired: boolean;
}

export interface WorkspaceEvidenceItem {
  evidenceItemId: string;
  title: string;
  mediaType: string;
  contentSha256: string;
  status: string;
  sourcePath: string | null;
  sourceMissing: boolean;
}

export interface WorkspaceCommunication {
  communicationId: string;
  channel: string;
  summary: string;
  occurredAt: string | null;
  status: string;
  sourcePath: string | null;
  sourceMissing: boolean;
}

export interface WorkspaceVersionLineage {
  packetVersion: number;
  sourcePath: string;
  sourceSha256: string;
  historical: boolean;
  currentReviewBaseline: boolean;
}

/**
 * Approval or execution state for one workspace target. Negative states
 * such as pending_review and not_started are first-class data and are
 * always rendered; the workspace never hides them.
 */
export interface WorkspaceExecutionState {
  targetType: string;
  targetId: string;
  stateKind: string;
  stateValue: string;
}

/** An explicit incomplete state; gaps are rendered, never hidden. */
export interface WorkspaceGap {
  gapId: string;
  kind: string;
  description: string;
}

export interface WorkspaceAuditEntry {
  auditId: string;
  action: string;
  actor: string;
  occurredAt: string;
  outcome: string;
  detail: string | null;
}

export interface WorkspaceSourceFile {
  relativePath: string;
  contentSha256: string;
  observedAt: string;
  byteCount: number | null;
}

export interface WorkspaceProvenance {
  sourceSnapshot: string;
  currentSourceSnapshot: string | null;
  adapterVersion: string;
  observedAt: string;
  stale: boolean;
  sourceFiles: readonly WorkspaceSourceFile[];
}

/** Full matter workspace aggregate in legal-domain terminology. */
export interface MatterWorkspace {
  matter: WorkspaceMatter;
  engagementDisplayName: string | null;
  parties: readonly WorkspaceParty[];
  timeline: readonly WorkspaceTimelineEvent[];
  facts: readonly WorkspaceFactAssertion[];
  tensions: readonly WorkspaceTensionGroup[];
  evidence: readonly WorkspaceEvidenceItem[];
  communications: readonly WorkspaceCommunication[];
  versionLineage: readonly WorkspaceVersionLineage[];
  executionStates: readonly WorkspaceExecutionState[];
  gaps: readonly WorkspaceGap[];
  audit: readonly WorkspaceAuditEntry[];
  provenance: WorkspaceProvenance;
}
