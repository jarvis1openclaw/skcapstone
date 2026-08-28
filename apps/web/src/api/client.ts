/** Same-origin SKLegal API client backed by a server-verifiable browser session. */

import { newCorrelationId } from "./correlation";
import { ApiError, apiErrorFromStatus, apiErrorFromCause } from "./errors";
import type {
  ClientDetail,
  ClientSummary,
  ClaimLedger,
  CorpusSearchResponse,
  CorpusSpan,
  MatterDetail,
  MatterSummary,
  MatterWorkspace,
} from "./types";
import type {
  CorpusSearchRead,
  CorpusSpanRead,
  GovernedCorpusResult,
} from "../features/governed_corpus/types";

export interface CorpusProjectionPins {
  releaseId: string;
  projectionGeneration: number;
  coreWatermark: number;
}

export interface ApiClientOptions {
  baseUrl: string;
  tenantId: () => string;
  csrfToken?: () => string | null;
  onAuthenticationFailure?: () => void;
  fetchImpl?: typeof fetch;
  corpusProjectionPins?: CorpusProjectionPins;
}

export interface ApiResult<T> {
  data: T;
  correlationId: string;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly tenantId: () => string;
  private readonly csrfToken: () => string | null;
  private readonly onAuthenticationFailure: () => void;
  private readonly fetchImpl: typeof fetch;
  private readonly corpusProjectionPins: CorpusProjectionPins | null;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.tenantId = options.tenantId;
    this.csrfToken = options.csrfToken ?? (() => null);
    this.onAuthenticationFailure =
      options.onAuthenticationFailure ?? (() => {});
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.corpusProjectionPins = options.corpusProjectionPins ?? null;
  }

  private async execute<T>(
    path: string,
    method: "GET" | "POST",
    body?: unknown,
  ): Promise<ApiResult<T>> {
    const correlationId = newCorrelationId();
    const tenantId = this.tenantId();
    if (!tenantId) {
      throw new ApiError({
        kind: "unauthenticated",
        status: 401,
        correlationId,
      });
    }
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Correlation-ID": correlationId,
      "X-SKLegal-Tenant": tenantId,
    };
    if (method === "POST") {
      const csrfToken = this.csrfToken();
      if (!csrfToken) {
        throw new ApiError({
          kind: "unauthenticated",
          status: 401,
          correlationId,
        });
      }
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrfToken;
    }
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin",
      });
    } catch (cause) {
      throw apiErrorFromCause(cause, correlationId);
    }
    const responseCorrelation =
      response.headers.get("X-Correlation-ID") ?? correlationId;
    if (!response.ok) {
      if (response.status === 401) {
        this.onAuthenticationFailure();
      }
      throw apiErrorFromStatus(response.status, responseCorrelation);
    }
    try {
      return {
        data: (await response.json()) as T,
        correlationId: responseCorrelation,
      };
    } catch (cause) {
      throw apiErrorFromCause(cause, responseCorrelation);
    }
  }

  private request<T>(path: string): Promise<ApiResult<T>> {
    return this.execute(path, "GET");
  }

  private postJson<T>(path: string, body: unknown): Promise<ApiResult<T>> {
    return this.execute(path, "POST", body);
  }

  listClients(): Promise<ApiResult<readonly ClientSummary[]>> {
    return this.request("/v1/clients");
  }

  getClient(clientId: string): Promise<ApiResult<ClientDetail>> {
    return this.request(`/v1/clients/${encodeURIComponent(clientId)}`);
  }

  listMatters(): Promise<ApiResult<readonly MatterSummary[]>> {
    return this.request("/v1/matters");
  }

  getMatter(matterId: string): Promise<ApiResult<MatterDetail>> {
    return this.request(`/v1/matters/${encodeURIComponent(matterId)}`);
  }

  getMatterWorkspace(matterId: string): Promise<ApiResult<MatterWorkspace>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/workspace`,
    );
  }

  async searchCorpus(
    matterId: string,
    query: string,
  ): Promise<ApiResult<CorpusSearchResponse>> {
    if (this.corpusProjectionPins !== null) {
      const response = await this.postJson<CorpusSearchRead>(
        `/v1/matters/${encodeURIComponent(matterId)}/corpus/search`,
        {
          query,
          expectedReleaseId: this.corpusProjectionPins.releaseId,
          expectedProjectionGeneration:
            this.corpusProjectionPins.projectionGeneration,
          requiredCoreWatermark: this.corpusProjectionPins.coreWatermark,
        },
      );
      return {
        correlationId: response.correlationId,
        data: legacyCorpusSearch(query, response.data.result),
      };
    }
    return this.postJson(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/search`,
      { query },
    );
  }

  async getCorpusSpan(
    matterId: string,
    sourceId: string,
  ): Promise<ApiResult<CorpusSpan>> {
    if (this.corpusProjectionPins !== null) {
      const query = new URLSearchParams({
        expectedReleaseId: this.corpusProjectionPins.releaseId,
        expectedProjectionGeneration: String(
          this.corpusProjectionPins.projectionGeneration,
        ),
        requiredCoreWatermark: String(this.corpusProjectionPins.coreWatermark),
      });
      const response = await this.request<CorpusSpanRead>(
        `/v1/matters/${encodeURIComponent(matterId)}/corpus/sources/${encodeURIComponent(sourceId)}/span?${query}`,
      );
      return {
        correlationId: response.correlationId,
        data: legacyCorpusSpan(response.data),
      };
    }
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/sources/${encodeURIComponent(sourceId)}/span`,
    );
  }

  getClaimLedger(matterId: string): Promise<ApiResult<ClaimLedger>> {
    return this.request(`/v1/matters/${encodeURIComponent(matterId)}/claims`);
  }
}

function legacyCorpusSearch(
  query: string,
  result: GovernedCorpusResult,
): CorpusSearchResponse {
  return {
    matterId: result.matterId,
    query,
    scopeOptions: [
      { scope: "this_matter", state: "active", reason: null },
      {
        scope: "tenant_corpus",
        state: "unavailable",
        reason: "Public synthetic composition is Matter scoped.",
      },
      {
        scope: "official_sources",
        state: "unavailable",
        reason: "No external source retrieval is enabled.",
      },
    ],
    results: result.hits.map(({ source, rank, supersessionStatus }) => ({
      rank: rank.rank,
      score: rank.score,
      snippet: source.exactSpan,
      sourceId: source.sourceId,
      title: source.title,
      citation: source.citation,
      classification: String(source.classification),
      origin: source.sourceRole,
      verificationState: source.verificationState,
      sourceVersion: source.sourceVersion,
      sourceSha256: source.sourceSha256,
      documentId: source.documentId,
      chunkId: source.chunkId,
      chunkSha256: source.chunkSha256,
      sourceLocator: `${source.documentId}:${source.chunkId}`,
      spanKind: source.locator.kind,
      spanStart: source.locator.start,
      spanEnd: source.locator.end,
      spanPage: source.locator.page,
      supersessionStatus,
    })),
    trace: {
      scopeKind: "this_matter",
      tenantId: result.tenantId,
      matterId: result.matterId,
      releaseId: result.projection.releaseId,
      projectionGeneration: result.projection.projectionGeneration,
      currentProjectionGeneration: result.projection.projectionGeneration,
      projectionStale: false,
      backendWatermark: result.projection.backendWatermark,
      lagEvents: result.projection.lagEvents,
      lagSeconds: result.projection.lagSeconds,
      queryTemplateId: "governed-corpus-full-text",
      queryTemplateVersion: "v1",
      queryTemplateSha256: result.querySha256,
      rankPath: result.hits[0]?.rank.rankPath ?? ["full_text"],
      retrievalAdapterVersion: "durable-postgres-v2",
      sourceIds: result.hits.map(({ source }) => source.sourceId),
      sourceHashes: result.hits.map(({ source }) => source.sourceSha256),
    },
  };
}

function legacyCorpusSpan(value: CorpusSpanRead): CorpusSpan {
  const { source } = value;
  return {
    state: "available",
    sourceId: source.sourceId,
    sourceVersion: source.sourceVersion,
    sourceSha256: source.sourceSha256,
    documentId: source.documentId,
    citation: source.citation,
    title: source.title,
    classification: String(source.classification),
    sourceLocator: `${source.documentId}:${source.chunkId}`,
    spanKind: source.locator.kind,
    spanStart: source.locator.start,
    spanEnd: source.locator.end,
    spanPage: source.locator.page,
    spanText: source.exactSpan,
    supersessionStatus: "current",
    jurisdiction: source.jurisdiction,
  };
}

export type { ApiError };
