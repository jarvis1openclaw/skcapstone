import { Link } from "@tanstack/react-router";

import { CorrelationBadge } from "../components/CorrelationBadge";

export function ForbiddenPage(props: { correlationId: string }) {
  return (
    <section aria-labelledby="sl-forbidden-heading" className="sl-error-state">
      <h1 id="sl-forbidden-heading">Access not permitted</h1>
      <p>
        You do not have access to this area. Navigation visibility is only a
        convenience; every protected record is authorized again by the server.
      </p>
      <CorrelationBadge correlationId={props.correlationId} />
      <p>
        <Link to="/">Return home</Link>
      </p>
    </section>
  );
}

export function NotFoundPage() {
  return (
    <section aria-labelledby="sl-not-found-heading" className="sl-error-state">
      <h1 id="sl-not-found-heading">Page not found</h1>
      <p>This page does not exist or is not available to you.</p>
      <p>
        <Link to="/">Return home</Link>
      </p>
    </section>
  );
}
