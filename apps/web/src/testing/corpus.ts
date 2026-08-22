/**
 * Synthetic corpus research fixture for component tests (SKL-S4-03A).
 * Every value is synthetic: the citation, titles, hashes, identifiers,
 * and span text are placeholders, with no real matter content, no real
 * Authority, and no legacy identifiers.
 */

import type {
  CorpusResult,
  CorpusScopeOption,
  CorpusSearchResponse,
  CorpusSpan,
  CorpusSpanDenied,
  CorpusTrace,
} from "../api/types";

export const SYNTHETIC_CORPUS_MATTER_ID =
  "20000000-0000-4000-8000-0000000000a1";
export const SYNTHETIC_QUERY = "synthetic unclaimed property statute";
export const SYNTHETIC_SOURCE_ID = "synthetic-source-1";
export const SYNTHETIC_DENIED_SOURCE_ID = "synthetic-source-denied-1";
export const SYNTHETIC_SOURCE_SHA = "b".repeat(64);
export const SYNTHETIC_TRACE_SHA = "c".repeat(64);
export const SYNTHETIC_SPAN_TEXT =
  "Exact synthetic span text used for the source-span viewer.";

export const syntheticScopeOptions: readonly CorpusScopeOption[] = [
  { scope: "this_matter", state: "active", reason: null },
  {
    scope: "tenant_corpus",
    state: "unavailable",
    reason: "Tenant corpus scope is pending retrieval policy approval.",
  },
  {
    scope: "official_sources",
    state: "unavailable",
    reason: "Official-source verification is a separate approved lane.",
  },
];

export function syntheticTrace(overrides?: {
  projectionGeneration?: number;
  currentProjectionGeneration?: number;
}): CorpusTrace {
  const generation = overrides?.projectionGeneration ?? 4;
  const currentGeneration = overrides?.currentProjectionGeneration ?? 4;
  return {
    scopeKind: "matter",
    tenantId: "10000000-0000-4000-8000-000000000001",
    matterId: SYNTHETIC_CORPUS_MATTER_ID,
    releaseId: "synthetic-release-1",
    projectionGeneration: generation,
    currentProjectionGeneration: currentGeneration,
    projectionStale: generation !== currentGeneration,
    backendWatermark: 10,
    lagEvents: 0,
    lagSeconds: 0,
    queryTemplateId: "lexical.search.v1",
    queryTemplateVersion: "1.0.0",
    queryTemplateSha256: SYNTHETIC_TRACE_SHA,
    rankPath: ["lexical_rank", "scope_aggregate"],
    retrievalAdapterVersion: "1.0.0",
    sourceIds: [SYNTHETIC_SOURCE_ID],
    sourceHashes: [SYNTHETIC_SOURCE_SHA],
  };
}

export const syntheticCorpusResults: readonly CorpusResult[] = [
  {
    rank: 1,
    score: 1.5,
    snippet: "Synthetic corpus snippet about the queried statute.",
    sourceId: SYNTHETIC_SOURCE_ID,
    title: "Synthetic research note",
    citation: "SYN 100 ILCS 1/2",
    classification: "protected",
    origin: "matter_corpus",
    verificationState: "unverified_research_proposal",
    sourceVersion: "1",
    sourceSha256: SYNTHETIC_SOURCE_SHA,
    documentId: "synthetic-document-1",
    chunkId: "synthetic-chunk-1",
    chunkSha256: "d".repeat(64),
    sourceLocator: "fixture/synthetic-source-1",
    spanKind: "text",
    spanStart: 0,
    spanEnd: 10,
    spanPage: 1,
    supersessionStatus: "current",
  },
  {
    rank: 2,
    score: 1.25,
    snippet: "Second synthetic corpus snippet with a superseded source.",
    sourceId: SYNTHETIC_DENIED_SOURCE_ID,
    title: "Synthetic superseded note",
    citation: "SYN 100 ILCS 1/3",
    classification: "protected",
    origin: "matter_corpus",
    verificationState: "unverified_research_proposal",
    sourceVersion: "1",
    sourceSha256: "e".repeat(64),
    documentId: "synthetic-document-2",
    chunkId: "synthetic-chunk-2",
    chunkSha256: "f".repeat(64),
    sourceLocator: "fixture/synthetic-source-2",
    spanKind: "text",
    spanStart: 0,
    spanEnd: 12,
    spanPage: 2,
    supersessionStatus: "superseded",
  },
];

export function syntheticSearchResponse(overrides?: {
  trace?: CorpusTrace;
}): CorpusSearchResponse {
  return {
    matterId: SYNTHETIC_CORPUS_MATTER_ID,
    query: SYNTHETIC_QUERY,
    scopeOptions: syntheticScopeOptions,
    results: syntheticCorpusResults,
    trace: overrides?.trace ?? syntheticTrace(),
  };
}

export const syntheticAvailableSpan: CorpusSpan = {
  state: "available",
  sourceId: SYNTHETIC_SOURCE_ID,
  sourceVersion: "1",
  sourceSha256: SYNTHETIC_SOURCE_SHA,
  documentId: "synthetic-document-1",
  citation: "SYN 100 ILCS 1/2",
  title: "Synthetic research note",
  classification: "protected",
  sourceLocator: "fixture/synthetic-source-1",
  spanKind: "text",
  spanStart: 0,
  spanEnd: 10,
  spanPage: 1,
  spanText: SYNTHETIC_SPAN_TEXT,
  supersessionStatus: "current",
  jurisdiction: "Synthetic Jurisdiction",
};

export const syntheticDeniedSpan: CorpusSpanDenied = {
  state: "denied",
  sourceId: SYNTHETIC_DENIED_SOURCE_ID,
  denialReason: "source_not_accessible",
  denialMessage:
    "The source artifact is not accessible under the current matter policy. No span content is available.",
};
