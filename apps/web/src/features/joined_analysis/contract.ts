export type Sha256 = string;

export type ClaimStatus =
  | "proposed"
  | "under_review"
  | "accepted"
  | "challenged"
  | "rejected"
  | "withdrawn";

export type LedgerClaimStatus =
  "proposed" | "under_review" | "supported" | "challenged" | "withdrawn";

type MappedClaimStatus = Exclude<ClaimStatus, "rejected">;

export interface LegacyClaimMigrationRead {
  legacyLedgerClaimId: string;
  legacyStatus: LedgerClaimStatus;
  mappedClaimStatus: MappedClaimStatus;
  mappingRevision: "ledger-claim-to-claim-v1";
}

export interface SourceSpanRead {
  sourceReferenceId: string;
  sourceSystem: string;
  sourceVersion: string;
  sourceLocator: string;
  contentSha256: Sha256;
  spanStart: number;
  spanEnd: number;
  excerptSha256: Sha256;
  origin:
    | "matter_record"
    | "course_instruction"
    | "current_official_authority"
    | "model_inference";
}

export interface MatterAnalysisTheoryRead {
  theoryId: string;
  theoryKind: "claim" | "defense";
  issueId: string;
  label: string;
  statement: string;
  version: number;
  status: ClaimStatus;
  elementIds: string[];
  factAssertionIds: string[];
  evidence: Array<{
    evidenceItemId: string;
    role: "support" | "counter_support";
    source: SourceSpanRead;
  }>;
  authorities: Array<{
    authorityId: string;
    role: "support" | "contrary_authority";
    applicability: "applicable" | "disputed" | "not_applicable" | "unverified";
    reasons: string[];
  }>;
  gaps: Array<{
    gapId: string;
    kind:
      | "missing_fact"
      | "missing_evidence"
      | "missing_authority"
      | "unresolved_burden";
    description: string;
    blocking: boolean;
  }>;
  challenges: Array<{
    challengeId: string;
    status: "not_run" | "pending_human_review" | "defect_found" | "no_defect";
    description: string;
    source: "public_synthetic" | "recorded_agent_run";
  }>;
  ledgerProjection: {
    tenantId: string;
    matterId: string;
    claimId: string;
    projectedClaimVersion: number;
    projectedClaimStatus: ClaimStatus;
    legacyMigration: LegacyClaimMigrationRead | null;
    policyRevision: Sha256;
    projectionState: "current" | "stale" | "unavailable";
  } | null;
}

