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
  ClaimLedger,
  MatterWorkspace,
  WorkspaceFactAssertion,
  WorkspaceGap,
  WorkspaceTimelineEvent,
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { WorkProductDraftingSection } from "../components/WorkProductDrafting";
import { MatterCockpit, v2CockpitSections } from "./MatterCockpit";
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
  { id: "feature-matrix", label: "Feature matrix" },
] as const;

export function MatterWorkspaceNav() {
  return (
    <nav aria-label="Matter workspace" className="sl-matter-nav">
      <ul>
        {v2CockpitSections.map((section) => (
          <li key={section.id}>
            <a href={`#${section.id}`}>{section.label}</a>
          </li>
        ))}
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

function IssuesAndClaimsSection(props: { ledger: ClaimLedger | null }) {
  return (
    <section
      id="issues-and-claims"
      aria-label="Issues and claims"
      tabIndex={-1}
    >
      <h2>Issues and claims</h2>
      {props.ledger === null ? (
        <p className="sl-record-meta">
          Safely unavailable: the claim ledger did not return an authorized
          answer. No Issue or Claim state was inferred.
        </p>
      ) : props.ledger.claims.length === 0 ? (
        <p>No Claims are recorded for this Matter.</p>
      ) : (
        <ul className="sl-record-list">
          {props.ledger.claims.map((claim) => (
            <li key={claim.claimId} id={`claim-${claim.claimId}`}>
              <strong>{claim.statement}</strong>
              <span className="sl-record-meta">
                Claim status {claim.status}; version {claim.version}; policy{" "}
                {claim.policyRevision}
              </span>
              <span className="sl-record-meta">
                Support {claim.support.length}; counter-support{" "}
                {claim.counterSupport.length}; verification{" "}
                {claim.supportVerificationState}
              </span>
              <span className="sl-record-meta">
                Gate {claim.gate?.outcome ?? "not evaluated"}; human reviews{" "}
                {claim.reviewHistory.length}
              </span>
              <RecordLink
                anchor={`claim-${claim.claimId}`}
                label={`Claim ${claim.statement}`}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AuthoritiesSection(props: { ledger: ClaimLedger | null }) {
  const records =
    props.ledger?.claims.flatMap((claim) =>
      [...claim.support, ...claim.counterSupport].map((support) => ({
        claim,
        support,
      })),
    ) ?? [];
  return (
    <section id="authorities" aria-label="Authorities" tabIndex={-1}>
      <h2>Authorities</h2>
      <p className="sl-record-meta">
        Source-linked support is a research record. It is not silently treated
        as controlling Authority.
      </p>
      {records.length === 0 ? (
        <p>No source-linked Authority support is recorded for this Matter.</p>
      ) : (
        <ul className="sl-record-list">
          {records.map(({ claim, support }) => (
            <li key={support.supportId} id={`authority-${support.supportId}`}>
              <span>
                {support.kind === "counter_support"
                  ? "Counter-support"
                  : "Support"}{" "}
                for {claim.statement}
              </span>
              <span className="sl-record-meta">
                {support.sourceSystem}; version {support.sourceVersion};{" "}
                {support.sourceLocator}
              </span>
              <code className="sl-hash">{support.contentSha256}</code>
              <span className="sl-record-meta">
                Applicability checks {claim.applicability.length}
              </span>
              <RecordLink
                anchor={`authority-${support.supportId}`}
                label={`Authority support ${support.supportId}`}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SafeUnavailableSection(props: {
  sectionId: string;
  label: string;
  reason: string;
}) {
  return (
    <section
      id={props.sectionId}
      aria-label={props.label}
      tabIndex={-1}
      data-feature-state="safely-unavailable"
    >
      <h2>{props.label}</h2>
      <p className="sl-record-meta">
        Safely unavailable in this internal MVP: {props.reason}
      </p>
    </section>
  );
}

export const matterWorkbenchFeatureMatrix = [
  { feature: "Client and Matter navigation", state: "mvp-complete" },
  { feature: "Matter activity and provenance", state: "mvp-complete" },
  {
    feature: "Evidence Items, Fact Assertions, and tensions",
    state: "mvp-complete",
  },
  {
    feature: "Issues, Claims, and source-linked Authority support",
    state: "mvp-complete",
  },
  { feature: "Corpus search and exact source spans", state: "mvp-complete" },
  {
    feature: "Work Products and exact-version Approval state",
    state: "mvp-complete",
  },
  { feature: "Task and Deadline mutation", state: "safely-unavailable" },
  { feature: "External action dispatch", state: "post-mvp" },
] as const;

function FeatureMatrixSection(props: { matterId: string }) {
  return (
    <section id="feature-matrix" aria-label="Feature matrix" tabIndex={-1}>
      <h2>MVP feature matrix</h2>
      <table>
        <caption>
          Internal public-synthetic Matter workbench delivery state
        </caption>
        <thead>
          <tr>
            <th scope="col">Feature</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {matterWorkbenchFeatureMatrix.map((row) => (
            <tr key={row.feature}>
              <th scope="row">{row.feature}</th>
              <td>{row.state}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        <a href={`/corpus?matterId=${encodeURIComponent(props.matterId)}`}>
          Open governed corpus research for this Matter
        </a>
      </p>
      <p className="sl-record-meta">
        Deterministic public-synthetic state resets when the development
        composition restarts.
      </p>
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

export function MatterWorkspaceView(props: {
  workspace: MatterWorkspace;
  claimLedger?: ClaimLedger | null;
}) {
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
      <div className="sl-v2-layout">
        <MatterWorkspaceNav />
        <div className="sl-v2-main-column">
          <MatterCockpit
            workspace={workspace}
            claimLedger={props.claimLedger ?? null}
          />
          <div className="sl-v2-records" aria-label="Authorized Matter records">
            <header className="sl-v2-records-head">
              <p className="sl-v2-eyebrow">Authorized API record</p>
              <h2>Underlying Matter workspace</h2>
              <p>
                The AI-first cockpit does not replace source records. These
                existing read surfaces remain the reconstructable record.
              </p>
            </header>
            <OverviewSection workspace={workspace} />
            <PartiesSection workspace={workspace} />
            <TimelineSection workspace={workspace} />
            <FactsAndTensionsSection workspace={workspace} />
            <EvidenceSection workspace={workspace} />
            <IssuesAndClaimsSection ledger={props.claimLedger ?? null} />
            <AuthoritiesSection ledger={props.claimLedger ?? null} />
            <CommunicationsSection workspace={workspace} />
            <SafeUnavailableSection
              sectionId="deadlines-and-tasks"
              label="Deadlines and tasks"
              reason="no reviewed Task and Deadline mutation contract is mounted in the immutable API composition"
            />
            <WorkProductDraftingSection workProducts={workspace.workProducts} />
            <SafeUnavailableSection
              sectionId="actions-and-receipts"
              label="Actions and receipts"
              reason="external actions remain limited to recorded negative state; dispatch is not authorized"
            />
            <AuditSection workspace={workspace} />
            <FeatureMatrixSection matterId={workspace.matter.matterId} />
          </div>
        </div>
      </div>
    </article>
  );
}
