/**
 * In-memory session store shared between the React tree and the router.
 *
 * Router guards run outside React, so they read the session through this
 * store; SessionProvider (session.tsx) keeps it in sync so the tree
 * re-renders on change. The store never persists beyond session storage
 * (see api/credentials.ts).
 */

import type { Session } from "./session";

let current: Session | null = null;
const listeners = new Set<(session: Session | null) => void>();

export function getSession(): Session | null {
  return current;
}

export function setSession(session: Session | null): void {
  current = session;
  for (const listener of listeners) {
    listener(current);
  }
}

export function subscribeSession(
  listener: (session: Session | null) => void,
): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
