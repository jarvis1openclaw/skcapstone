import { describe, expect, it } from "vitest";

import { switchTenant, TenantSwitchDenied, type Session } from "./session";

const session: Session = {
  principal: {
    id: "p1",
    displayName: "Avery Solicitor",
    capabilities: ["tenant.read"],
    tenantIds: ["tenant-a"],
  },
  tenants: [
    { id: "tenant-a", displayName: "Tenant A" },
    { id: "tenant-b", displayName: "Tenant B" },
  ],
  activeTenantId: "tenant-a",
};

describe("switchTenant", () => {
  it("switches to a tenant the principal belongs to", () => {
    const member: Session = {
      ...session,
      principal: { ...session.principal, tenantIds: ["tenant-a", "tenant-b"] },
    };
    expect(switchTenant(member, "tenant-b").activeTenantId).toBe("tenant-b");
  });

  it("fails closed for a non-member tenant", () => {
    expect(() => switchTenant(session, "tenant-b")).toThrow(TenantSwitchDenied);
  });

  it("fails closed for an unknown tenant even if membership claims it", () => {
    const forged: Session = {
      ...session,
      principal: { ...session.principal, tenantIds: ["tenant-a", "tenant-z"] },
    };
    expect(() => switchTenant(forged, "tenant-z")).toThrow(TenantSwitchDenied);
  });
});
