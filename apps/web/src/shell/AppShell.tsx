/**
 * SKLegal application shell.
 *
 * Landmark structure: skip link, banner (product identity, tenant
 * switcher, principal), primary navigation, main content, contentinfo
 * footer with the session correlation identifier. Below the expanded
 * breakpoint the navigation moves behind a disclosure control; the mode
 * is decided by layoutModeForWidth and mirrored by styles.css.
 */

import { useState, type ReactNode } from "react";

import { newCorrelationId } from "../api/correlation";
import { CorrelationBadge } from "../components/CorrelationBadge";
import { layoutModeForWidth, type LayoutMode } from "../design/tokens";
import { PRODUCT_NAME } from "../foundation";
import type { Session } from "../auth/session";
import { Navigation } from "./Navigation";
import { TenantSwitcher } from "./TenantSwitcher";

const sessionCorrelationId = newCorrelationId();

export function AppShell(props: {
  session: Session | null;
  layoutMode?: LayoutMode;
  viewportWidthPx?: number;
  children: ReactNode;
}) {
  const [navOpen, setNavOpen] = useState(false);
  const layoutMode =
    props.layoutMode ??
    (props.viewportWidthPx !== undefined
      ? layoutModeForWidth(props.viewportWidthPx)
      : "expanded");
  const navId = "sl-primary-nav";

  return (
    <div className="sl-shell" data-layout={layoutMode}>
      <a className="sl-skip-link" href="#sl-main">
        Skip to main content
      </a>
      <header className="sl-banner" role="banner">
        <span className="sl-product-name">{PRODUCT_NAME}</span>
        {layoutMode === "compact" && props.session !== null ? (
          <button
            type="button"
            className="sl-nav-toggle"
            aria-expanded={navOpen}
            aria-controls={navId}
            onClick={() => setNavOpen((open) => !open)}
          >
            Menu
          </button>
        ) : null}
        <div className="sl-banner-side">
          <TenantSwitcher />
          {props.session !== null ? (
            <span className="sl-principal">
              {props.session.principal.displayName}
            </span>
          ) : null}
        </div>
      </header>
      {props.session !== null && (layoutMode === "expanded" || navOpen) ? (
        <Navigation session={props.session} id={navId} />
      ) : null}
      <main id="sl-main" className="sl-main" tabIndex={-1}>
        {props.children}
      </main>
      <footer className="sl-contentinfo" role="contentinfo">
        <CorrelationBadge
          correlationId={sessionCorrelationId}
          label="Session correlation"
        />
      </footer>
    </div>
  );
}
