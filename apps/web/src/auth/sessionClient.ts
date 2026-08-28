import type { Session } from "./session";
import { newCorrelationId } from "../api/correlation";
import { ApiError, apiErrorFromCause, apiErrorFromStatus } from "../api/errors";

export const PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE =
  "development:public-synthetic:mvp";

export interface SessionEnvelope extends Session {
  expiresAt: string;
  csrfToken: string;
}

export class SessionClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private csrfToken: string | null = null;

  constructor(baseUrl: string, fetchImpl?: typeof fetch) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.fetchImpl = fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async request(
    path: string,
    init: RequestInit = {},
  ): Promise<SessionEnvelope | null> {
    const correlationId = newCorrelationId();
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Correlation-ID": correlationId,
      ...(init.headers as Record<string, string> | undefined),
    };
    if (this.csrfToken !== null && init.method !== undefined) {
      headers["X-CSRF-Token"] = this.csrfToken;
    }
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        credentials: "same-origin",
        headers,
      });
    } catch (cause) {
      throw apiErrorFromCause(cause, correlationId);
    }
    const responseCorrelation =
      response.headers.get("X-Correlation-ID") ?? correlationId;
    if (!response.ok) {
      this.csrfToken = null;
      throw apiErrorFromStatus(response.status, responseCorrelation);
    }
    if (response.status === 204) {
      this.csrfToken = null;
      return null;
    }
    try {
      const envelope = (await response.json()) as SessionEnvelope;
      if (Date.parse(envelope.expiresAt) <= Date.now()) {
        this.csrfToken = null;
        throw new ApiError({
          kind: "unauthenticated",
          status: 401,
          correlationId: responseCorrelation,
        });
      }
      this.csrfToken = envelope.csrfToken;
      return envelope;
    } catch (cause) {
      throw apiErrorFromCause(cause, responseCorrelation);
    }
  }

  bootstrap(credentialReference: string, tenantId: string) {
    return this.request("/v1/session/bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credentialReference, tenantId }),
    });
  }

  current() {
    return this.request("/v1/session");
  }

  refresh() {
    return this.request("/v1/session/refresh", { method: "POST" });
  }

  switchTenant(tenantId: string) {
    return this.request("/v1/session/tenant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenantId }),
    });
  }

  signOut() {
    return this.request("/v1/session", { method: "DELETE" });
  }
}
