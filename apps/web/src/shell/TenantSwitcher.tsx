/**
 * Tenant switcher.
 *
 * Tenants are the top security boundary, so the active tenant is always
 * visible in the shell header. Implemented as a native labelled select:
 * fully keyboard operable and screen-reader announced without custom
 * widget risk. Switching fails closed for non-member tenants (see
 * auth/session.ts) and the server re-authorizes under the new tenant on
 * every request.
 */

import { useSession } from "../auth/SessionProvider";

export function TenantSwitcher() {
  const { session, setActiveTenant } = useSession();
  if (session === null) {
    return null;
  }
  if (session.tenants.length === 1) {
    const only = session.tenants[0];
    return (
      <div className="sl-tenant-switcher">
        <span className="sl-tenant-label">Tenant</span>
        <span className="sl-tenant-name">{only?.displayName ?? "Unknown"}</span>
      </div>
    );
  }
  return (
    <div className="sl-tenant-switcher">
      <label htmlFor="sl-tenant-select" className="sl-tenant-label">
        Tenant
      </label>
      <select
        id="sl-tenant-select"
        className="sl-tenant-select"
        value={session.activeTenantId}
        onChange={(event) => setActiveTenant(event.target.value)}
      >
        {session.tenants.map((tenant) => (
          <option key={tenant.id} value={tenant.id}>
            {tenant.displayName}
          </option>
        ))}
      </select>
    </div>
  );
}
