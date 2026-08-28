import { createContext, useContext, useMemo, type ReactNode } from "react";

import { ApiClient } from "./client";
import { getSession } from "../auth/sessionStore";
import { useSession } from "../auth/SessionProvider";

const publicSyntheticCorpusPins =
  import.meta.env.VITE_SKLEGAL_PUBLIC_SYNTHETIC_PREVIEW === "1"
    ? {
        releaseId: "public-synthetic-release-v1",
        projectionGeneration: 1,
        coreWatermark: 1,
      }
    : undefined;

const ApiContext = createContext<ApiClient | null>(null);

export function ApiProvider(props: { baseUrl: string; children: ReactNode }) {
  const { csrfToken, invalidate } = useSession();
  const client = useMemo(
    () =>
      new ApiClient({
        baseUrl: props.baseUrl,
        tenantId: () => getSession()?.activeTenantId ?? "",
        csrfToken: () => csrfToken,
        onAuthenticationFailure: invalidate,
        corpusProjectionPins: publicSyntheticCorpusPins,
      }),
    [csrfToken, invalidate, props.baseUrl],
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
