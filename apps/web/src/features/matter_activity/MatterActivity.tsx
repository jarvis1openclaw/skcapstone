import type {
  ActivityExportProposal,
  MatterActivityItem,
  MatterActivityState,
} from "./types";

type MatterActivityProps = {
  state: MatterActivityState;
  exportProposal?: ActivityExportProposal | null;
  onLoadMore?: (() => void) | undefined;
  onProposeExport?: (() => void) | undefined;
};

function ExactValue(props: { label: string; value: string }) {
  return (
    <code
      aria-label={props.label}
      tabIndex={0}
      style={{ overflowWrap: "anywhere", userSelect: "all" }}
    >
      {props.value}
    </code>
  );
}

function Trace(props: { item: MatterActivityItem }) {
  const trace = props.item.trace;
  return (
    <details>
      <summary>Audit and provenance</summary>
      <dl>
        <dt>Actor</dt>
        <dd>
          <ExactValue
            label="Actor principal"
            value={props.item.actorPrincipalId}
          />
        </dd>
        <dt>Correlation</dt>
        <dd>
          <ExactValue
            label="Correlation identifier"
            value={trace.correlationId}
          />
        </dd>
        <dt>Run</dt>
        <dd>
          <ExactValue label="Run identifier" value={trace.runId} />
        </dd>
        {trace.workflowReferenceId !== null && (
          <>
            <dt>Workflow reference</dt>
            <dd>{trace.workflowReferenceId}</dd>
          </>
        )}
        {trace.agentRunId !== null && (
          <>
            <dt>Agent Run</dt>
            <dd>{trace.agentRunId}</dd>
          </>
        )}
        {trace.toolCallId !== null && (
          <>
            <dt>Tool call</dt>
            <dd>{trace.toolCallId}</dd>
          </>
        )}
      </dl>
    </details>
  );
}

function ActivityRow(props: { item: MatterActivityItem }) {
  const { item } = props;
  return (
    <li
      data-source-kind={item.source.kind}
      data-chain-status={item.chainStatus}
    >
      <article>
        <header>
          <strong>Event {item.eventSequence}</strong>
          <span> {item.action}</span>
          <span> {item.outcome}</span>
        </header>
        <p>
          Verified Tenant audit event at {item.recordedAt}. Source:{" "}
          {item.source.kind}, version {item.source.sourceVersion}, status{" "}
          {item.source.status}.
        </p>
        <dl>
          <dt>Event hash</dt>
          <dd>
            <ExactValue
              label={`Event ${item.eventSequence} hash`}
              value={item.eventSha256}
            />
          </dd>
          <dt>Source hash</dt>
          <dd>
            <ExactValue
              label={`Event ${item.eventSequence} source hash`}
              value={item.source.sourceSha256}
            />
          </dd>
          {item.source.correctsSourceId !== null && (
            <>
              <dt>Corrects source</dt>
              <dd>{item.source.correctsSourceId}</dd>
            </>
          )}
          {item.source.supersededBySourceId !== null && (
            <>
              <dt>Superseded by source</dt>
              <dd>
                {item.source.supersededBySourceId}, version{" "}
                {item.source.supersededBySourceVersion}
              </dd>
            </>
          )}
        </dl>
        <Trace item={item} />
      </article>
    </li>
  );
}

function ExportProposal(props: { proposal: ActivityExportProposal }) {
  return (
    <aside aria-label="Hashed activity export proposal">
      <h3>{props.proposal.title}</h3>
      <p>
        Work Product proposal only. It has no Approval and requests no dispatch
        or external effect.
      </p>
      <dl>
        <dt>Selection hash</dt>
        <dd>
          <ExactValue
            label="Activity selection hash"
            value={props.proposal.selectionSha256}
          />
        </dd>
        <dt>Content hash</dt>
        <dd>
          <ExactValue
            label="Activity export content hash"
            value={props.proposal.contentSha256}
          />
        </dd>
        <dt>Dispatch state</dt>
        <dd>{props.proposal.dispatchState}</dd>
      </dl>
    </aside>
  );
}

export function MatterActivity(props: MatterActivityProps) {
  if (props.state.status === "loading") {
    return (
      <p role="status" aria-busy="true">
        Loading authorized Matter activity
      </p>
    );
  }
  if (props.state.status === "unavailable") {
    return (
      <section aria-label="Matter activity and provenance log">
        <p role="alert">Matter activity is temporarily unavailable.</p>
        <p>Correlation: {props.state.correlationId}</p>
        <button type="button" disabled>
          Export proposal unavailable
        </button>
      </section>
    );
  }
  const page = props.state.page;
  return (
    <section aria-label="Matter activity and provenance log">
      <header>
        <h2>Matter activity and provenance log</h2>
        <p>
          Verified Tenant chain {page.watermark.projectedSequence} of{" "}
          {page.watermark.tenantHeadSequence}. Projection lag:{" "}
          {page.watermark.lagEvents} events.
        </p>
        <p>
          Corrections append attributable events. They never rewrite prior
          history.
        </p>
      </header>
      {page.items.length === 0 ? (
        <p>The authorized API returned no Matter activity entries.</p>
      ) : (
        <ol>
          {page.items.map((item) => (
            <ActivityRow key={item.activityId} item={item} />
          ))}
        </ol>
      )}
      <button
        type="button"
        disabled={page.nextCursor === null || props.onLoadMore === undefined}
        onClick={props.onLoadMore}
      >
        Load more activity
      </button>
      <button
        type="button"
        disabled={
          page.items.length === 0 || props.onProposeExport === undefined
        }
        onClick={props.onProposeExport}
      >
        Propose hashed Work Product export
      </button>
      <p>
        No send, file, service, email, calendar, or connector action is
        available here.
      </p>
      {props.exportProposal !== null && props.exportProposal !== undefined && (
        <ExportProposal proposal={props.exportProposal} />
      )}
    </section>
  );
}
