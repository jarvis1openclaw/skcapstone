import type {
  CorpusSearchCommand,
  CorpusSearchRead,
  CorpusSpanRead,
  GovernedCorpusErrorCode,
} from "./types";

const knownCodes = new Set<GovernedCorpusErrorCode>([
  "authentication_required",
  "access_denied",
  "not_found",
  "stale_projection",
  "validation_failed",
  "idempotency_conflict",
  "version_conflict",
  "policy_unavailable",
  "resource_unavailable",
  "internal_error",
]);

export class GovernedCorpusFeatureError extends Error {
  constructor(
    readonly code: GovernedCorpusErrorCode,
    readonly status: number,
  ) {
    super(code);
    this.name = "GovernedCorpusFeatureError";
  }
}

export interface GovernedCorpusClientOptions {
  baseUrl: string;
  tenantId: () => string;
  csrfToken: () => string | null;
  fetchImpl?: typeof fetch;
}

function fallback(status: number): GovernedCorpusErrorCode {
  if (status === 401) return "authentication_required";
  if (status === 403) return "access_denied";
  if (status === 404) return "not_found";
  if (status === 409) return "stale_projection";
  if (status === 422) return "validation_failed";
  if (status === 503) return "resource_unavailable";
  return "internal_error";
}

async function closedError(
  response: Response,
): Promise<GovernedCorpusFeatureError> {
  let code = fallback(response.status);
  try {
    const body = (await response.json()) as { detail?: { code?: unknown } };
    const candidate = body.detail?.code;
    if (
      typeof candidate === "string" &&
      knownCodes.has(candidate as GovernedCorpusErrorCode)
    ) {
      code = candidate as GovernedCorpusErrorCode;
    }
  } catch {
    // Only a bounded error code may cross an untrusted response boundary.
  }
  return new GovernedCorpusFeatureError(code, response.status);
}

export class GovernedCorpusFeatureClient {
  private readonly baseUrl: string;
  private readonly tenantId: () => string;
  private readonly csrfToken: () => string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: GovernedCorpusClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.tenantId = options.tenantId;
    this.csrfToken = options.csrfToken;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async request<T>(
    path: string,
    method: "GET" | "POST",
    body?: unknown,
    idempotencyKey?: string,
  ): Promise<T> {
    const tenantId = this.tenantId();
    if (!tenantId)
      throw new GovernedCorpusFeatureError("authentication_required", 401);
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-SKLegal-Tenant": tenantId,
      "X-Correlation-ID": crypto.randomUUID(),
    };
    if (method === "POST") {
      const csrf = this.csrfToken();
      if (!csrf)
        throw new GovernedCorpusFeatureError("authentication_required", 401);
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrf;
    }
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        credentials: "same-origin",
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new GovernedCorpusFeatureError("resource_unavailable", 503);
    }
    if (!response.ok) throw await closedError(response);
    return (await response.json()) as T;
  }

  search(
    matterId: string,
    command: CorpusSearchCommand,
  ): Promise<CorpusSearchRead> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/search`,
      "POST",
      command,
    );
  }

  span(
    matterId: string,
    sourceId: string,
    pins: {
      expectedReleaseId: string;
      expectedProjectionGeneration: number;
      requiredCoreWatermark: number;
    },
  ): Promise<CorpusSpanRead> {
    const query = new URLSearchParams({
      expectedReleaseId: pins.expectedReleaseId,
      expectedProjectionGeneration: String(pins.expectedProjectionGeneration),
      requiredCoreWatermark: String(pins.requiredCoreWatermark),
    });
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/sources/${encodeURIComponent(sourceId)}/span?${query}`,
      "GET",
    );
  }

  recordSource(
    matterId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<unknown> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/corpus/sources`,
      "POST",
      command,
      idempotencyKey,
    );
  }
}
