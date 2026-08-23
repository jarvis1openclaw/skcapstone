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

export function FeatureAvailabilityPage(props: {
  title: string;
  state: "safely-unavailable" | "post-mvp";
  reason: string;
  alternativeTo?: "/matters" | "/corpus";
  alternativeLabel?: string;
}) {
  return (
    <section
      aria-labelledby="sl-feature-heading"
      data-feature-state={props.state}
    >
      <h1 id="sl-feature-heading">{props.title}</h1>
      <p>
        <strong>{props.state}</strong>
      </p>
      <p>{props.reason}</p>
      {props.alternativeTo !== undefined && (
        <p>
          <Link to={props.alternativeTo}>
            {props.alternativeLabel ?? "Open available workbench"}
          </Link>
        </p>
      )}
      <p className="sl-record-meta">
        No record, Approval, Execution Event, receipt, or external effect was
        created.
      </p>
    </section>
  );
}
