/**
 * Protected error states.
 *
 * Rendered whenever the API denies or cannot answer. Messages are
 * generic on purpose: an error panel never reveals whether a protected
 * record exists or what it contains. Each panel shows the request
 * correlation identifier so the failure is traceable in audit.
 */

import type { ApiErrorKind } from "../api/errors";
import { CorrelationBadge } from "./CorrelationBadge";

const ERROR_COPY: Record<ApiErrorKind, { heading: string; body: string }> = {
  unauthenticated: {
    heading: "Sign in required",
    body: "Your session has ended or is not recognized. Sign in again to continue.",
  },
  forbidden: {
    heading: "Access not permitted",
    body: "You do not have access to this record. If you believe this is wrong, contact your administrator and quote the correlation identifier.",
  },
  not_found: {
    heading: "Record not available",
    body: "This record is not available to you. It may have been moved, closed, or restricted.",
  },
  unavailable: {
    heading: "Service unavailable",
    body: "The service did not answer. Your work is unchanged. Try again in a moment.",
  },
  unknown: {
    heading: "Something went wrong",
    body: "The request could not be completed. No change was made. Quote the correlation identifier if this keeps happening.",
  },
};

export function ErrorState(props: {
  kind: ApiErrorKind;
  correlationId: string;
  onRetry?: () => void;
}) {
  const copy = ERROR_COPY[props.kind];
  return (
    <section
      role="alert"
      className="sl-error-state"
      data-error-kind={props.kind}
    >
      <h2>{copy.heading}</h2>
      <p>{copy.body}</p>
      <CorrelationBadge correlationId={props.correlationId} />
      {props.onRetry !== undefined && props.kind === "unavailable" ? (
        <button type="button" className="sl-button" onClick={props.onRetry}>
          Try again
        </button>
      ) : null}
    </section>
  );
}
