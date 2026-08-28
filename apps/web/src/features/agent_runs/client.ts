import type {
  AgentRunRecord,
  AnalysisRequestCommand,
  BlindChallengeCommand,
  HumanDispositionCommand,
} from "./contracts";

export class AgentRunsApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

export interface AgentRunsClientOptions {
  baseUrl: string;
  tenantId: string;
  csrfToken: string;
  fetchImpl?: typeof fetch;
}

export class AgentRunsClient {
  readonly #baseUrl: string;
  readonly #tenantId: string;
  readonly #csrfToken: string;
  readonly #fetch: typeof fetch;

  constructor(options: AgentRunsClientOptions) {
    this.#baseUrl = options.baseUrl.replace(/\/$/, "");
    this.#tenantId = options.tenantId;
    this.#csrfToken = options.csrfToken;
    this.#fetch = options.fetchImpl ?? fetch;
  }

  list(matterId: string): Promise<AgentRunRecord[]> {
    return this.#request(
      `/v1/matters/${encodeURIComponent(matterId)}/agent-runs`,
      {},
    );
  }

  get(matterId: string, runId: string): Promise<AgentRunRecord> {
    return this.#request(
      `/v1/matters/${encodeURIComponent(matterId)}/agent-runs/${encodeURIComponent(runId)}`,
      {},
    );
  }

  start(
    matterId: string,
    idempotencyKey: string,
    command: AnalysisRequestCommand,
  ): Promise<AgentRunRecord> {
    return this.#request(
      `/v1/matters/${encodeURIComponent(matterId)}/agent-runs`,
      {
        method: "POST",
        body: JSON.stringify(command),
        headers: { "Idempotency-Key": idempotencyKey },
      },
    );
  }

  challenge(
    matterId: string,
    runId: string,
    idempotencyKey: string,
    command: BlindChallengeCommand,
  ): Promise<AgentRunRecord> {
    return this.#request(
      `/v1/matters/${encodeURIComponent(matterId)}/agent-runs/${encodeURIComponent(runId)}/challenges`,
      {
        method: "POST",
        body: JSON.stringify(command),
        headers: { "Idempotency-Key": idempotencyKey },
      },
    );
  }

  dispose(
    matterId: string,
    runId: string,
    idempotencyKey: string,
    command: HumanDispositionCommand,
  ): Promise<AgentRunRecord> {
    return this.#request(
      `/v1/matters/${encodeURIComponent(matterId)}/agent-runs/${encodeURIComponent(runId)}/dispositions`,
      {
        method: "POST",
        body: JSON.stringify(command),
        headers: { "Idempotency-Key": idempotencyKey },
      },
    );
  }

  async #request<T>(path: string, init: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await this.#fetch(`${this.#baseUrl}${path}`, {
        ...init,
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": this.#csrfToken,
          "X-SKLegal-Tenant": this.#tenantId,
          ...init.headers,
        },
      });
    } catch {
      throw new AgentRunsApiError(0, "agent_runs_unavailable");
    }
    if (!response.ok) {
      let code = "agent_runs_request_failed";
      try {
        const payload = (await response.json()) as {
          detail?: { code?: unknown };
        };
        if (typeof payload.detail?.code === "string") {
          code = payload.detail.code;
        }
      } catch {
        // Preserve the sanitized fallback. Response bytes never reach the UI.
      }
      throw new AgentRunsApiError(response.status, code);
    }
    return (await response.json()) as T;
  }
}
