import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "./client";
import { isCorrelationId } from "./correlation";
import { ApiError, apiErrorFromCause, apiErrorFromStatus } from "./errors";

describe("apiErrorFromStatus", () => {
  it("maps known statuses and fails closed on everything else", () => {
    expect(apiErrorFromStatus(401, "c").kind).toBe("unauthenticated");
    expect(apiErrorFromStatus(403, "c").kind).toBe("forbidden");
    expect(apiErrorFromStatus(404, "c").kind).toBe("not_found");
    expect(apiErrorFromStatus(503, "c").kind).toBe("unavailable");
    expect(apiErrorFromStatus(418, "c").kind).toBe("unknown");
    expect(apiErrorFromStatus(200, "c").kind).toBe("unknown");
  });
});

describe("apiErrorFromCause", () => {
  it("passes ApiError through and wraps anything else as unknown", () => {
    const original = apiErrorFromStatus(403, "c1");
    expect(apiErrorFromCause(original, "c2")).toBe(original);
    const wrapped = apiErrorFromCause(new TypeError("network"), "c2");
    expect(wrapped.kind).toBe("unknown");
    expect(wrapped.correlationId).toBe("c2");
  });
});

describe("ApiClient", () => {
  function buildClient(fetchImpl: typeof fetch) {
    return new ApiClient({
      baseUrl: "https://api.test/",
      tenantId: () => "tenant-a",
      csrfToken: () => "csrf-test-token",
      fetchImpl,
    });
  }

  it("sends exact tenant scope and a fresh correlation id without bearer material", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    ) as unknown as typeof fetch;
    const client = buildClient(fetchImpl);
    await client.listClients();
    const [, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-SKLegal-Tenant"]).toBe("tenant-a");
    expect(headers.Authorization).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
    expect(isCorrelationId(headers["X-Correlation-ID"] ?? "")).toBe(true);
  });

  it("maps denial statuses fail closed to typed errors with correlation ids", async () => {
    const fetchImpl = (async () =>
      new Response("denied", { status: 403 })) as typeof fetch;
    const client = buildClient(fetchImpl);
    const error = await client.listClients().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("forbidden");
    expect(isCorrelationId((error as ApiError).correlationId)).toBe(true);
  });

  it("maps network failures to unknown errors without leaking detail", async () => {
    const fetchImpl = (async () => {
      throw new TypeError("socket detail that must not leak");
    }) as typeof fetch;
    const client = buildClient(fetchImpl);
    const error = await client.listMatters().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("unknown");
    expect((error as ApiError).message).not.toContain("socket detail");
  });

  it("encodes record identifiers in paths", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify({}), { status: 200 }),
    ) as unknown as typeof fetch;
    const client = buildClient(fetchImpl);
    await client.getMatter("matter/1?x");
    const [url] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string];
    expect(url).toBe("https://api.test/v1/matters/matter%2F1%3Fx");
  });

  it("loads the matter-scoped claim ledger through an encoded path", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ matterId: "matter/1", claims: [] }), {
          status: 200,
        }),
    ) as unknown as typeof fetch;
    const client = buildClient(fetchImpl);
    await client.getClaimLedger("matter/1?x");
    const [url] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string];
    expect(url).toBe("https://api.test/v1/matters/matter%2F1%3Fx/claims");
  });
});
