/**
 * Primary navigation for the SKLegal shell.
 *
 * Items follow the approved information architecture. Items are filtered
 * by route authorization as a usability measure only; the server
 * authorizes every protected request regardless of what is rendered.
 * Keyboard support: roving tabindex with Arrow keys, Home, and End.
 */

import { useRef, type KeyboardEvent } from "react";
import { Link } from "@tanstack/react-router";

import {
  authorizeRoute,
  routeRequirements,
  type RouteRequirementKey,
} from "../auth/authorization";
import type { Session } from "../auth/session";

export interface NavItem {
  key: RouteRequirementKey;
  label: string;
  to: string;
}

export const primaryNavItems: readonly NavItem[] = [
  { key: "home", label: "Home", to: "/" },
  { key: "clients", label: "Clients", to: "/clients" },
  { key: "matters", label: "Matters", to: "/matters" },
  { key: "calendar", label: "Calendar", to: "/calendar" },
  { key: "workQueue", label: "Work Queue", to: "/work-queue" },
  { key: "corpus", label: "Corpus", to: "/corpus" },
  { key: "agentRuns", label: "Agent Runs", to: "/agent-runs" },
  { key: "approvals", label: "Approvals", to: "/approvals" },
  { key: "administration", label: "Administration", to: "/administration" },
];

export function visibleNavItems(session: Session | null): readonly NavItem[] {
  return primaryNavItems.filter(
    (item) => authorizeRoute(session, routeRequirements[item.key]).allowed,
  );
}

export function Navigation(props: { session: Session | null; id?: string }) {
  const items = visibleNavItems(props.session);
  const listRef = useRef<HTMLUListElement>(null);

  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const links =
      listRef.current?.querySelectorAll<HTMLAnchorElement>("a[data-nav-link]");
    if (links === undefined || links.length === 0) {
      return;
    }
    const currentIndex = Array.from(links).findIndex(
      (link) => link === document.activeElement,
    );
    let nextIndex: number;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % links.length;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        nextIndex =
          currentIndex < 0
            ? 0
            : (currentIndex - 1 + links.length) % links.length;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = links.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    links.item(nextIndex).focus();
  };

  return (
    <nav aria-label="Primary" id={props.id} className="sl-nav">
      <ul ref={listRef} onKeyDown={onKeyDown}>
        {items.map((item, index) => (
          <li key={item.key}>
            <Link
              to={item.to}
              data-nav-link
              tabIndex={index === 0 ? 0 : -1}
              activeProps={{ "aria-current": "page" }}
              activeOptions={{ exact: item.to === "/" }}
            >
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
