/**
 * Matter workspace view (SKL-S4-02).
 *
 * Renders the matter workspace aggregate in legal-domain terminology:
 * overview, parties, timeline, facts and tensions, evidence,
 * communications, and audit, with provenance and record gaps visible.
 * Unresolved tensions, negative approval and execution states, missing
 * sources, and stale snapshots are always rendered, never hidden. Every
 * section and record carries a stable anchor so deep links survive
 * content changes.
 */

import type {
  MatterWorkspace,
  WorkspaceFactAssertion,
  WorkspaceGap,
  WorkspaceTimelineEvent,
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import {
  communicationStatus,
  evidenceItemStatus,
  executionStateValue,
  factReviewStatus,
  matterEventStatus,
  matterStatus,
  missingSource,
  snapshotFreshness,
  statusOrFallback,
  tensionStatus,
  verificationStatus,
} from "../design/tokens";

/**
 * Matter workspace section order from the approved information
 * architecture (SKLEGAL-HIGH-LEVEL-TDD.md section 16). Section ids are
 * stable deep-link anchors.
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

/** Sections delivered by later cards render an explicit pending note. */
const pendingSectionCards: Readonly<Record<string, string>> = {
  "issues-and-claims": "SKL-S4-03",
  authorities: "SKL-S4-03",
  "deadlines-and-tasks": "SKL-S4-05",
  "work-products": "SKL-S4-04",
  "actions-and-receipts": "SKL-S4-06",
};

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

function RecordLink(props: { anchor: string; label: string }) {
  return (
    <a
      className="sl-record-link"
      href={`#${props.anchor}`}
      aria-label={`Deep link to ${props.label}`}
    >
      Link
    </a>
  );
}

function SourceNote(props: { sourcePath: string | null; missing: boolean }) {
  if (props.missing) {
    return <StatusBadge status={missingSource} />;
  }
  if (props.sourcePath === null) {
    return <span className="sl-record-meta">Source not recorded</span>;
  }
  return <span className="sl-record-meta">Source: {props.sourcePath}</span>;
}

function factValueText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value);
}

function targetTypeLabel(targetType: string): string {
  if (targetType === "matter") {
    return "Matter";
  }
  if (targetType === "matter_event") {
    return "Matter Event";
  }
  return targetType;
}

function stateKindLabel(stateKind: string): string {
  if (stateKind === "approval") {
    return "Approval";
  }
  if (stateKind === "execution") {
    return "Execution";
  }
  return stateKind;
}

function OverviewSection(props: { workspace: MatterWorkspace }) {
  const { workspace } = props;
  const { matter, provenance } = workspace;
  return (
    <section id="overview" aria-label="Overview" tabIndex={-1}>
      <h2>Overview</h2>
      <p>{matter.summary}</p>
      <dl className="sl-detail-list">
        <dt>Client</dt>
        <dd>{matter.clientDisplayName}</dd>
        <dt>Engagement</dt>
        <dd>{workspace.engagementDisplayName ?? "Not recorded"}</dd>
        <dt>Opened</dt>
        <dd>{matter.openedAt ?? "Not recorded"}</dd>
        <dt>Status</dt>
        <dd>
          <StatusBadge status={matterStatus[matter.status]} />
        </dd>
      </dl>
      {matter.legacyAliases.length > 0 && (
        <p className="sl-record-meta">
          Legacy references (provenance only): {matter.legacyAliases.join(", ")}
        </p>
      )}
      <h3>Approval and execution states</h3>
      {workspace.executionStates.length === 0 ? (
        <p>No approval or execution states are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {workspace.executionStates.map((state) => (
            <li
              key={`${state.targetType}-${state.targetId}-${state.stateKind}`}
              id={`state-${state.targetId}-${state.stateKind}`}
            >
              <span>
                {targetTypeLabel(state.targetType)}{" "}
                {stateKindLabel(state.stateKind)}
              </span>
              <StatusBadge
                status={statusOrFallback(executionStateValue, state.stateValue)}
              />
            </li>
          ))}
        </ul>
      )}
      <h3>Provenance</h3>
      <dl className="sl-detail-list">
        <dt>Source snapshot</dt>
        <dd>{provenance.sourceSnapshot}</dd>
        <dt>Freshness</dt>
        <dd>
          <StatusBadge
            status={
              provenance.stale
                ? snapshotFreshness.stale
                : snapshotFreshness.current
            }
          />
        </dd>
        <dt>Adapter version</dt>
        <dd>{provenance.adapterVersion}</dd>
        <dt>Observed at</dt>
        <dd>{provenance.observedAt}</dd>
      </dl>
      {provenance.stale && (
        <p role="alert">
          The recorded source snapshot is stale: the current source snapshot is{" "}
          {provenance.currentSourceSnapshot ?? "unknown"} while this workspace
          was built from {provenance.sourceSnapshot}.
        </p>
      )}
      <RecordGaps gaps={workspace.gaps} />
    </section>
  );
}

