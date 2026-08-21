import { Link } from "@tanstack/react-router";

import type { Session } from "../auth/session";

export function HomePage(props: { session: Session | null }) {
  if (props.session === null) {
    return (
      <section aria-labelledby="sl-home-heading">
        <h1 id="sl-home-heading">SKLegal</h1>
        <p>The legal workbench. Sign in to continue.</p>
        <p>
          <Link to="/sign-in">Sign in</Link>
        </p>
      </section>
    );
  }
  const activeTenant = props.session.tenants.find(
    (tenant) => tenant.id === props.session?.activeTenantId,
  );
  return (
    <section aria-labelledby="sl-home-heading">
      <h1 id="sl-home-heading">SKLegal</h1>
      <p>
        Signed in as {props.session.principal.displayName} in tenant{" "}
        {activeTenant?.displayName ?? "unknown"}.
      </p>
      <p>
        Start with <Link to="/clients">Clients</Link> or{" "}
        <Link to="/matters">Matters</Link>.
      </p>
    </section>
  );
}

export function PlaceholderPage(props: { title: string; card: string }) {
  return (
    <section aria-labelledby="sl-placeholder-heading">
      <h1 id="sl-placeholder-heading">{props.title}</h1>
      <p>This workbench area is delivered by card {props.card}.</p>
    </section>
  );
}