export interface MatterAnalysisRead {
  schemaRevision: "sklegal-matter-analysis/v1";
  tenantId: string;
  matterId: string;
  snapshot: {
    snapshotId: string;
    version: number;
    observedAt: string;
    matterSnapshotSha256: Sha256;
    claimProjectionRevision: Sha256;
    authoritySnapshot: Sha256;
    projectionRevision: Sha256;
    projectionSha256: Sha256;
  };
  classification: {
    value: "public" | "internal" | "confidential" | "privileged";
    purpose: "claim_review";
    egress: "not_evaluated" | "local_only" | "approved";
    sourceRights: "public_synthetic" | "recorded";
  };
  policyIdentity: {
    authorizationDecisionId: string;
    principalId: string;
    capability: "claim.review";
    purpose: "claim_review";
    verifierPolicyVersion: string;
    principalPolicyRevisions: Sha256[];
    trustedIssuerPolicyRevision: Sha256;
    revocationRevision: Sha256;
  };
  forum: {
    forumId: string;
    name: string;
    jurisdiction: string;
    forumKind: "court" | "agency" | "arbitration" | "mediation" | "other";
    sourceReferenceId: string | null;
  } | null;
  proceedings: Array<{
    proceedingId: string;
    title: string;
    forumId: string | null;
    docketNumber: string | null;
    status: "proposed" | "active" | "stayed" | "disposed" | "closed";
    version: number;
  }>;
  issues: Array<{
    issueId: string;
    question: string;
    status: "identified" | "under_review" | "resolved" | "deferred";
    version: number;
    theoryIds: string[];
  }>;
  theories: MatterAnalysisTheoryRead[];
  elements: Array<{
    elementId: string;
    theoryId: string;
    theoryKind: "claim" | "defense";
    description: string;
    status: "alleged" | "supported" | "disputed" | "not_established";
    version: number;
    burden: {
      allocation:
        | "claimant"
        | "respondent"
        | "moving_party"
        | "opposing_party"
        | "unknown";
      standard: string;
      authorityId: string | null;
      verificationState: "recorded" | "needs_authority" | "disputed";
    };
    evidenceItemIds: string[];
    factAssertionIds: string[];
  }>;
  factAssertions: Array<{
    factAssertionId: string;
    subjectRef: string;
    predicate: string;
    assertedValue: boolean | number | string | null;
    status: "source_asserted" | "ambiguous" | "verified" | "superseded";
    version: number;
    source: SourceSpanRead;
  }>;
  evidenceItems: Array<{
    evidenceItemId: string;
    title: string;
    mediaType: string;
    contentSha256: Sha256;
    status:
      | "proposed"
      | "collected"
      | "verified"
      | "challenged"
      | "excluded"
      | "superseded";
    version: number;
    source: SourceSpanRead;
  }>;
  authorities: Array<{
    authorityId: string;
    title: string;
    citation: string;
    jurisdiction: string;
    authorityKind:
      | "constitution"
      | "statute"
      | "regulation"
      | "case"
      | "rule"
      | "administrative_material"
      | "secondary_source"
      | "other";
    status:
      "proposed" | "verified" | "challenged" | "not_applicable" | "superseded";
    version: number;
    source: SourceSpanRead;
  }>;
  page: {
    limit: number;
    returned: number;
    hasMore: boolean;
    nextCursor: string | null;
  };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const CLAIM_STATUSES = [
  "proposed",
  "under_review",
  "accepted",
  "challenged",
  "rejected",
  "withdrawn",
] as const satisfies readonly ClaimStatus[];
const LEDGER_CLAIM_STATUSES = [
  "proposed",
  "under_review",
  "supported",
  "challenged",
  "withdrawn",
] as const satisfies readonly LedgerClaimStatus[];
const LEGACY_LEDGER_STATUS_MAP: Record<LedgerClaimStatus, MappedClaimStatus> = {
  proposed: "proposed",
  under_review: "under_review",
  supported: "accepted",
  challenged: "challenged",
  withdrawn: "withdrawn",
};

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("joined analysis response is invalid");
  }
  return value as Record<string, unknown>;
}

function array(value: unknown): unknown[] {
  if (!Array.isArray(value))
    throw new TypeError("joined analysis response is invalid");
  return value;
}

function identifier(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) {
    throw new TypeError("joined analysis response is invalid");
  }
  return value;
}

function digest(value: unknown): string {
  if (typeof value !== "string" || !SHA256.test(value)) {
    throw new TypeError("joined analysis response is invalid");
  }
  return value;
}

function integer(value: unknown, minimum = 0): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new TypeError("joined analysis response is invalid");
  }
  return value as number;
}

function text(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError("joined analysis response is invalid");
  }
  return value;
}

function oneOf<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    throw new TypeError("joined analysis response is invalid");
  }
  return value as T;
}

function sourceSpan(value: unknown): Record<string, unknown> {
  const source = record(value);
  identifier(source.sourceReferenceId);
  text(source.sourceSystem);
  text(source.sourceVersion);
  text(source.sourceLocator);
  digest(source.contentSha256);
  digest(source.excerptSha256);
  const start = integer(source.spanStart);
  const end = integer(source.spanEnd, 1);
  if (end <= start) throw new TypeError("joined analysis response is invalid");
  oneOf(source.origin, [
    "matter_record",
    "course_instruction",
    "current_official_authority",
    "model_inference",
  ] as const);
  return source;
}