function RecordGaps(props: { gaps: readonly WorkspaceGap[] }) {
  if (props.gaps.length === 0) {
    return null;
  }
  return (
    <>
      <h3>Record gaps</h3>
      <ul className="sl-record-list">
        {props.gaps.map((gap) => (
          <li key={gap.gapId} id={`gap-${gap.gapId}`}>
            <span>{gap.description}</span>
            <RecordLink anchor={`gap-${gap.gapId}`} label={`gap ${gap.kind}`} />
          </li>
        ))}
      </ul>
    </>
  );
}

function PartiesSection(props: { workspace: MatterWorkspace }) {
  const { parties } = props.workspace;
  return (
    <section id="parties" aria-label="Parties" tabIndex={-1}>
      <h2>Parties</h2>
      {parties.length === 0 ? (
        <p>
          No verified parties are recorded for this matter. Party-related fact
          assertions remain visible under Facts and tensions.
        </p>
      ) : (
        <ul className="sl-record-list">
          {parties.map((party) => (
            <li key={party.partyId} id={`party-${party.partyId}`}>
              <span>{party.displayName}</span>
              <span className="sl-record-meta">
                {party.partyKind}
                {party.roles.length > 0
                  ? `; roles: ${party.roles.join(", ")}`
                  : ""}
              </span>
              <StatusBadge
                status={statusOrFallback(verificationStatus, party.status)}
              />
              <RecordLink
                anchor={`party-${party.partyId}`}
                label={`party ${party.displayName}`}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function TimelineEventItem(props: { event: WorkspaceTimelineEvent }) {
  const { event } = props;
  return (
    <li id={`event-${event.eventId}`}>
      <span>{event.description}</span>
      <span className="sl-record-meta">
        {event.eventType}; occurred {event.occurredAt ?? "not recorded"};
        observed {event.observedAt}
      </span>
      <StatusBadge status={statusOrFallback(matterEventStatus, event.status)} />
      {event.legacyAliases.length > 0 && (
        <span className="sl-record-meta">
          Legacy reference: {event.legacyAliases.join(", ")}
        </span>
      )}
      {event.sourcePath !== null && (
        <span className="sl-record-meta">Source: {event.sourcePath}</span>
      )}
      <RecordLink
        anchor={`event-${event.eventId}`}
        label={`matter event ${event.description}`}
      />
    </li>
  );
}

function TimelineSection(props: { workspace: MatterWorkspace }) {
  const { timeline } = props.workspace;
  return (
    <section id="timeline" aria-label="Timeline" tabIndex={-1}>
      <h2>Timeline</h2>
      {timeline.length === 0 ? (
        <p>No matter events are recorded for this matter.</p>
      ) : (
        <ol className="sl-record-list">
          {timeline.map((event) => (
            <TimelineEventItem key={event.eventId} event={event} />
          ))}
        </ol>
      )}
    </section>
  );
}

function FactListItem(props: { fact: WorkspaceFactAssertion }) {
  const { fact } = props;
  return (
    <li id={`fact-${fact.factAssertionId}`}>
      <span>
        {fact.predicate}: {factValueText(fact.assertedValue)}
      </span>
      <StatusBadge
        status={statusOrFallback(factReviewStatus, fact.reviewStatus)}
      />
      <span className="sl-record-meta">
        {fact.sourceMissing
          ? `Locator: ${fact.sourceLocator}`
          : `Source: ${fact.sourcePath ?? "not recorded"}; locator ${fact.sourceLocator}`}
      </span>
      {fact.sourceMissing && <StatusBadge status={missingSource} />}
      {fact.tensionGroupKey !== null && (
        <a
          className="sl-record-link"
          href={`#tension-${fact.tensionGroupKey}`}
          aria-label={`Deep link to tension group ${fact.tensionGroupKey}`}
        >
          Tension: {fact.tensionGroupKey}
        </a>
      )}
      <RecordLink
        anchor={`fact-${fact.factAssertionId}`}
        label={`fact assertion ${fact.predicate}`}
      />
    </li>
  );
}

function FactsAndTensionsSection(props: { workspace: MatterWorkspace }) {
  const { facts, tensions } = props.workspace;
  const factsById = new Map(facts.map((fact) => [fact.factAssertionId, fact]));
  return (
    <section
      id="facts-and-tensions"
      aria-label="Facts and tensions"
      tabIndex={-1}
    >
      <h2>Facts and tensions</h2>
      <h3>Tension groups</h3>
      {tensions.length === 0 ? (
        <p>No tension groups are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {tensions.map((tension) => (
            <li key={tension.tensionKey} id={`tension-${tension.tensionKey}`}>
              <span>Tension: {tension.tensionKey}</span>
              <StatusBadge
                status={statusOrFallback(tensionStatus, tension.status)}
              />
              {tension.reviewRequired && (
                <span className="sl-record-meta">Review required</span>
              )}
              <RecordLink
                anchor={`tension-${tension.tensionKey}`}
                label={`tension group ${tension.tensionKey}`}
              />
              <ul>
                {tension.assertionIds.map((assertionId) => {
                  const fact = factsById.get(assertionId);
                  if (fact === undefined) {
                    return (
                      <li key={assertionId}>
                        <span>
                          Assertion {assertionId} is not present in this
                          workspace.
                        </span>
                        <StatusBadge status={missingSource} />
                      </li>
                    );
                  }
                  return (
                    <li key={assertionId}>
                      <a href={`#fact-${fact.factAssertionId}`}>
                        {fact.predicate}: {factValueText(fact.assertedValue)}
                      </a>
                      <StatusBadge
                        status={statusOrFallback(
                          factReviewStatus,
                          fact.reviewStatus,
                        )}
                      />
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      )}
      <h3>Fact assertions</h3>
      {facts.length === 0 ? (
        <p>No fact assertions are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {facts.map((fact) => (
            <FactListItem key={fact.factAssertionId} fact={fact} />
          ))}
        </ul>
      )}
    </section>
  );
}

function EvidenceSection(props: { workspace: MatterWorkspace }) {
  const { evidence } = props.workspace;
  return (
    <section id="evidence" aria-label="Evidence" tabIndex={-1}>
      <h2>Evidence</h2>
      {evidence.length === 0 ? (
        <p>No evidence items are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {evidence.map((item) => (
            <li
              key={item.evidenceItemId}
              id={`evidence-${item.evidenceItemId}`}
            >
              <span>{item.title}</span>
              <span className="sl-record-meta">{item.mediaType}</span>
              <code className="sl-hash">{item.contentSha256}</code>
              <StatusBadge
                status={statusOrFallback(evidenceItemStatus, item.status)}
              />
              <SourceNote
                sourcePath={item.sourcePath}
                missing={item.sourceMissing}
              />
              <RecordLink
                anchor={`evidence-${item.evidenceItemId}`}
                label={`evidence item ${item.title}`}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CommunicationsSection(props: { workspace: MatterWorkspace }) {
  const { communications } = props.workspace;
  return (
    <section id="communications" aria-label="Communications" tabIndex={-1}>
      <h2>Communications</h2>
      {communications.length === 0 ? (
        <p>No communications are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {communications.map((entry) => (
            <li
              key={entry.communicationId}
              id={`communication-${entry.communicationId}`}
            >
              <span>{entry.summary}</span>
              <span className="sl-record-meta">
                {entry.channel}; {entry.occurredAt ?? "date not recorded"}
              </span>
              <StatusBadge
                status={statusOrFallback(communicationStatus, entry.status)}
              />
              <SourceNote
                sourcePath={entry.sourcePath}
                missing={entry.sourceMissing}
              />
              <RecordLink
                anchor={`communication-${entry.communicationId}`}
                label={`communication ${entry.summary}`}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AuditSection(props: { workspace: MatterWorkspace }) {
  const { audit, provenance, versionLineage } = props.workspace;
  return (
    <section id="audit" aria-label="Audit" tabIndex={-1}>
      <h2>Audit</h2>
      <h3>Audit entries</h3>
      {audit.length === 0 ? (
        <p>No audit entries are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {audit.map((entry) => (
            <li key={entry.auditId} id={`audit-${entry.auditId}`}>
              <span>{entry.action}</span>
              <span className="sl-record-meta">
                {entry.actor}; {entry.occurredAt}; outcome {entry.outcome}
              </span>
              {entry.detail !== null && (
                <span className="sl-record-meta">{entry.detail}</span>
              )}
              <RecordLink
                anchor={`audit-${entry.auditId}`}
                label={`audit entry ${entry.action}`}
              />
            </li>
          ))}
        </ul>
      )}
      <h3>Version lineage</h3>
      {versionLineage.length === 0 ? (
        <p>No work product versions are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {versionLineage.map((version) => (
            <li
              key={version.packetVersion}
              id={`version-${version.packetVersion}`}
            >
              <span>Packet version {version.packetVersion}</span>
              <StatusBadge
                status={
                  version.currentReviewBaseline
                    ? {
                        label: "Current review baseline",
                        glyph: "✓",
                        tone: "positive",
                      }
                    : { label: "Historical", glyph: "←", tone: "neutral" }
                }
              />
              <span className="sl-record-meta">{version.sourcePath}</span>
              <code className="sl-hash">{version.sourceSha256}</code>
              <RecordLink
                anchor={`version-${version.packetVersion}`}
                label={`packet version ${version.packetVersion}`}
              />
            </li>
          ))}
        </ul>
      )}
      <h3>Source files</h3>
      {provenance.sourceFiles.length === 0 ? (
        <p>No source files are recorded for this matter.</p>
      ) : (
        <ul className="sl-record-list">
          {provenance.sourceFiles.map((file) => (
            <li key={file.relativePath}>
              <span>{file.relativePath}</span>
              <code className="sl-hash">{file.contentSha256}</code>
              <span className="sl-record-meta">observed {file.observedAt}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PendingSection(props: { sectionId: string; label: string }) {
  return (
    <section id={props.sectionId} aria-label={props.label} tabIndex={-1}>
      <h2>{props.label}</h2>
      <p className="sl-record-meta">
        This workspace section is delivered by card{" "}
        {pendingSectionCards[props.sectionId] ?? "to be scheduled"}.
      </p>
    </section>
  );
}

export function MatterWorkspaceView(props: { workspace: MatterWorkspace }) {
  const { workspace } = props;
  return (
    <article aria-labelledby="sl-matter-heading">
      <header>
        <h1 id="sl-matter-heading">{workspace.matter.title}</h1>
        <p className="sl-record-meta">
          Client: {workspace.matter.clientDisplayName}{" "}
          <StatusBadge status={matterStatus[workspace.matter.status]} />
        </p>
      </header>
      <MatterWorkspaceNav />
      <OverviewSection workspace={workspace} />
      <PartiesSection workspace={workspace} />
      <TimelineSection workspace={workspace} />
      <FactsAndTensionsSection workspace={workspace} />
      <EvidenceSection workspace={workspace} />
      <PendingSection sectionId="issues-and-claims" label="Issues and claims" />
      <PendingSection sectionId="authorities" label="Authorities" />
      <CommunicationsSection workspace={workspace} />
      <PendingSection
        sectionId="deadlines-and-tasks"
        label="Deadlines and tasks"
      />
      <PendingSection sectionId="work-products" label="Work products" />
      <PendingSection
        sectionId="actions-and-receipts"
        label="Actions and receipts"
      />
      <AuditSection workspace={workspace} />
    </article>
  );
}
