import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderStatic } from "../testing/ssr";
import { setSession } from "../auth/sessionStore";
import type { Session } from "../auth/session";

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

import { SessionProvider } from "../auth/SessionProvider";
import { Navigation, primaryNavItems, visibleNavItems } from "./Navigation";
import { AppShell } from "./AppShell";
import { TenantSwitcher } from "./TenantSwitcher";

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

afterEach(() => {
  setSession(null);
});

describe("Navigation", () => {
  it("renders a labelled nav landmark", () => {
    const html = renderStatic(<Navigation session={session} />);
    expect(html).toContain("<nav");
    expect(html).toContain('aria-label="Primary"');
  });

  it("shows only routes the session is authorized for (UX gating)", () => {
    const visible = visibleNavItems(session);
    expect(visible.map((item) => item.label)).toEqual([
      "Home",
      "Clients",
      "Matters",
    ]);
    expect(visibleNavItems(null)).toEqual([]);
  });

  it("declares a route requirement for every nav item", () => {
    for (const item of primaryNavItems) {
      expect(item.key.length).toBeGreaterThan(0);
      expect(item.to.startsWith("/")).toBe(true);
    }
  });

  it("uses roving tabindex so the list is one tab stop", () => {
    const html = renderStatic(<Navigation session={session} />);
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('tabindex="-1"');
  });
});

describe("TenantSwitcher", () => {
  it("renders a labelled select for multi-tenant principals", () => {
    setSession(session);
    const html = renderStatic(
      <SessionProvider>
        <TenantSwitcher />
      </SessionProvider>,
    );
    expect(html).toContain('for="sl-tenant-select"');
    expect(html).toContain('id="sl-tenant-select"');
    expect(html).toContain("Tenant A");
    expect(html).toContain("Tenant B");
  });

  it("renders the single tenant as static text, not a control", () => {
    setSession({ ...session, tenants: [session.tenants[0]!] });
    const html = renderStatic(
      <SessionProvider>
        <TenantSwitcher />
      </SessionProvider>,
    );
    expect(html).not.toContain("<select");
    expect(html).toContain("Tenant A");
  });

  it("renders nothing without a session", () => {
    const html = renderStatic(
      <SessionProvider>
        <TenantSwitcher />
      </SessionProvider>,
    );
    expect(html).toBe("");
  });
});

describe("AppShell", () => {
  it("renders banner, navigation, main, and contentinfo landmarks with a skip link", () => {
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="expanded">
          <p>content</p>
        </AppShell>
      </SessionProvider>,
    );
    expect(html).toContain('class="sl-skip-link"');
    expect(html).toContain('href="#sl-main"');
    expect(html).toContain('role="banner"');
    expect(html).toContain('aria-label="Primary"');
    expect(html).toContain('id="sl-main"');
    expect(html).toContain('role="contentinfo"');
  });

  it("shows the active tenant and principal in the banner", () => {
    setSession(session);
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="expanded">
          <p>content</p>
        </AppShell>
      </SessionProvider>,
    );
    expect(html).toContain("Avery Solicitor");
    expect(html).toContain("Tenant");
  });

  it("displays the session correlation identifier in the footer", () => {
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="expanded">
          <p>content</p>
        </AppShell>
      </SessionProvider>,
    );
    expect(html).toContain("Session correlation");
    expect(html).toContain("sl-correlation-id");
  });

  it("collapses navigation behind an accessible disclosure in compact mode", () => {
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="compact">
          <p>content</p>
        </AppShell>
      </SessionProvider>,
    );
    expect(html).toContain('data-layout="compact"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-controls="sl-primary-nav"');
  });

  it("keeps navigation expanded without a toggle in expanded mode", () => {
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={session} layoutMode="expanded">
          <p>content</p>
        </AppShell>
      </SessionProvider>,
    );
    expect(html).toContain('data-layout="expanded"');
    expect(html).not.toContain("sl-nav-toggle");
    expect(html).toContain('aria-label="Primary"');
  });

  it("hides navigation and identity chrome when signed out", () => {
    const html = renderStatic(
      <SessionProvider>
        <AppShell session={null} layoutMode="expanded">
          <p>content</p>
        </AppShell>
      </SessionProvider>,
    );
    expect(html).not.toContain('aria-label="Primary"');
    expect(html).not.toContain("sl-tenant");
  });
});
