/**
 * SKLegal route tree.
 *
 * Every protected route declares its capability requirement in
 * auth/authorization.ts and enforces it in beforeLoad. The guard fails
 * closed: no session means redirect to sign-in, a missing capability or
 * tenant mismatch means redirect to forbidden. These guards are a UX
 * gate only; server authorization remains authoritative for all data.
 */

import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from "@tanstack/react-router";

import {
  authorizeRoute,
  routeRequirements,
  type RouteRequirementKey,
} from "./auth/authorization";
import { getSession } from "./auth/sessionStore";
import { useSession } from "./auth/SessionProvider";
import { newCorrelationId } from "./api/correlation";
import { AppShell } from "./shell/AppShell";
import { FeatureAvailabilityPage, HomePage } from "./pages/HomePage";
import { SignInPage } from "./pages/SignInPage";
import { ClientsPage } from "./pages/ClientsPage";
import { ClientDetailPage } from "./pages/ClientDetailPage";
import { MattersPage } from "./pages/MattersPage";
import { MatterDetailPage } from "./pages/MatterDetailPage";
import { CorpusPage } from "./pages/CorpusPage";
import { ForbiddenPage, NotFoundPage } from "./pages/StatusPages";

/** Route path to capability requirement, asserted complete by router tests. */
export const protectedRouteRequirements: Readonly<
  Record<string, RouteRequirementKey>
> = {
  "/": "home",
  "/clients": "clients",
  "/clients/$clientId": "clientDetail",
  "/matters": "matters",
  "/matters/$matterId": "matterDetail",
  "/calendar": "calendar",
  "/work-queue": "workQueue",
  "/corpus": "corpus",
  "/agent-runs": "agentRuns",
  "/approvals": "approvals",
  "/administration": "administration",
};

export function guardRoute(requirementKey: RouteRequirementKey): () => void {
  return () => {
    const decision = authorizeRoute(
      getSession(),
      routeRequirements[requirementKey],
    );
    if (!decision.allowed) {
      if (decision.reason === "unauthenticated") {
        throw redirect({ to: "/sign-in" });
      }
      throw redirect({ to: "/forbidden" });
    }
  };
}

function RootLayout() {
  const { session } = useSession();
  return (
    <AppShell session={session}>
      <Outlet />
    </AppShell>
  );
}

const rootRoute = createRootRoute({ component: RootLayout });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: guardRoute("home"),
  component: function IndexComponent() {
    return <HomePage session={getSession()} />;
  },
});

const signInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sign-in",
  component: SignInPage,
});

const forbiddenRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/forbidden",
  component: function ForbiddenComponent() {
    return <ForbiddenPage correlationId={newCorrelationId()} />;
  },
});

const clientsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/clients",
  beforeLoad: guardRoute("clients"),
  component: ClientsPage,
});

const clientDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/clients/$clientId",
  beforeLoad: guardRoute("clientDetail"),
  component: function ClientDetailComponent() {
    const { clientId } = clientDetailRoute.useParams();
    return <ClientDetailPage clientId={clientId} />;
  },
});

const mattersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/matters",
  beforeLoad: guardRoute("matters"),
  component: MattersPage,
});

const matterDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/matters/$matterId",
  beforeLoad: guardRoute("matterDetail"),
  component: function MatterDetailComponent() {
    const { matterId } = matterDetailRoute.useParams();
    return <MatterDetailPage matterId={matterId} />;
  },
});

const calendarRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/calendar",
  beforeLoad: guardRoute("calendar"),
  component: function CalendarComponent() {
    return (
      <FeatureAvailabilityPage
        title="Calendar"
        state="safely-unavailable"
        reason="The immutable MVP API has no reviewed Deadline calendar contract."
        alternativeTo="/matters"
        alternativeLabel="Open Matters"
      />
    );
  },
});

const workQueueRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/work-queue",
  beforeLoad: guardRoute("workQueue"),
  component: function WorkQueueComponent() {
    return (
      <FeatureAvailabilityPage
        title="Work Queue"
        state="safely-unavailable"
        reason="The immutable MVP API has no reviewed Task mutation contract."
        alternativeTo="/matters"
        alternativeLabel="Open Matter workbench"
      />
    );
  },
});

const corpusRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/corpus",
  beforeLoad: guardRoute("corpus"),
  validateSearch: (search: Record<string, unknown>): { matterId?: string } => ({
    matterId:
      typeof search.matterId === "string" && search.matterId.length > 0
        ? search.matterId
        : undefined,
  }),
  component: function CorpusComponent() {
    const { matterId } = corpusRoute.useSearch();
    return <CorpusPage matterId={matterId ?? null} />;
  },
});

const agentRunsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agent-runs",
  beforeLoad: guardRoute("agentRuns"),
  component: function AgentRunsComponent() {
    return (
      <FeatureAvailabilityPage
        title="Agent Runs"
        state="safely-unavailable"
        reason="Agent Run execution is not composed into the internal public-synthetic MVP."
        alternativeTo="/corpus"
        alternativeLabel="Open governed corpus research"
      />
    );
  },
});

const approvalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/approvals",
  beforeLoad: guardRoute("approvals"),
  component: function ApprovalsComponent() {
    return (
      <FeatureAvailabilityPage
        title="Approvals"
        state="safely-unavailable"
        reason="Approval state is visible on each Matter, but no global Approval mutation surface is authorized."
        alternativeTo="/matters"
        alternativeLabel="Review Matter Approval state"
      />
    );
  },
});

const administrationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/administration",
  beforeLoad: guardRoute("administration"),
  component: function AdministrationComponent() {
    return (
      <FeatureAvailabilityPage
        title="Administration"
        state="post-mvp"
        reason="Administrative mutation is outside this bounded internal MVP."
      />
    );
  },
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  signInRoute,
  forbiddenRoute,
  clientsRoute,
  clientDetailRoute,
  mattersRoute,
  matterDetailRoute,
  calendarRoute,
  workQueueRoute,
  corpusRoute,
  agentRunsRoute,
  approvalsRoute,
  administrationRoute,
]);

export function createAppRouter() {
  return createRouter({
    routeTree,
    defaultNotFoundComponent: NotFoundPage,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
