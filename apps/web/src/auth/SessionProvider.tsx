import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { QueryClient } from "@tanstack/react-query";

import { getSession, setSession, subscribeSession } from "./sessionStore";
import { SessionClient } from "./sessionClient";
import type { Session } from "./session";

interface SessionContextValue {
  session: Session | null;
  csrfToken: string | null;
  ready: boolean;
  signIn: (credentialReference: string, tenantId: string) => Promise<void>;
  setActiveTenant: (tenantId: string) => Promise<void>;
  clearSession: () => Promise<void>;
  invalidate: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider(props: {
  client?: SessionClient;
  queryClient?: QueryClient;
  children: ReactNode;
}) {
  const client = useMemo(
    () =>
      props.client ??
      new SessionClient(
        "/api",
        async () => new Response(null, { status: 401 }),
      ),
    [props.client],
  );
  const queryClient = useMemo(
    () => props.queryClient ?? new QueryClient(),
    [props.queryClient],
  );
  const [session, setSessionState] = useState<Session | null>(() => getSession());
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const install = useCallback(
    (envelope: Awaited<ReturnType<SessionClient["current"]>>) => {
      if (envelope === null) {
        setSession(null);
        setCsrfToken(null);
        return;
      }
      const nextSession: Session = {
        principal: envelope.principal,
        tenants: envelope.tenants,
        activeTenantId: envelope.activeTenantId,
      };
      const nextCsrf = envelope.csrfToken;
      setSession(nextSession);
      setCsrfToken(nextCsrf || null);
    },
    [],
  );

  const invalidate = useCallback(() => {
    setSession(null);
    setCsrfToken(null);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => subscribeSession(setSessionState), []);

  useEffect(() => {
    let active = true;
    client
      .current()
      .then((envelope) => {
        if (active) install(envelope);
      })
      .catch(() => {
        if (active) invalidate();
      })
      .finally(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, [install, invalidate, client]);

  useEffect(() => {
    const receive = (event: StorageEvent) => {
      if (event.key === "sklegal.session.revoked") invalidate();
    };
    globalThis.addEventListener?.("storage", receive);
    return () => globalThis.removeEventListener?.("storage", receive);
  }, [invalidate]);

  const signIn = useCallback(
    async (credentialReference: string, tenantId: string) => {
      install(await client.bootstrap(credentialReference, tenantId));
      queryClient.clear();
    },
    [install, client, queryClient],
  );

  const setActiveTenant = useCallback(
    async (tenantId: string) => {
      const envelope = await client.switchTenant(tenantId).catch((cause) => {
        invalidate();
        throw cause;
      });
      queryClient.clear();
      install(envelope);
    },
    [install, invalidate, client, queryClient],
  );

  const clearSession = useCallback(async () => {
    try {
      await client.signOut();
    } finally {
      invalidate();
      try {
        globalThis.localStorage?.setItem(
          "sklegal.session.revoked",
          String(Date.now()),
        );
        globalThis.localStorage?.removeItem("sklegal.session.revoked");
      } catch {
        // Multi-tab signaling is best effort. Session revocation is server-side.
      }
    }
  }, [invalidate, client]);

  const value = useMemo(
    () => ({
      session,
      csrfToken,
      ready,
      signIn,
      setActiveTenant,
      clearSession,
      invalidate,
    }),
    [
      clearSession,
      csrfToken,
      invalidate,
      ready,
      session,
      setActiveTenant,
      signIn,
    ],
  );

  return (
    <SessionContext.Provider value={value}>{props.children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession must be used inside SessionProvider");
  }
  return value;
}
