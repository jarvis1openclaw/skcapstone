/**
 * SKLegal API client for the application shell.
 *
 * Every request is tenant-scoped, carries the session credential, and is
 * stamped with a fresh X-Correlation-ID for end-to-end traceability.
 * Responses are mapped fail closed: unexpected statuses become a generic
 * ApiError and no response body detail is surfaced to the user.
 */

import type { SessionCredentialStore } from "./credentials";
import { newCorrelationId } from "./correlation";
import { ApiError, apiErrorFromStatus, apiErrorFromCause } from "./errors";
import type {
  ClientDetail,
  ClientSummary,
  CorpusSearchResponse,
  CorpusSpan,
  MatterDetail,
  MatterSummary,
  MatterWorkspace,
} from "./types";

export interface ApiClientOptions {
  baseUrl: string;
  credentials: SessionCredentialStore;
  tenantId: () => string;
  fetchImpl?: typeof fetch;
}

export interface ApiResult<T> {
  data: T;
  correlationId: string;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly credentials: SessionCredentialStore;
  private readonly tenantId: () => string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.credentials = options.credentials;
    this.tenantId = options.tenantId;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async request<T>(path: string): Promise<ApiResult<T>> {
    const correlationId = newCorrelationId();
    const credential = this.credentials.loadActive();
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Correlation-ID": correlationId,
      "X-SKLegal-Tenant": this.tenantId(),
    };
    if (credential !== null) {
      headers.Authorization = `Bearer ${credential.token}`;
    }
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, { headers });
    } catch (cause) {
      throw apiErrorFromCause(cause, correlationId);
    }
    if (!response.ok) {
      throw apiErrorFromStatus(response.status, correlationId);
    }
    try {
      const data = (await response.json()) as T;
      return { data, correlationId };
    } catch (cause) {
      throw apiErrorFromCause(cause, correlationId);
    }
  }

  private async postJson<T>(
    path: string,
    body: unknown,
  ): Promise<ApiResult<T>> {
    const correlationId = newCorrelationId();
    const credential = this.credentials.loadActive();
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": correlationId,
      "X-SKLegal-Tenant": this.tenantId(),
    };
    if (credential !== null) {
      headers.Authorization = `Bearer ${credential.token}`;
    }
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
    } catch (cause) {
      throw apiErrorFromCause(cause, correlationId);
    }
    if (!response.ok) {
      throw apiErrorFromStatus(response.status, correlationId);
    }
    try {
      const data = (await response.json()) as T;
      return { data, correlationId };
    } catch (cause) {
      throw apiErrorFromCause(cause, correlationId);
    }
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
}

export type { ApiError };
