/**
 * Client-side route authorization.
 *
 * These guards decide which routes the shell renders and navigate to. They
 * are a user experience gate only: every protected record is authorized
 * again by the API and CapAuth on the server, and hidden UI is never a
 * substitute for server authorization. When no decision input is
 * available the guard denies (fail closed).
 */

import type { Session } from "./session";

export interface RouteRequirement {
  /** Capability the principal must hold, e.g. "client.read". */
  capability: string;
}

export type DenialReason = "unauthenticated" | "forbidden" | "tenant_mismatch";

export type AuthorizationDecision =
  { allowed: true } | { allowed: false; reason: DenialReason };

/**
 * Capability required by each protected route in the shell. Route paths
 * are defined in router.tsx and reference this table so tests can assert
 * that every protected route has a guard.
 */
export const routeRequirements = {
  home: { capability: "tenant.read" },
  clients: { capability: "client.read" },
  clientDetail: { capability: "client.read" },
  matters: { capability: "matter.read" },
  matterDetail: { capability: "matter.read" },
  calendar: { capability: "calendar.read" },
  workQueue: { capability: "task.read" },
  corpus: { capability: "corpus.read" },
  agentRuns: { capability: "agentrun.read" },
  approvals: { capability: "approval.read" },
  administration: { capability: "tenant.admin" },
} as const satisfies Record<string, RouteRequirement>;

export type RouteRequirementKey = keyof typeof routeRequirements;

export function authorizeRoute(
  session: Session | null,
  requirement: RouteRequirement,
): AuthorizationDecision {
  if (session === null) {
    return { allowed: false, reason: "unauthenticated" };
  }
  if (!session.principal.tenantIds.includes(session.activeTenantId)) {
    return { allowed: false, reason: "tenant_mismatch" };
  }
  if (!session.principal.capabilities.includes(requirement.capability)) {
    return { allowed: false, reason: "forbidden" };
  }
  return { allowed: true };
}
