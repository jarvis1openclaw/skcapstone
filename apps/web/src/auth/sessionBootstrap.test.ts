import { afterEach, describe, expect, it, vi } from "vitest";

import { hydrateSessionBeforeRouter } from "./sessionBootstrap";
import type { SessionEnvelope } from "./sessionClient";
import { getSession, setSession } from "./sessionStore";

const envelope: SessionEnvelope = {
  principal: {
    id: "95000000-0000-4000-8000-000000000002",
    displayName: "Public Synthetic Reviewer",
    capabilities: ["tenant.read", "matter.read"],
    tenantIds: ["10000000-0000-4000-8000-000000000001"],
  },
  tenants: [
    {
      id: "10000000-0000-4000-8000-000000000001",
      displayName: "Public Synthetic Tenant",
    },
  ],
  activeTenantId: "10000000-0000-4000-8000-000000000001",
  expiresAt: "2999-01-01T00:00:00Z",
  csrfToken: "public-synthetic-csrf-must-not-enter-the-route-store",
};

afterEach(() => setSession(null));

describe("hydrateSessionBeforeRouter", () => {
  it("installs only bounded session identity before protected route loading", async () => {
    await hydrateSessionBeforeRouter({ current: vi.fn(async () => envelope) });

    expect(getSession()).toEqual({
      principal: envelope.principal,
      tenants: envelope.tenants,
      activeTenantId: envelope.activeTenantId,
    });
    expect(getSession()).not.toHaveProperty("csrfToken");
    expect(getSession()).not.toHaveProperty("expiresAt");
  });

  it("fails closed for an absent current session", async () => {
    setSession({
      principal: envelope.principal,
      tenants: envelope.tenants,
      activeTenantId: envelope.activeTenantId,
    });
    await hydrateSessionBeforeRouter({ current: vi.fn(async () => null) });
    expect(getSession()).toBeNull();
  });

  it("fails closed and sanitizes current-session outages", async () => {
    await expect(
      hydrateSessionBeforeRouter({
        current: vi.fn(async () => {
          throw new Error("private upstream detail");
        }),
      }),
    ).resolves.toBeUndefined();
    expect(getSession()).toBeNull();
  });
});
