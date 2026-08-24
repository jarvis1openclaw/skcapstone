import { MatterAnalysisRead, parseMatterAnalysisRead } from "./contract";

export type JoinedAnalysisFailure =
  | "authentication_required"
  | "access_denied"
  | "resource_unavailable"
  | "dependency_unavailable"
  | "invalid_response";

export class JoinedAnalysisError extends Error {
  constructor(public readonly kind: JoinedAnalysisFailure) {
    super("Joined analysis is unavailable.");
  }
}

export async function loadJoinedAnalysis(options: {
  baseUrl: string;
  tenantId: string;
  matterId: string;
  limit?: number;
  cursor?: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}): Promise<MatterAnalysisRead> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const url = new URL(
    `/v1/matters/${encodeURIComponent(options.matterId)}/analysis`,
    options.baseUrl,
  );
  url.searchParams.set("limit", String(options.limit ?? 50));
  if (options.cursor !== undefined)
    url.searchParams.set("cursor", options.cursor);
  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { "X-SKLegal-Tenant": options.tenantId },
      signal: options.signal,
    });
  } catch {
    throw new JoinedAnalysisError("dependency_unavailable");
  }
  if (!response.ok) {
    if (response.status === 401)
      throw new JoinedAnalysisError("authentication_required");
    if (response.status === 403) throw new JoinedAnalysisError("access_denied");
    if (response.status === 404)
      throw new JoinedAnalysisError("resource_unavailable");
    throw new JoinedAnalysisError("dependency_unavailable");
  }
  try {
    return parseMatterAnalysisRead(await response.json());
  } catch {
    throw new JoinedAnalysisError("invalid_response");
  }
}
