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
import { HomePage, PlaceholderPage } from "./pages/HomePage";
import { SignInPage } from "./pages/SignInPage";
import { ClientsPage } from "./pages/ClientsPage";
import { ClientDetailPage } from "./pages/ClientDetailPage";
import { MattersPage } from "./pages/MattersPage";
import { MatterDetailPage } from "./pages/MatterDetailPage";
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
    return <PlaceholderPage title="Calendar" card="SKL-S4-05" />;
  },
});

const workQueueRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/work-queue",
  beforeLoad: guardRoute("workQueue"),
  component: function WorkQueueComponent() {
    return <PlaceholderPage title="Work Queue" card="SKL-S4-06" />;
  },
});

const corpusRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/corpus",
  beforeLoad: guardRoute("corpus"),
  component: function CorpusComponent() {
    return <PlaceholderPage title="Corpus" card="SKL-S4-03" />;
  },
});

const agentRunsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agent-runs",
  beforeLoad: guardRoute("agentRuns"),
  component: function AgentRunsComponent() {
    return <PlaceholderPage title="Agent Runs" card="SKL-S4-04" />;
  },
});

const approvalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/approvals",
  beforeLoad: guardRoute("approvals"),
  component: function ApprovalsComponent() {
    return <PlaceholderPage title="Approvals" card="SKL-S4-06" />;
  },
});

const administrationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/administration",
  beforeLoad: guardRoute("administration"),
  component: function AdministrationComponent() {
    return <PlaceholderPage title="Administration" card="SKL-S4-07" />;
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
