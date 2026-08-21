/**
 * React binding for the SKLegal session.
 *
 * Wraps the shared session store so components re-render when the session
 * or active tenant changes. Tenant switches go through switchTenant,
 * which fails closed on tenants the principal does not belong to.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { Session } from "./session";
import { switchTenant } from "./session";
import { getSession, setSession, subscribeSession } from "./sessionStore";

interface SessionContextValue {
  session: Session | null;
  setActiveTenant: (tenantId: string) => void;
  clearSession: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider(props: { children: ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(() =>
    getSession(),
  );

  useEffect(() => subscribeSession(setSessionState), []);

  const setActiveTenant = useCallback((tenantId: string) => {
    const current = getSession();
    if (current === null) {
      return;
    }
    setSession(switchTenant(current, tenantId));
  }, []);

  const clearSession = useCallback(() => {
    setSession(null);
  }, []);

  const value = useMemo(
    () => ({ session, setActiveTenant, clearSession }),
    [session, setActiveTenant, clearSession],
  );

  return (
    <SessionContext.Provider value={value}>
      {props.children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession must be used inside SessionProvider");
  }
  return value;
}
