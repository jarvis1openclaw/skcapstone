import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { useApiClient } from "../api/ApiContext";
import type { ClientSummary } from "../api/types";
import { QueryBoundary } from "../components/QueryBoundary";

export function ClientList(props: { clients: readonly ClientSummary[] }) {
  if (props.clients.length === 0) {
    return <p>No clients are available in this tenant.</p>;
  }
  return (
    <ul className="sl-record-list">
      {props.clients.map((client) => (
        <li key={client.id}>
          <Link to="/clients/$clientId" params={{ clientId: client.id }}>
            {client.displayName}
          </Link>
          <span className="sl-record-meta">
            {client.matterCount === 1
              ? "1 matter"
              : `${client.matterCount} matters`}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ClientsPage() {
  const api = useApiClient();
  const query = useQuery({
    queryKey: ["clients"],
    queryFn: () => api.listClients(),
  });
  return (
    <section aria-labelledby="sl-clients-heading">
      <h1 id="sl-clients-heading">Clients</h1>
      <QueryBoundary query={query} loadingLabel="Loading clients">
        {(result) => <ClientList clients={result.data} />}
      </QueryBoundary>
    </section>
  );
}
