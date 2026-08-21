import { afterEach, describe, expect, it } from "vitest";
import { isRedirect } from "@tanstack/react-router";

import { routeRequirements } from "./auth/authorization";
import { setSession } from "./auth/sessionStore";
import type { Session } from "./auth/session";
import { primaryNavItems } from "./shell/Navigation";
import { guardRoute, protectedRouteRequirements, routeTree } from "./router";

const fullSession: Session = {
  principal: {
    id: "p1",
    displayName: "Avery Solicitor",
    capabilities: [
      "tenant.read",
      "client.read",
      "matter.read",
      "calendar.read",
      "task.read",
      "corpus.read",
      "agentrun.read",
      "approval.read",
      "tenant.admin",
    ],
    tenantIds: ["tenant-a"],
  },
  tenants: [{ id: "tenant-a", displayName: "Tenant A" }],
  activeTenantId: "tenant-a",
};

afterEach(() => {
  setSession(null);
});

describe("route guards", () => {
  it("redirects unauthenticated users to sign-in", () => {
    let thrown: unknown;
    try {
      guardRoute("clients")();
    } catch (cause) {
      thrown = cause;
    }
    expect(isRedirect(thrown)).toBe(true);
    expect((thrown as { options: { to: string } }).options.to).toBe("/sign-in");
  });

  it("redirects unauthorized users to forbidden", () => {
    setSession({
      ...fullSession,
      principal: { ...fullSession.principal, capabilities: ["tenant.read"] },
    });
    let thrown: unknown;
    try {
      guardRoute("administration")();
    } catch (cause) {
      thrown = cause;
    }
    expect(isRedirect(thrown)).toBe(true);
    expect((thrown as { options: { to: string } }).options.to).toBe(
      "/forbidden",
    );
  });

  it("does not throw for an authorized session", () => {
    setSession(fullSession);
    expect(() => guardRoute("matters")()).not.toThrow();
  });
});

describe("route tree coverage", () => {
  it("declares a capability requirement for every protected path", () => {
    const paths = Object.keys(protectedRouteRequirements);
    expect(paths).toContain("/clients");
    expect(paths).toContain("/clients/$clientId");
    expect(paths).toContain("/matters");
    expect(paths).toContain("/matters/$matterId");
    for (const key of Object.values(protectedRouteRequirements)) {
      expect(routeRequirements[key]).toBeDefined();
    }
  });

  it("keeps sign-in and forbidden routes out of the protected set", () => {
    expect(Object.keys(protectedRouteRequirements)).not.toContain("/sign-in");
    expect(Object.keys(protectedRouteRequirements)).not.toContain("/forbidden");
  });

  it("attaches a beforeLoad guard to every protected route in the tree", () => {
    interface RouteOptions {
      path?: string;
      beforeLoad?: unknown;
    }
    const children = Object.values(
      (
        routeTree as unknown as {
          children: Record<string, { options: RouteOptions }>;
        }
      ).children,
    );
    const protectedPaths = new Set(Object.keys(protectedRouteRequirements));
    const seen = new Set<string>();
    for (const route of children) {
      const path = route.options.path;
      if (path !== undefined && protectedPaths.has(path)) {
        seen.add(path);
        expect(typeof route.options.beforeLoad).toBe("function");
      }
    }
    expect(seen).toEqual(protectedPaths);
  });

  it("maps every primary nav item to a declared route requirement", () => {
    for (const item of primaryNavItems) {
      expect(Object.values(protectedRouteRequirements)).toContain(item.key);
    }
  });
});
