import { useQuery } from "@tanstack/react-query";

import { useApiClient } from "../api/ApiContext";
import type { MatterDetail } from "../api/types";
import { QueryBoundary } from "../components/QueryBoundary";
import { StatusBadge } from "../components/StatusBadge";
import { matterStatus } from "../design/tokens";

/**
 * Matter workspace section order from the approved information
 * architecture (SKLEGAL-HIGH-LEVEL-TDD.md section 16). The shell ships
 * the navigation structure with stable section anchors; section content
 * is delivered by the matter workspace card.
 */
export const matterWorkspaceSections = [
  { id: "overview", label: "Overview" },
  { id: "parties", label: "Parties" },
  { id: "timeline", label: "Timeline" },
  { id: "facts-and-tensions", label: "Facts and tensions" },
  { id: "evidence", label: "Evidence" },
  { id: "issues-and-claims", label: "Issues and claims" },
  { id: "authorities", label: "Authorities" },
  { id: "communications", label: "Communications" },
  { id: "deadlines-and-tasks", label: "Deadlines and tasks" },
  { id: "work-products", label: "Work products" },
  { id: "actions-and-receipts", label: "Actions and receipts" },
  { id: "audit", label: "Audit" },
] as const;

export function MatterWorkspaceNav() {
  return (
    <nav aria-label="Matter workspace" className="sl-matter-nav">
      <ul>
        {matterWorkspaceSections.map((section) => (
          <li key={section.id}>
            <a href={`#${section.id}`}>{section.label}</a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function MatterDetailView(props: { matter: MatterDetail }) {
  const { matter } = props;
  return (
    <article aria-labelledby="sl-matter-heading">
      <header>
        <h1 id="sl-matter-heading">{matter.title}</h1>
        <p className="sl-record-meta">
          Client: {matter.clientDisplayName}{" "}
          <StatusBadge status={matterStatus[matter.status]} />
        </p>
      </header>
      <MatterWorkspaceNav />
      <section id="overview" aria-label="Overview" tabIndex={-1}>
        <h2>Overview</h2>
        <p>{matter.description}</p>
        <p className="sl-record-meta">Opened {matter.openedOn}</p>
      </section>
      {matterWorkspaceSections
        .filter((section) => section.id !== "overview")
        .map((section) => (
          <section
            key={section.id}
            id={section.id}
            aria-label={section.label}
            tabIndex={-1}
          >
            <h2>{section.label}</h2>
            <p className="sl-record-meta">
              This workspace section is delivered by the client and matter
              workspace card.
            </p>
          </section>
        ))}
    </article>
  );
}

export function MatterDetailPage(props: { matterId: string }) {
  const api = useApiClient();
  const query = useQuery({
    queryKey: ["matters", props.matterId],
    queryFn: () => api.getMatter(props.matterId),
  });
  return (
    <QueryBoundary query={query} loadingLabel="Loading matter">
      {(result) => <MatterDetailView matter={result.data} />}
    </QueryBoundary>
  );
}
