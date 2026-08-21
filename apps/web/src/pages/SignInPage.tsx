import { useState, type FormEvent } from "react";

import { setSession } from "../auth/sessionStore";
import type { Session } from "../auth/session";

/**
 * Sign-in placeholder.
 *
 * Production authentication is handled by the deployment boundary and
 * CapAuth before the shell loads; this page exists so route guards have
 * somewhere to send unauthenticated users. A development demo session is
 * available only in dev builds and never persists beyond session storage.
 */
export function SignInPage() {
  const [started, setStarted] = useState(false);
  const dev = import.meta.env.DEV;

  const startDemoSession = (event: FormEvent) => {
    event.preventDefault();
    const demo: Session = {
      principal: {
        id: "dev-principal",
        displayName: "Development User",
        capabilities: [
          "tenant.read",
          "client.read",
          "matter.read",
          "calendar.read",
          "task.read",
          "corpus.read",
          "agentrun.read",
          "approval.read",
        ],
        tenantIds: ["dev-tenant"],
      },
      tenants: [{ id: "dev-tenant", displayName: "Development Tenant" }],
      activeTenantId: "dev-tenant",
    };
    setSession(demo);
    setStarted(true);
  };

  return (
    <section aria-labelledby="sl-sign-in-heading">
      <h1 id="sl-sign-in-heading">Sign in</h1>
      <p>
        SKLegal sign-in is provided by the deployment authentication boundary.
        If you see this page in production, your session was not recognized.
      </p>
      {dev && !started ? (
        <form onSubmit={startDemoSession}>
          <button type="submit" className="sl-button">
            Start development demo session
          </button>
        </form>
      ) : null}
      {started ? (
        <p role="status">
          Development session started. Return home to continue.
        </p>
      ) : null}
    </section>
  );
}
