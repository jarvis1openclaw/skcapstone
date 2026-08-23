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

export interface ApiClientOptions {
  baseUrl: string;
  tenantId: () => string;
  csrfToken?: () => string | null;
  onAuthenticationFailure?: () => void;
  fetchImpl?: typeof fetch;
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

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.tenantId = options.tenantId;
    this.csrfToken = options.csrfToken ?? (() => null);
    this.onAuthenticationFailure = options.onAuthenticationFailure ?? (() => {});
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async execute<T>(
    path: string,
    method: "GET" | "POST",
    body?: unknown,
  ): Promise<ApiResult<T>> {
    const correlationId = newCorrelationId();
    const tenantId = this.tenantId();
    if (!tenantId) {
      throw new ApiError({ kind: "unauthenticated", status: 401, correlationId });
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
      return { data: (await response.json()) as T, correlationId: responseCorrelation };
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
    return this.request(`/v1/matters/${encodeURIComponent(matterId)}/workspace`);
  }

  searchCorpus(
    matterId: string,
    query: string,
  ): Promise<ApiResult<CorpusSearchResponse>> {
    return this.postJson(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/search`,
      { query },
    );
  }

  getCorpusSpan(
    matterId: string,
    sourceId: string,
  ): Promise<ApiResult<CorpusSpan>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/sources/${encodeURIComponent(sourceId)}/span`,
    );
  }

  getClaimLedger(matterId: string): Promise<ApiResult<ClaimLedger>> {
    return this.request(`/v1/matters/${encodeURIComponent(matterId)}/claims`);
  }
}

export type { ApiError };
