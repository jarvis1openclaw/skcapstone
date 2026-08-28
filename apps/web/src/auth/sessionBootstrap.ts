import type { Session } from "./session";
import type { SessionClient, SessionEnvelope } from "./sessionClient";
import { setSession } from "./sessionStore";

function boundedSession(envelope: SessionEnvelope): Session {
  return {
    principal: envelope.principal,
    tenants: envelope.tenants,
    activeTenantId: envelope.activeTenantId,
  };
}

/** Hydrate the in-memory route guard before the router performs its first load. */
export async function hydrateSessionBeforeRouter(
  client: Pick<SessionClient, "current">,
): Promise<void> {
  try {
    const envelope = await client.current();
    setSession(envelope === null ? null : boundedSession(envelope));
  } catch {
    setSession(null);
  }
}
