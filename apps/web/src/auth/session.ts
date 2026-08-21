/**
 * Session model for the SKLegal shell.
 *
 * A session is established by the deployment authentication boundary and
 * delivered to the shell by the API. The shell holds it in memory and in
 * approved session storage only; no protected data is written to
 * localStorage or other persistent browser storage.
 */

export interface SessionPrincipal {
  id: string;
  displayName: string;
  /** CapAuth capabilities granted to the principal, e.g. "client.read". */
  capabilities: readonly string[];
  /** Tenants the principal is a member of. */
  tenantIds: readonly string[];
}

export interface TenantSummary {
  id: string;
  displayName: string;
}

export interface Session {
  principal: SessionPrincipal;
  tenants: readonly TenantSummary[];
  activeTenantId: string;
}

export class TenantSwitchDenied extends Error {
  constructor(tenantId: string) {
    super(`tenant switch denied for tenant ${tenantId}`);
    this.name = "TenantSwitchDenied";
  }
}

/** Fail closed: a principal may only activate a tenant they belong to. */
export function switchTenant(session: Session, tenantId: string): Session {
  const allowed =
    session.principal.tenantIds.includes(tenantId) &&
    session.tenants.some((tenant) => tenant.id === tenantId);
  if (!allowed) {
    throw new TenantSwitchDenied(tenantId);
  }
  return { ...session, activeTenantId: tenantId };
}
