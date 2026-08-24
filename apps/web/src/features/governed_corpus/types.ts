export type RetrievalMode = "full_text" | "vector_exact" | "hybrid_rrf";

export interface CorpusSearchCommand {
  schemaVersion?: "sklegal.governed-corpus-query/v1";
  query: string;
  queryEmbedding?: number[] | null;
  mode?: RetrievalMode;
  maxResults?: number;
  expectedReleaseId: string;
  expectedProjectionGeneration: number;
  requiredCoreWatermark: number;
  continuationCursor?: string | null;
}

export interface ProjectionState {
  releaseId: string;
  projectionGeneration: number;
  backendWatermark: number;
  coreWatermark: number;
  lagEvents: number;
  lagSeconds: number;
  maxLagEvents: number;
  maxLagSeconds: number;
  fullTextBackend: "postgresql_full_text";
  vectorBackend: "postgresql_pgvector_exact";
  qdrantCompatibility: "metadata_only";
  falkordbCompatibility: "metadata_only";
}

export interface CorpusSource {
  tenantId: string;
  matterId: string;
  sourceId: string;
  sourceVersionId: string;
  sourceVersion: string;
  releaseId: string;
  projectionGeneration: number;
  title: string;
  citation: string;
  sourceRole: "matter_evidence" | "course_instruction" | "official_authority";
  classification: number;
  rightsRevision: string;
  sourceSha256: string;
  documentId: string;
  chunkId: string;
  chunkSha256: string;
  locator: {
    kind: "character" | "page_character";
    start: number;
    end: number;
    page: number | null;
  };
  exactSpan: string;
  verificationState:
    "corpus_proposal" | "source_verified" | "official_authority_verified";
  jurisdiction: string | null;
}

export interface GovernedCorpusHit {
  source: CorpusSource;
  rank: {
    rank: number;
    score: number;
    fullTextRank: number | null;
    vectorDistance: number | null;
    rankPath: ("full_text" | "pgvector_exact" | "hybrid_rrf")[];
  };
  supersessionStatus: "current";
}

export interface GovernedCorpusResult {
  tenantId: string;
  matterId: string;
  querySha256: string;
  authorizationDecisionId: string;
  policyDecisionId: string;
  policyRevision: string;
  rightsRevision: string;
  classificationCeiling: number;
  projection: ProjectionState;
  mode: RetrievalMode;
  hits: GovernedCorpusHit[];
  noAnswer: boolean;
  continuationCursor: string | null;
}

export interface CorpusSearchRead {
  result: GovernedCorpusResult;
}

export interface CorpusSpanRead {
  source: CorpusSource;
  projection: ProjectionState;
}

export type GovernedCorpusErrorCode =
  | "authentication_required"
  | "access_denied"
  | "not_found"
  | "stale_projection"
  | "validation_failed"
  | "idempotency_conflict"
  | "version_conflict"
  | "policy_unavailable"
  | "resource_unavailable"
  | "internal_error";
