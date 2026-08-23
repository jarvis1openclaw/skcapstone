/**
 * Visual regression: deterministic SSR markup snapshots for the shell and
 * its core surfaces at each layout mode. Any markup or attribute change
 * (landmarks, aria wiring, token-driven classes, breakpoint behavior)
 * fails this suite until the snapshot is deliberately updated.
 *
 * Browser-pixel regression (Playwright or equivalent) is a follow-up; the
 * execution environment for this card has no browser runner installed.
 */

import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderStatic, stabilize } from "./testing/ssr";
import { setSession } from "./auth/sessionStore";
import type { Session } from "./auth/session";

vi.mock("@tanstack/react-router", () => ({
  Link: (
    props: AnchorHTMLAttributes<HTMLAnchorElement> & {
      to: string;
      activeProps?: AnchorHTMLAttributes<HTMLAnchorElement>;
      activeOptions?: { exact?: boolean };
      children?: ReactNode;
    },
  ) => {
    const { to, children, ...rest } = props;
    delete (rest as Record<string, unknown>).activeProps;
    delete (rest as Record<string, unknown>).activeOptions;
    return (
      <a href={to} {...rest}>
        {children}
      </a>
    );
  },
}));

import { SessionProvider } from "./auth/SessionProvider";
import { AppShell } from "./shell/AppShell";
import { ClientList } from "./pages/ClientsPage";
import { ClientDetailView } from "./pages/ClientDetailPage";
import { MatterList } from "./pages/MattersPage";
import { MatterWorkspaceView } from "./pages/MatterWorkspace";
import { syntheticClaimLedger } from "./testing/claims";
import { syntheticWorkspace } from "./testing/workspace";
import { ErrorState } from "./components/ErrorState";
import { StatusBadge } from "./components/StatusBadge";
import { ForbiddenPage, NotFoundPage } from "./pages/StatusPages";
import { FeatureAvailabilityPage, HomePage } from "./pages/HomePage";

const FIXED_ID = "00000000-0000-4000-8000-000000000000";

const session: Session = {
  principal: {
    id: "p1",
    displayName: "Avery Solicitor",
    capabilities: ["tenant.read", "client.read", "matter.read"],
    tenantIds: ["tenant-a", "tenant-b"],
  },
  tenants: [
    { id: "tenant-a", displayName: "Tenant A" },
    { id: "tenant-b", displayName: "Tenant B" },
  ],
  activeTenantId: "tenant-a",
};

const clients = [
  {
    id: "c1",
    tenantId: "tenant-a",
    displayName: "Casey Rivera",
    matterCount: 2,
  },
  {
    id: "c2",
    tenantId: "tenant-a",
    displayName: "Rivera Family Trust",
    matterCount: 1,
  },
];

const matters = [
  {
    id: "m1",
    tenantId: "tenant-a",
    clientId: "c1",
    clientDisplayName: "Casey Rivera",
    title: "Rivera probate",
    status: "open" as const,
  },
  {
    id: "m2",
    tenantId: "tenant-a",
    clientId: "c2",
    clientDisplayName: "Rivera Family Trust",
    title: "Trust administration",
    status: "on_hold" as const,
  },
];

function snap(markup: string) {
  return stabilize(markup);
}

afterEach(() => {
  setSession(null);
});

describe("visual regression snapshots", () => {
  it("shell, expanded layout", () => {
    setSession(session);
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="expanded">
          <HomePage session={session} />
        </AppShell>
      </SessionProvider>,
    );
    expect(snap(html)).toMatchSnapshot();
  });

  it("shell, compact layout", () => {
    setSession(session);
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="compact">
          <HomePage session={session} />
        </AppShell>
      </SessionProvider>,
    );
    expect(snap(html)).toMatchSnapshot();
  });

  it("shell, signed out", () => {
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={null} layoutMode="expanded">
          <HomePage session={null} />
        </AppShell>
      </SessionProvider>,
    );
    expect(snap(html)).toMatchSnapshot();
  });

  it("client list and client detail", () => {
    expect(
      snap(renderStatic(<ClientList clients={clients} />)),
    ).toMatchSnapshot();
    expect(
      snap(
        renderStatic(<ClientDetailView client={{ ...clients[0]!, matters }} />),
      ),
    ).toMatchSnapshot();
  });

  it("matter list and matter workspace", () => {
    expect(
      snap(renderStatic(<MatterList matters={matters} />)),
    ).toMatchSnapshot();
    expect(
      snap(
        renderStatic(
          <MatterWorkspaceView
            workspace={syntheticWorkspace}
            claimLedger={syntheticClaimLedger()}
          />,
        ),
      ),
    ).toMatchSnapshot();
  });

  it("protected error states", () => {
    for (const kind of [
      "unauthenticated",
      "forbidden",
      "not_found",
      "unavailable",
      "unknown",
    ] as const) {
      expect(
        snap(renderStatic(<ErrorState kind={kind} correlationId={FIXED_ID} />)),
      ).toMatchSnapshot();
    }
  });

  it("status badge and status pages", () => {
    expect(
      snap(
        renderStatic(
          <StatusBadge
            status={{ label: "Overdue", glyph: "\u2715", tone: "critical" }}
          />,
        ),
      ),
    ).toMatchSnapshot();
    expect(
      snap(renderStatic(<ForbiddenPage correlationId={FIXED_ID} />)),
    ).toMatchSnapshot();
    expect(snap(renderStatic(<NotFoundPage />))).toMatchSnapshot();
    expect(
      snap(
        renderStatic(
          <FeatureAvailabilityPage
            title="Calendar"
            state="safely-unavailable"
            reason="The immutable MVP API has no reviewed Deadline calendar contract."
            alternativeTo="/matters"
            alternativeLabel="Open Matters"
          />,
        ),
      ),
    ).toMatchSnapshot();
  });
});
