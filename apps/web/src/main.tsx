import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import { ApiProvider } from "./api/ApiContext";
import { SessionClient } from "./auth/sessionClient";
import { SessionProvider } from "./auth/SessionProvider";
import { createAppRouter } from "./router";
import "./styles.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("SKLegal root element is missing");
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Fail closed: authorization and not-found denials are never retried,
      // and stale protected data is always revalidated against the server.
      retry: false,
      staleTime: 0,
    },
  },
});

const router = createAppRouter();
const apiBase = import.meta.env.VITE_SKLEGAL_API_BASE || "/api";
const sessionClient = new SessionClient(apiBase);

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SessionProvider client={sessionClient} queryClient={queryClient}>
        <ApiProvider baseUrl={apiBase}>
          <RouterProvider router={router} />
        </ApiProvider>
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
