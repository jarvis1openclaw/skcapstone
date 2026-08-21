import { describe, expect, it } from "vitest";

import { authorizeRoute, routeRequirements } from "./authorization";
import type { Session } from "./session";

const baseSession: Session = {
  principal: {
    id: "p1",
    displayName: "Avery Solicitor",
    capabilities: ["tenant.read", "client.read", "matter.read"],
    tenantIds: ["tenant-a"],
  },
  tenants: [{ id: "tenant-a", displayName: "Tenant A" }],
  activeTenantId: "tenant-a",
};

describe("authorizeRoute", () => {
  it("denies unauthenticated users", () => {
    expect(authorizeRoute(null, routeRequirements.clients)).toEqual({
      allowed: false,
      reason: "unauthenticated",
    });
  });

  it("denies a principal missing the route capability", () => {
    expect(
      authorizeRoute(baseSession, routeRequirements.administration),
    ).toEqual({
      allowed: false,
      reason: "forbidden",
    });
  });

  it("denies a session whose active tenant is not a membership (fail closed)", () => {
    const tampered: Session = { ...baseSession, activeTenantId: "tenant-b" };
    expect(authorizeRoute(tampered, routeRequirements.clients)).toEqual({
      allowed: false,
      reason: "tenant_mismatch",
    });
  });

  it("allows a principal holding the route capability", () => {
    expect(authorizeRoute(baseSession, routeRequirements.matters)).toEqual({
      allowed: true,
    });
  });
});
