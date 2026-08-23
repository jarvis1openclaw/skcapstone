import { describe, expect, it, vi } from "vitest";

import { SessionClient } from "./sessionClient";
import { ApiError } from "../api/errors";

const envelope = {
  principal: {
    id: "95000000-0000-4000-8000-000000000002",
    displayName: "Public Synthetic Reviewer",
    capabilities: ["tenant.read"],
    tenantIds: ["10000000-0000-4000-8000-000000000001"],
  },
  tenants: [
    {
      id: "10000000-0000-4000-8000-000000000001",
      displayName: "Synthetic Tenant",
    },
  ],
  activeTenantId: "10000000-0000-4000-8000-000000000001",
  expiresAt: "2999-01-01T00:00:00Z",
  csrfToken: "csrf-public-synthetic",
};

describe("SessionClient", () => {
  it("bootstraps with a credential reference and same-origin cookies only", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify(envelope), { status: 200 }),
    ) as unknown as typeof fetch;
    const client = new SessionClient("/api/", fetchImpl);
    await client.bootstrap("development:public-synthetic:mvp", envelope.activeTenantId);
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/session/bootstrap");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers).not.toHaveProperty("Authorization");
    expect(init.body).not.toContain("csrf-public-synthetic");
  });

  it("uses CSRF for state changes and clears it after denial", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(envelope), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 403 }))
      .mockResolvedValueOnce(new Response(null, { status: 403 })) as unknown as typeof fetch;
    const client = new SessionClient("/api", fetchImpl);
    await client.bootstrap("development:public-synthetic:mvp", envelope.activeTenantId);
    await expect(client.refresh()).rejects.toBeInstanceOf(ApiError);
    const second = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[1]?.[1] as RequestInit;
    expect((second.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf-public-synthetic",
    );
    await expect(client.refresh()).rejects.toBeInstanceOf(ApiError);
    const third = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[2]?.[1] as RequestInit;
    expect((third.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("rejects expired current-session responses", async () => {
    const fetchImpl = (async () =>
      new Response(
        JSON.stringify({ ...envelope, expiresAt: "2000-01-01T00:00:00Z" }),
        { status: 200 },
      )) as typeof fetch;
    const client = new SessionClient("/api", fetchImpl);
    const error = await client.current().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("unauthenticated");
  });

  it("sanitizes API outage detail", async () => {
    const client = new SessionClient("/api", (async () => {
      throw new TypeError("private network detail");
    }) as typeof fetch);
    const error = await client.current().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as Error).message).not.toContain("private network detail");
  });
});
