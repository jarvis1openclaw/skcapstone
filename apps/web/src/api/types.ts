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

/**
 * Corpus research shapes (SKL-S4-03A). Search is matter-scoped; every
 * response carries the full retrieval trace so a result can always be
 * traced to its release, projection generation, and rank path. A corpus
 * row is an unverified research proposal, never controlling Authority.
 */

/** One selectable scope chip in the governed search bar. */
export interface CorpusScopeOption {
  scope: "this_matter" | "tenant_corpus" | "official_sources";
  state: "active" | "unavailable";
  reason: string | null;
}

/** Full S2-10 retrieval trace behind one search response. */
export interface CorpusTrace {
  scopeKind: string;
  tenantId: string;
  matterId: string;
  releaseId: string;
  projectionGeneration: number;
  currentProjectionGeneration: number;
  projectionStale: boolean;
  backendWatermark: number;
  lagEvents: number;
  lagSeconds: number;
  queryTemplateId: string;
  queryTemplateVersion: string;
  queryTemplateSha256: string;
  rankPath: readonly string[];
  retrievalAdapterVersion: string;
  sourceIds: readonly string[];
  sourceHashes: readonly string[];
}

/** One ranked corpus row with its exact source locator. */
export interface CorpusResult {
  rank: number;
  score: number;
  snippet: string;
  sourceId: string;
  title: string;
  citation: string;
  classification: string;
  origin: string;
  verificationState: string;
  sourceVersion: string;
  sourceSha256: string;
  documentId: string;
  chunkId: string;
  chunkSha256: string;
  sourceLocator: string;
  spanKind: string;
  spanStart: number;
  spanEnd: number;
  spanPage: number | null;
  supersessionStatus: string;
}

export interface CorpusSearchResponse {
  matterId: string;
  query: string;
  scopeOptions: readonly CorpusScopeOption[];
  results: readonly CorpusResult[];
  trace: CorpusTrace;
}

/** Accessible exact source span with full provenance. */
export interface CorpusSpanAvailable {
  state: "available";
  sourceId: string;
  sourceVersion: string;
  sourceSha256: string;
  documentId: string;
  citation: string;
  title: string;
  classification: string;
  sourceLocator: string;
  spanKind: string;
  spanStart: number;
  spanEnd: number;
  spanPage: number | null;
  spanText: string;
  supersessionStatus: string;
  jurisdiction: string | null;
}

/**
 * Denied source span: the shape structurally excludes span text, the
 * locator, and every hash, so a denial can never leak protected content.
 */
export interface CorpusSpanDenied {
  state: "denied";
  sourceId: string;
  denialReason: string;
  denialMessage: string;
}

export type CorpusSpan = CorpusSpanAvailable | CorpusSpanDenied;

/**
 * Claim ledger shapes (SKL-S4-03B). Every material claim exposes its
 * support and counter-support spans, the deterministic applicability
 * factors behind its authority support verification, the typed blind
 * challenges with preserved defects, the claim-state transition history,
 * and the current claim gate evaluation. Failed gates carry their failed
 * checks and closed reason vocabulary so nothing is silently reduced.
 */

/** One append-only support or counter-support link to an exact source span. */
export interface ClaimSupportRecord {
  supportId: string;
  kind: "support" | "counter_support";
  recordedAt: string;
  sourceSystem: string;
  sourceVersion: string;
  sourceLocator: string;
  contentSha256: string;
  spanStart: number;
  spanEnd: number;
  excerptSha256: string;
  note: string | null;
  recordedByPrincipalId: string;
  policyRevision: string;
}

/** One deterministic applicability, status, or quotation factor. */
export interface ApplicabilityCheck {
  checkId: string;
  subjectId: string | null;
  outcome: "passed" | "failed";
  reasons: readonly string[];
}

/** One challenge finding preserved verbatim for human review. */
export interface ChallengeDefect {
  defectKind: string;
  description: string;
}

/** One typed blind-challenge record with its independence label. */
export interface Challenge {
  challengeId: string;
  issuedAt: string;
  independence: "independent" | "same_model" | "not_blind";
  outcome: "no_defect" | "defect_found";
  sawChallengedConclusion: boolean;
  challengerProvider: string;
  challengerModelName: string;
  challengerModelRevision: string;
  defects: readonly ChallengeDefect[];
}

/** One failed gate check with the subject it names and its reasons. */
export interface GateCheck {
  checkId: string;
  subjectId: string | null;
  reasons: readonly string[];
}

/** The recorded claim gate evaluation for one ledger claim. */
export interface GateEvaluation {
  gate: string;
  evaluatedAt: string;
  outcome: "passed" | "failed";
  failedChecks: readonly GateCheck[];
}

/** One recorded claim-state transition; fromStatus is null initially. */
export interface ClaimStateTransition {
  fromStatus: string | null;
  toStatus: string;
  at: string;
  version: number;
}

/** One append-only human review decision for an exact claim version. */
export interface ClaimReviewRecord {
  reviewId: string;
  reviewedAt: string;
  reviewerPrincipalId: string;
  claimVersion: number;
  decision:
    | "accepted"
    | "changes_requested"
    | "challenge_recorded"
    | "withdrawal_confirmed";
  note: string;
  policyRevision: string;
}

/** One material claim with support, qualification, challenges, and history. */
export interface ClaimLedgerEntry {
  claimId: string;
  statement: string;
  status: string;
  version: number;
  policyRevision: string;
  updatedAt: string;
  support: readonly ClaimSupportRecord[];
  counterSupport: readonly ClaimSupportRecord[];
  supportVerificationState: "passed" | "failed" | "missing";
  applicability: readonly ApplicabilityCheck[];
  challenges: readonly Challenge[];
  reviewHistory: readonly ClaimReviewRecord[];
  gate: GateEvaluation | null;
  stateTransitions: readonly ClaimStateTransition[];
}

/** The claim ledger for one matter; zero claims is the no-answer state. */
export interface ClaimLedger {
  matterId: string;
  claims: readonly ClaimLedgerEntry[];
}
