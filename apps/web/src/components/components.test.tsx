import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/errors";
import { renderStatic } from "../testing/ssr";
import { ErrorState } from "./ErrorState";
import { QueryBoundary } from "./QueryBoundary";
import { StatusBadge } from "./StatusBadge";

const FIXED_ID = "00000000-0000-4000-8000-000000000000";

describe("StatusBadge", () => {
  it("conveys status by text and glyph, not color alone", () => {
    const html = renderStatic(
      <StatusBadge
        status={{ label: "Overdue", glyph: "\u2715", tone: "critical" }}
      />,
    );
    expect(html).toContain("Overdue");
    expect(html).toContain("sl-status-glyph");
    expect(html).toContain('data-tone="critical"');
  });
});

describe("ErrorState", () => {
  it("announces itself with role=alert and shows the correlation id", () => {
    const html = renderStatic(
      <ErrorState kind="forbidden" correlationId={FIXED_ID} />,
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain(FIXED_ID);
    expect(html).toContain("Access not permitted");
  });

  it("never includes protected record detail", () => {
    const html = renderStatic(
      <ErrorState kind="not_found" correlationId={FIXED_ID} />,
    );
    expect(html).not.toContain("matter");
    expect(html).toContain("not available");
  });

  it("offers retry only for recoverable unavailability", () => {
    const retry = vi.fn();
    const unavailable = renderStatic(
      <ErrorState
        kind="unavailable"
        correlationId={FIXED_ID}
        onRetry={retry}
      />,
    );
    expect(unavailable).toContain("Try again");
    const forbidden = renderStatic(
      <ErrorState kind="forbidden" correlationId={FIXED_ID} onRetry={retry} />,
    );
    expect(forbidden).not.toContain("Try again");
  });
});

describe("QueryBoundary", () => {
  it("renders an aria-busy loading state while pending", () => {
    const html = renderStatic(
      <QueryBoundary
        query={{ status: "pending" }}
        loadingLabel="Loading clients"
      >
        {() => <p>data</p>}
      </QueryBoundary>,
    );
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="status"');
    expect(html).toContain("Loading clients");
  });

  it("maps typed API errors to the matching protected error state", () => {
    const error = new ApiError({
      kind: "forbidden",
      status: 403,
      correlationId: FIXED_ID,
    });
    const html = renderStatic(
      <QueryBoundary query={{ status: "error", error }} loadingLabel="Loading">
        {() => <p>data</p>}
      </QueryBoundary>,
    );
    expect(html).toContain('data-error-kind="forbidden"');
    expect(html).toContain(FIXED_ID);
  });

  it("fails closed: unknown errors render the generic state", () => {
    const html = renderStatic(
      <QueryBoundary
        query={{ status: "error", error: new Error("internal detail") }}
        loadingLabel="Loading"
      >
        {() => <p>data</p>}
      </QueryBoundary>,
    );
    expect(html).toContain('data-error-kind="unknown"');
    expect(html).not.toContain("internal detail");
  });

  it("renders children with data on success", () => {
    const html = renderStatic(
      <QueryBoundary
        query={{ status: "success", data: "result" }}
        loadingLabel="Loading"
      >
        {(data) => <p>{data}</p>}
      </QueryBoundary>,
    );
    expect(html).toContain("result");
  });
});
