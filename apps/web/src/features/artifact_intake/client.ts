import type {
  ArtifactErrorCode,
  ArtifactDerivationRequest,
  ArtifactIntakeCommand,
  ArtifactIntakeReceipt,
  ArtifactRead,
} from "./types";

const errorCodes = new Set<ArtifactErrorCode>([
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

export class ArtifactIntakeFeatureError extends Error {
  readonly code: ArtifactErrorCode;
  readonly status: number;

  constructor(code: ArtifactErrorCode, status: number) {
    super(code);
    this.name = "ArtifactIntakeFeatureError";
    this.code = code;
    this.status = status;
  }
}

export interface ArtifactIntakeFeatureClientOptions {
  baseUrl: string;
  tenantId: () => string;
  csrfToken: () => string | null;
  fetchImpl?: typeof fetch;
}

function fallbackCode(status: number): ArtifactErrorCode {
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
): Promise<ArtifactIntakeFeatureError> {
  let code = fallbackCode(response.status);
  try {
    const body = (await response.json()) as {
      detail?: { code?: unknown };
    };
    const candidate = body.detail?.code;
    if (
      typeof candidate === "string" &&
      errorCodes.has(candidate as ArtifactErrorCode)
    ) {
      code = candidate as ArtifactErrorCode;
    }
  } catch {
    // The response body is intentionally discarded. Only a closed code crosses.
  }
  return new ArtifactIntakeFeatureError(code, response.status);
}

export class ArtifactIntakeFeatureClient {
  private readonly baseUrl: string;
  private readonly tenantId: () => string;
  private readonly csrfToken: () => string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ArtifactIntakeFeatureClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.tenantId = options.tenantId;
    this.csrfToken = options.csrfToken;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private sessionHeaders(mutation: boolean): Record<string, string> {
    const tenantId = this.tenantId();
    if (!tenantId)
      throw new ArtifactIntakeFeatureError("authentication_required", 401);
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-SKLegal-Tenant": tenantId,
      "X-Correlation-ID": crypto.randomUUID(),
    };
    if (mutation) {
      const csrf = this.csrfToken();
      if (!csrf)
        throw new ArtifactIntakeFeatureError("authentication_required", 401);
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrf;
    }
    return headers;
  }

  async intake(
    matterId: string,
    idempotencyKey: string,
    command: unknown,
  ): Promise<ArtifactIntakeReceipt> {
    const headers = this.sessionHeaders(true);
    headers["Idempotency-Key"] = idempotencyKey;
    let response: Response;
    try {
      response = await this.fetchImpl(
        `${this.baseUrl}/v1/matters/${encodeURIComponent(matterId)}/artifacts`,
        {
          method: "POST",
          credentials: "same-origin",
          headers,
          body: JSON.stringify(command),
        },
      );
    } catch {
      throw new ArtifactIntakeFeatureError("dependency_unavailable", 503);
    }
    if (!response.ok) throw await closedError(response);
    return (await response.json()) as ArtifactIntakeReceipt;
  }

  async get(matterId: string, artifactId: string): Promise<ArtifactRead> {
    let response: Response;
    try {
      response = await this.fetchImpl(
        `${this.baseUrl}/v1/matters/${encodeURIComponent(matterId)}/artifacts/${encodeURIComponent(artifactId)}`,
        {
          method: "GET",
          credentials: "same-origin",
          headers: this.sessionHeaders(false),
        },
      );
    } catch {
      throw new ArtifactIntakeFeatureError("dependency_unavailable", 503);
    }
    if (!response.ok) throw await closedError(response);
    return (await response.json()) as ArtifactRead;
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const size = 32_768;
  for (let index = 0; index < bytes.length; index += size) {
    binary += String.fromCharCode(...bytes.subarray(index, index + size));
  }
  return btoa(binary);
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}

export async function artifactCommandFromFile(input: {
  file: File;
  sourceIdentity: string;
  retentionPolicyId: string;
  requestOcr: boolean;
  requestTranscript: boolean;
}): Promise<ArtifactIntakeCommand> {
  const content = new Uint8Array(await input.file.arrayBuffer());
  const digest = hex(await crypto.subtle.digest("SHA-256", content));
  const requestedDerivations: ArtifactDerivationRequest[] = [
    {
      kind: "text_extraction",
      toolName: "synthetic-text-extraction",
      toolVersion: "1.0.0",
      outputMediaType: "text/plain",
    },
  ];
  if (input.requestOcr) {
    requestedDerivations.push({
      kind: "ocr",
      toolName: "synthetic-ocr",
      toolVersion: "1.0.0",
      outputMediaType: "text/plain",
    });
  }
  if (input.requestTranscript) {
    requestedDerivations.push({
      kind: "transcript",
      toolName: "synthetic-transcript",
      toolVersion: "1.0.0",
      outputMediaType: "text/plain",
    });
  }
  return {
    source: {
      sourceSystem: "public_synthetic",
      sourceIdentity: input.sourceIdentity,
      sourceVersion: "v1",
      observedAt: new Date().toISOString(),
    },
    original: {
      filename: input.file.name,
      mediaType: input.file.type || "application/octet-stream",
      byteCount: content.byteLength,
      contentSha256: digest,
      contentBase64: bytesToBase64(content),
    },
    acquisitionMethod: "synthetic_adapter",
    classification: "public",
    privilegeState: "not_privileged",
    retentionPolicyId: input.retentionPolicyId,
    legalHoldIds: [],
    ethicalWallIds: [],
    requestedDerivations,
    proposedLinks: [],
  };
}
