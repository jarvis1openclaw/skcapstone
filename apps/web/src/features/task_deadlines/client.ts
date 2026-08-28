import type {
  DeadlineRead,
  MutationReceipt,
  SimulationReceipt,
  TaskDeadlineErrorCode,
  TaskRead,
} from "./types";

const knownCodes = new Set<TaskDeadlineErrorCode>([
  "authentication_required",
  "access_denied",
  "validation_failed",
  "precondition_failed",
  "idempotency_conflict",
  "policy_unavailable",
  "dependency_unavailable",
  "resource_unavailable",
  "internal_error",
]);

export class TaskDeadlineFeatureError extends Error {
  constructor(
    readonly code: TaskDeadlineErrorCode,
    readonly status: number,
  ) {
    super(code);
    this.name = "TaskDeadlineFeatureError";
  }
}

export interface TaskDeadlineClientOptions {
  baseUrl: string;
  tenantId: () => string;
  csrfToken: () => string | null;
  fetchImpl?: typeof fetch;
}

function fallback(status: number): TaskDeadlineErrorCode {
  if (status === 401) return "authentication_required";
  if (status === 403) return "access_denied";
  if (status === 404) return "resource_unavailable";
  if (status === 409) return "precondition_failed";
  if (status === 422) return "validation_failed";
  if (status === 503) return "dependency_unavailable";
  return "internal_error";
}

async function closedError(
  response: Response,
): Promise<TaskDeadlineFeatureError> {
  let code = fallback(response.status);
  try {
    const body = (await response.json()) as { detail?: { code?: unknown } };
    const candidate = body.detail?.code;
    if (
      typeof candidate === "string" &&
      knownCodes.has(candidate as TaskDeadlineErrorCode)
    ) {
      code = candidate as TaskDeadlineErrorCode;
    }
  } catch {
    // Only the bounded code crosses from an untrusted error response.
  }
  return new TaskDeadlineFeatureError(code, response.status);
}

export class TaskDeadlineFeatureClient {
  private readonly baseUrl: string;
  private readonly tenantId: () => string;
  private readonly csrfToken: () => string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: TaskDeadlineClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.tenantId = options.tenantId;
    this.csrfToken = options.csrfToken;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private headers(mutation: boolean): Record<string, string> {
    const tenantId = this.tenantId();
    if (!tenantId)
      throw new TaskDeadlineFeatureError("authentication_required", 401);
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-SKLegal-Tenant": tenantId,
      "X-Correlation-ID": crypto.randomUUID(),
    };
    if (mutation) {
      const csrf = this.csrfToken();
      if (!csrf)
        throw new TaskDeadlineFeatureError("authentication_required", 401);
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrf;
    }
    return headers;
  }

  private async request<T>(
    path: string,
    method: "GET" | "POST",
    body?: unknown,
    idempotencyKey?: string,
  ): Promise<T> {
    const headers = this.headers(method === "POST");
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
      throw new TaskDeadlineFeatureError("dependency_unavailable", 503);
    }
    if (!response.ok) throw await closedError(response);
    return (await response.json()) as T;
  }

  listTasks(matterId: string): Promise<TaskRead[]> {
    return this.request<{ tasks: TaskRead[] }>(
      `/v1/matters/${encodeURIComponent(matterId)}/tasks`,
      "GET",
    ).then((response) => response.tasks);
  }

  listDeadlines(matterId: string): Promise<DeadlineRead[]> {
    return this.request<{ deadlines: DeadlineRead[] }>(
      `/v1/matters/${encodeURIComponent(matterId)}/deadlines`,
      "GET",
    ).then((response) => response.deadlines);
  }

  createTask(
    matterId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<MutationReceipt<TaskRead>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/tasks`,
      "POST",
      command,
      idempotencyKey,
    );
  }

  transitionTask(
    matterId: string,
    taskId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<MutationReceipt<TaskRead>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/tasks/${encodeURIComponent(taskId)}/transitions`,
      "POST",
      command,
      idempotencyKey,
    );
  }

  computeDeadline(
    matterId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<MutationReceipt<DeadlineRead>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/deadlines`,
      "POST",
      command,
      idempotencyKey,
    );
  }

  reviewDeadline(
    matterId: string,
    deadlineId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<MutationReceipt<DeadlineRead>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/deadlines/${encodeURIComponent(deadlineId)}/reviews`,
      "POST",
      command,
      idempotencyKey,
    );
  }

  simulate(
    matterId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<MutationReceipt<SimulationReceipt>> {
    return this.request(
      `/v1/matters/${encodeURIComponent(matterId)}/action-simulations`,
      "POST",
      command,
      idempotencyKey,
    );
  }
}
