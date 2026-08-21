import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "./client";
import { isCorrelationId } from "./correlation";
import { SessionCredentialStore, type CredentialStorage } from "./credentials";
import { ApiError, apiErrorFromCause, apiErrorFromStatus } from "./errors";

function fakeStorage(): CredentialStorage & { calls: string[] } {
  const map = new Map<string, string>();
  const calls: string[] = [];
  return {
    calls,
    getItem: (key) => {
      calls.push(`get:${key}`);
      return map.get(key) ?? null;
    },
    setItem: (key, value) => {
      calls.push(`set:${key}`);
      map.set(key, value);
    },
    removeItem: (key) => {
      calls.push(`remove:${key}`);
      map.delete(key);
    },
  };
}

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

describe("SessionCredentialStore", () => {
  it("round-trips a credential through the approved session storage only", () => {
    const storage = fakeStorage();
    const store = new SessionCredentialStore(storage);
    store.save({ token: "t", expiresAt: "2999-01-01T00:00:00Z" });
    expect(store.loadActive(new Date("2026-01-01T00:00:00Z"))?.token).toBe("t");
    expect(storage.calls.every((call) => !call.includes("localStorage"))).toBe(
      true,
    );
  });

  it("treats an expired credential as absent and clears it", () => {
    const storage = fakeStorage();
    const store = new SessionCredentialStore(storage);
    store.save({ token: "t", expiresAt: "2000-01-01T00:00:00Z" });
    expect(store.loadActive(new Date("2026-01-01T00:00:00Z"))).toBeNull();
    expect(storage.calls).toContain("remove:sklegal.session.credential");
  });

  it("treats malformed stored data as absent", () => {
    const storage = fakeStorage();
    storage.setItem("sklegal.session.credential", "not-json");
    const store = new SessionCredentialStore(storage);
    expect(store.load()).toBeNull();
  });
});

describe("ApiClient", () => {
  function buildClient(fetchImpl: typeof fetch) {
    const storage = fakeStorage();
    const credentials = new SessionCredentialStore(storage);
    credentials.save({
      token: "session-token",
      expiresAt: "2999-01-01T00:00:00Z",
    });
    return new ApiClient({
      baseUrl: "https://api.test/",
      credentials,
      tenantId: () => "tenant-a",
      fetchImpl,
    });
  }

  it("sends tenant scope, credential, and a fresh correlation id", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    ) as unknown as typeof fetch;
    const client = buildClient(fetchImpl);
    await client.listClients();
    const [, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-SKLegal-Tenant"]).toBe("tenant-a");
    expect(headers.Authorization).toBe("Bearer session-token");
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
});
