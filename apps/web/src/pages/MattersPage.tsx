import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { useApiClient } from "../api/ApiContext";
import type { MatterSummary } from "../api/types";
import { QueryBoundary } from "../components/QueryBoundary";
import { StatusBadge } from "../components/StatusBadge";
import { matterStatus } from "../design/tokens";

export function MatterList(props: { matters: readonly MatterSummary[] }) {
  if (props.matters.length === 0) {
    return <p>No matters are available in this tenant.</p>;
  }
  return (
    <ul className="sl-record-list">
      {props.matters.map((matter) => (
        <li key={matter.id}>
          <Link to="/matters/$matterId" params={{ matterId: matter.id }}>
            {matter.title}
          </Link>
          <span className="sl-record-meta">{matter.clientDisplayName}</span>
          <StatusBadge status={matterStatus[matter.status]} />
        </li>
      ))}
    </ul>
  );
}

export function MattersPage() {
  const api = useApiClient();
  const query = useQuery({
    queryKey: ["matters"],
    queryFn: () => api.listMatters(),
  });
  return (
    <section aria-labelledby="sl-matters-heading">
      <h1 id="sl-matters-heading">Matters</h1>
      <QueryBoundary query={query} loadingLabel="Loading matters">
        {(result) => <MatterList matters={result.data} />}
      </QueryBoundary>
    </section>
  );
}
