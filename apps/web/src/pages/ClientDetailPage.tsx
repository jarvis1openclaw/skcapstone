import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { useApiClient } from "../api/ApiContext";
import type { ClientDetail } from "../api/types";
import { QueryBoundary } from "../components/QueryBoundary";
import { StatusBadge } from "../components/StatusBadge";
import { matterStatus } from "../design/tokens";

export function ClientDetailView(props: { client: ClientDetail }) {
  const { client } = props;
  return (
    <section aria-labelledby="sl-client-heading">
      <h1 id="sl-client-heading">{client.displayName}</h1>
      <h2>Matters</h2>
      {client.matters.length === 0 ? (
        <p>No matters are recorded for this client.</p>
      ) : (
        <ul className="sl-record-list">
          {client.matters.map((matter) => (
            <li key={matter.id}>
              <Link to="/matters/$matterId" params={{ matterId: matter.id }}>
                {matter.title}
              </Link>
              <StatusBadge status={matterStatus[matter.status]} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ClientDetailPage(props: { clientId: string }) {
  const api = useApiClient();
  const query = useQuery({
    queryKey: ["clients", props.clientId],
    queryFn: () => api.getClient(props.clientId),
  });
  return (
    <QueryBoundary query={query} loadingLabel="Loading client">
      {(result) => <ClientDetailView client={result.data} />}
    </QueryBoundary>
  );
}
