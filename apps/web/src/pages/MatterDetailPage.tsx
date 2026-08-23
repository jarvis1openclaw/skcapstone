import { useQuery } from "@tanstack/react-query";

import { useApiClient } from "../api/ApiContext";
import { QueryBoundary } from "../components/QueryBoundary";
import { MatterWorkspaceView } from "./MatterWorkspace";

export { matterWorkspaceSections, MatterWorkspaceNav } from "./MatterWorkspace";

/**
 * Matter workspace page (SKL-S4-02). Loads the CapAuth-authorized
 * workspace aggregate for one matter and renders every workspace
 * section; the server enforces tenant scope and matter membership on
 * every request.
 */
export function MatterDetailPage(props: { matterId: string }) {
  const api = useApiClient();
  const workspaceQuery = useQuery({
    queryKey: ["matters", props.matterId, "workspace"],
    queryFn: () => api.getMatterWorkspace(props.matterId),
  });
  const claimQuery = useQuery({
    queryKey: ["matters", props.matterId, "claims"],
    queryFn: () => api.getClaimLedger(props.matterId),
  });
  return (
    <QueryBoundary
      query={workspaceQuery}
      loadingLabel="Loading matter workspace"
    >
      {(workspaceResult) => (
        <QueryBoundary query={claimQuery} loadingLabel="Loading Claim ledger">
          {(claimResult) => (
            <MatterWorkspaceView
              workspace={workspaceResult.data}
              claimLedger={claimResult.data}
            />
          )}
        </QueryBoundary>
      )}
    </QueryBoundary>
  );
}