export function parseMatterAnalysisRead(value: unknown): MatterAnalysisRead {
  const root = record(value);
  if (root.schemaRevision !== "sklegal-matter-analysis/v1") {
    throw new TypeError("joined analysis response is invalid");
  }
  const tenantId = identifier(root.tenantId);
  const matterId = identifier(root.matterId);
  const snapshot = record(root.snapshot);
  identifier(snapshot.snapshotId);
  integer(snapshot.version, 1);
  text(snapshot.observedAt);
  digest(snapshot.matterSnapshotSha256);
  digest(snapshot.claimProjectionRevision);
  digest(snapshot.authoritySnapshot);
  digest(snapshot.projectionRevision);
  digest(snapshot.projectionSha256);
  const classification = record(root.classification);
  oneOf(classification.value, [
    "public",
    "internal",
    "confidential",
    "privileged",
  ] as const);
  if (classification.purpose !== "claim_review") {
    throw new TypeError("joined analysis response is invalid");
  }
  oneOf(classification.egress, [
    "not_evaluated",
    "local_only",
    "approved",
  ] as const);
  oneOf(classification.sourceRights, ["public_synthetic", "recorded"] as const);
  const policy = record(root.policyIdentity);
  if (
    policy.capability !== "claim.review" ||
    policy.purpose !== "claim_review"
  ) {
    throw new TypeError("joined analysis response is invalid");
  }
  identifier(policy.authorizationDecisionId);
  identifier(policy.principalId);
  text(policy.verifierPolicyVersion);
  digest(policy.trustedIssuerPolicyRevision);
  digest(policy.revocationRevision);
  array(policy.principalPolicyRevisions).forEach(digest);

  const issues = array(root.issues).map(record);
  const issueIds = new Set(issues.map((issue) => identifier(issue.issueId)));
  if (issueIds.size !== issues.length)
    throw new TypeError("joined analysis response is invalid");
  for (const issue of issues) {
    text(issue.question);
    integer(issue.version, 1);
    const issueTheoryIds = array(issue.theoryIds).map(identifier);
    if (new Set(issueTheoryIds).size !== issueTheoryIds.length) {
      throw new TypeError("joined analysis response is invalid");
    }
  }
  const evidenceItems = array(root.evidenceItems).map(record);
  const evidenceIds = new Set(
    evidenceItems.map((item) => identifier(item.evidenceItemId)),
  );
  if (evidenceIds.size !== evidenceItems.length) {
    throw new TypeError("joined analysis response is invalid");
  }
  const evidenceSources = new Map<string, string>();
  for (const item of evidenceItems) {
    digest(item.contentSha256);
    const source = sourceSpan(item.source);
    if (item.contentSha256 !== source.contentSha256) {
      throw new TypeError("joined analysis response is invalid");
    }
    evidenceSources.set(item.evidenceItemId as string, JSON.stringify(source));
  }
  const authorities = array(root.authorities).map(record);
  const authorityIds = new Set(
    authorities.map((authority) => identifier(authority.authorityId)),
  );
  if (authorityIds.size !== authorities.length) {
    throw new TypeError("joined analysis response is invalid");
  }
  for (const authority of authorities) {
    const source = sourceSpan(authority.source);
    if (source.origin !== "current_official_authority") {
      throw new TypeError("joined analysis response is invalid");
    }
  }
  const theories = array(root.theories).map(record);
  const theoryIds = new Set(
    theories.map((theory) => identifier(theory.theoryId)),
  );
  if (theoryIds.size !== theories.length) {
    throw new TypeError("joined analysis response is invalid");
  }
  const theoryById = new Map(
    theories.map((theory) => [identifier(theory.theoryId), theory]),
  );
  for (const issue of issues) {
    for (const theoryId of array(issue.theoryIds).map(identifier)) {
      const theory = theoryById.get(theoryId);
      if (!theory || theory.issueId !== issue.issueId) {
        throw new TypeError("joined analysis response is invalid");
      }
    }
  }
  for (const theory of theories) {
    const kind = theory.theoryKind;
    const ledger = theory.ledgerProjection;
    const claimStatus = oneOf(theory.status, CLAIM_STATUSES);
    if (!issueIds.has(identifier(theory.issueId))) {
      throw new TypeError("joined analysis response is invalid");
    }
    const elementIds = array(theory.elementIds).map(identifier);
    const evidenceLinks = array(theory.evidence).map(record);
    if (
      new Set(evidenceLinks.map((item) => identifier(item.evidenceItemId)))
        .size !== evidenceLinks.length
    ) {
      throw new TypeError("joined analysis response is invalid");
    }
    for (const link of evidenceLinks) {
      oneOf(link.role, ["support", "counter_support"] as const);
      const evidenceId = link.evidenceItemId as string;
      if (
        !evidenceIds.has(evidenceId) ||
        evidenceSources.get(evidenceId) !==
          JSON.stringify(sourceSpan(link.source))
      ) {
        throw new TypeError("joined analysis response is invalid");
      }
    }
    const authorityLinks = array(theory.authorities).map(record);
    if (
      new Set(authorityLinks.map((item) => identifier(item.authorityId)))
        .size !== authorityLinks.length
    ) {
      throw new TypeError("joined analysis response is invalid");
    }
    for (const link of authorityLinks) {
      if (!authorityIds.has(link.authorityId as string)) {
        throw new TypeError("joined analysis response is invalid");
      }
      oneOf(link.role, ["support", "contrary_authority"] as const);
    }
    if (kind === "claim") {
      const projection = record(ledger);
      const projectedClaimStatus = oneOf(
        projection.projectedClaimStatus,
        CLAIM_STATUSES,
      );
      if (
        projection.tenantId !== tenantId ||
        projection.matterId !== matterId ||
        projection.claimId !== theory.theoryId ||
        projection.projectedClaimVersion !== theory.version ||
        projectedClaimStatus !== claimStatus
      ) {
        throw new TypeError("joined analysis response is invalid");
      }
      const migrationValue = projection.legacyMigration;
      if (
        migrationValue === null &&
        (claimStatus === "accepted" || claimStatus === "rejected")
      ) {
        throw new TypeError("joined analysis response is invalid");
      }
      if (migrationValue !== null) {
        const migration = record(migrationValue);
        identifier(migration.legacyLedgerClaimId);
        const legacyStatus = oneOf(
          migration.legacyStatus,
          LEDGER_CLAIM_STATUSES,
        );
        const mappedClaimStatus = oneOf(
          migration.mappedClaimStatus,
          CLAIM_STATUSES.filter((status) => status !== "rejected"),
        );
        if (
          migration.mappingRevision !== "ledger-claim-to-claim-v1" ||
          LEGACY_LEDGER_STATUS_MAP[legacyStatus] !== mappedClaimStatus ||
          mappedClaimStatus !== claimStatus
        ) {
          throw new TypeError("joined analysis response is invalid");
        }
      }
    } else if (kind !== "defense" || ledger !== null) {
      throw new TypeError("joined analysis response is invalid");
    }
    if (elementIds.length !== new Set(elementIds).size) {
      throw new TypeError("joined analysis response is invalid");
    }
  }
  const elements = array(root.elements).map(record);
  const elementIds = new Set(
    elements.map((element) => identifier(element.elementId)),
  );
  if (elementIds.size !== elements.length) {
    throw new TypeError("joined analysis response is invalid");
  }
  const elementById = new Map(
    elements.map((element) => [identifier(element.elementId), element]),
  );
  for (const theory of theories) {
    for (const elementId of array(theory.elementIds).map(identifier)) {
      const element = elementById.get(elementId);
      if (
        !element ||
        element.theoryId !== theory.theoryId ||
        element.theoryKind !== theory.theoryKind
      )
        throw new TypeError("joined analysis response is invalid");
    }
  }
  for (const element of elements) {
    const owningTheory = theoryById.get(identifier(element.theoryId));
    if (
      !owningTheory ||
      element.theoryKind !== owningTheory.theoryKind ||
      !array(owningTheory.elementIds)
        .map(identifier)
        .includes(identifier(element.elementId))
    ) {
      throw new TypeError("joined analysis response is invalid");
    }
    const burden = record(element.burden);
    text(burden.standard);
    if (
      burden.authorityId !== null &&
      !authorityIds.has(identifier(burden.authorityId))
    ) {
      throw new TypeError("joined analysis response is invalid");
    }
  }
  const forumId =
    root.forum === null ? null : identifier(record(root.forum).forumId);
  const proceedingIds = array(root.proceedings).map((proceeding) =>
    identifier(record(proceeding).proceedingId),
  );
  const factAssertions = array(root.factAssertions).map(record);
  const factIds = new Set(
    factAssertions.map((fact) => identifier(fact.factAssertionId)),
  );
  if (factIds.size !== factAssertions.length) {
    throw new TypeError("joined analysis response is invalid");
  }
  const allowedFactSubjects = new Set([
    matterId,
    ...(forumId === null ? [] : [forumId]),
    ...proceedingIds,
    ...issueIds,
    ...theoryIds,
    ...elementIds,
    ...factIds,
    ...evidenceIds,
    ...authorityIds,
  ]);
  for (const fact of factAssertions) {
    if (!allowedFactSubjects.has(identifier(fact.subjectRef))) {
      throw new TypeError("joined analysis response is invalid");
    }
    sourceSpan(fact.source);
  }
  const page = record(root.page);
  const limit = integer(page.limit, 1);
  if (limit > 100 || integer(page.returned) !== theories.length) {
    throw new TypeError("joined analysis response is invalid");
  }
  if (typeof page.hasMore !== "boolean") {
    throw new TypeError("joined analysis response is invalid");
  }
  if (page.nextCursor !== null) text(page.nextCursor);
  return value as MatterAnalysisRead;
}
