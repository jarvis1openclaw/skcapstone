/**
 * Provides the ApiClient to the React tree. The client is rebuilt when
 * the active tenant changes so every request carries the current tenant
 * scope; the server still authorizes each request independently.
 */

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { ApiClient } from "./client";
import type { SessionCredentialStore } from "./credentials";
import { getSession } from "../auth/sessionStore";

const ApiContext = createContext<ApiClient | null>(null);

export function ApiProvider(props: {
  baseUrl: string;
  credentials: SessionCredentialStore;
  children: ReactNode;
}) {
  const client = useMemo(
    () =>
      new ApiClient({
        baseUrl: props.baseUrl,
        credentials: props.credentials,
        tenantId: () => getSession()?.activeTenantId ?? "",
      }),
    [props.baseUrl, props.credentials],
  );
  return (
    <ApiContext.Provider value={client}>{props.children}</ApiContext.Provider>
  );
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiContext);
  if (client === null) {
    throw new Error("useApiClient must be used inside ApiProvider");
  }
  return client;
}
