/**
 * Query boundary.
 *
 * Single place where query loading and failure states become accessible
 * UI. Loading renders an aria-busy skeleton; errors are mapped fail
 * closed through ApiError and rendered as protected error states with
 * the request correlation identifier.
 */

import type { ReactNode } from "react";

import { apiErrorFromCause, type ApiError } from "../api/errors";
import { newCorrelationId } from "../api/correlation";
import { ErrorState } from "./ErrorState";

/** Narrow structural shape so the boundary is testable without a live QueryClient. */
export interface BoundaryQuery<T> {
  status: "pending" | "error" | "success";
  data?: T;
  error?: unknown;
  refetch?: () => void;
}

const fallbackCorrelationId = newCorrelationId();

export function QueryBoundary<T>(props: {
  query: BoundaryQuery<T>;
  loadingLabel: string;
  children: (data: T) => ReactNode;
}) {
  const { query } = props;
  if (query.status === "pending") {
    return (
      <div className="sl-loading" role="status" aria-busy="true">
        <span className="sl-skeleton" aria-hidden="true" />
        <span>{props.loadingLabel}</span>
      </div>
    );
  }
  if (query.status === "error") {
    const error: ApiError = apiErrorFromCause(
      query.error,
      fallbackCorrelationId,
    );
    return (
      <ErrorState
        kind={error.kind}
        correlationId={error.correlationId}
        onRetry={query.refetch}
      />
    );
  }
  return <>{props.children(query.data as T)}</>;
}
