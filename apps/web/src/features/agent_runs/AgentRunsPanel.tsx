import type {
  AgentRunRecord,
  AnalysisRequestCommand,
  HumanDispositionDecision,
  RecommendationRecord,
} from "./contracts";

export interface AgentRunsPanelProps {
  run: AgentRunRecord | null;
  onRequestChallenge?: (recommendation: RecommendationRecord) => void;
  onDisposition?: (
    recommendation: RecommendationRecord,
    decision: HumanDispositionDecision,
  ) => void;
}

export interface AgentRunRequestComposerProps {
  command: AnalysisRequestCommand;
  disabled?: boolean;
  onStart: (command: AnalysisRequestCommand) => void;
}

export function AgentRunRequestComposer(props: AgentRunRequestComposerProps) {
  const { command } = props;
  return (
    <section className="sl-panel" aria-labelledby="agent-run-request-heading">
      <h3 id="agent-run-request-heading">New governed analysis</h3>
      <p>
        This request is limited to public-synthetic material. The result is a
        typed proposal and cannot change Matter workflow state.
      </p>
      <dl>
        <dt>Analysis</dt>
        <dd>{command.analysisKind.replaceAll("_", " ")}</dd>
        <dt>Purpose</dt>
        <dd>{command.purpose}</dd>
        <dt>Logical route</dt>
        <dd>{command.requestedLogicalRouteId}</dd>
        <dt>Matter snapshot</dt>
        <dd>
          <Hash>{command.snapshots.matterSnapshotSha256}</Hash>
        </dd>
        <dt>Corpus release</dt>
        <dd>
          {command.snapshots.corpusReleaseId}{" "}
          <Hash>{command.snapshots.corpusSnapshotSha256}</Hash>
        </dd>
        <dt>Authority snapshot</dt>
        <dd>
          <Hash>{command.snapshots.authoritySnapshotSha256}</Hash>
        </dd>
        <dt>Policy snapshot</dt>
        <dd>
          <Hash>{command.snapshots.policySnapshotSha256}</Hash>
        </dd>
      </dl>
      <button
        type="button"
        disabled={props.disabled}
        onClick={() => props.onStart(command)}
      >
        Start governed analysis
      </button>
    </section>
  );
}

function Hash(props: { children: string }) {
  return <code className="sl-hash">{props.children}</code>;
}

function EvidenceLanes(props: { recommendation: RecommendationRecord }) {
  return (
    <ul className="sl-record-list" aria-label="Recommendation source lanes">
      {props.recommendation.evidence.map((item) => (
        <li key={`${item.sourceRole}-${item.sourceId}-${item.exactLocator}`}>
          <strong>{item.sourceRole.replaceAll("_", " ")}</strong>:{" "}
          {item.sourceId}
          <span className="sl-record-meta">
            {item.verificationState.replaceAll("_", " ")}; version{" "}
            {item.sourceVersion}; locator {item.exactLocator}
          </span>
          <Hash>{item.sourceSha256}</Hash>
        </li>
      ))}
    </ul>
  );
}

function RecommendationCard(
  props: AgentRunsPanelProps & {
    recommendation: RecommendationRecord;
  },
) {
  const { recommendation, run } = props;
  if (run === null) return null;
  const challenge = [...run.challenges]
    .reverse()
    .find(
      (item) =>
        item.recommendationId === recommendation.recommendationId &&
        item.recommendationVersion === recommendation.version,
    );
  const disposition = [...run.dispositions]
    .reverse()
    .find(
      (item) =>
        item.recommendationId === recommendation.recommendationId &&
        item.recommendationVersion === recommendation.version,
    );
  const challengePassed = challenge?.outcome === "no_defect";
  return (
    <article
      className="sl-agent-recommendation"
      data-review-state={recommendation.reviewState}
      id={`agent-recommendation-${recommendation.recommendationId}`}
    >
      <header>
        <h4>{recommendation.proposedOutput.replaceAll("_", " ")} proposal</h4>
        <span className="sl-record-meta">
          {recommendation.targetKind}; {recommendation.proceedingPhase}; v
          {recommendation.version}; {recommendation.urgency} urgency
        </span>
      </header>
      <p>{recommendation.reason}</p>
      <p>
        Score <strong>{recommendation.scoreTotal}/100</strong>; confidence{" "}
        {(recommendation.confidenceBasisPoints / 100).toFixed(2)}%
      </p>
      <table>
        <caption>Scoring policy dimensions</caption>
        <thead>
          <tr>
            <th scope="col">Dimension</th>
            <th scope="col">Score</th>
            <th scope="col">Rationale digest</th>
          </tr>
        </thead>
        <tbody>
          {recommendation.scoreDimensions.map((dimension) => (
            <tr key={dimension.dimension}>
              <th scope="row">{dimension.dimension.replaceAll("_", " ")}</th>
              <td>{dimension.value}</td>
              <td>
                <Hash>{dimension.rationaleSha256}</Hash>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h5>Traceable source roles</h5>
      <EvidenceLanes recommendation={recommendation} />
      <dl>
        <dt>Prerequisites</dt>
        <dd>{recommendation.prerequisites.join("; ") || "None recorded"}</dd>
        <dt>Prohibited sequencing</dt>
        <dd>
          {recommendation.prohibitedSequencing.join("; ") || "None recorded"}
        </dd>
        <dt>Required capability</dt>
        <dd>{recommendation.requiredCapability}</dd>
        <dt>Downstream human gate</dt>
        <dd>{recommendation.downstreamHumanGate}</dd>
      </dl>
      <section aria-label="Blind challenge">
        <h5>Blind challenge</h5>
        {challenge === undefined ? (
          <p role="status">Independent challenge has not been recorded.</p>
        ) : (
          <>
            <p data-challenge-outcome={challenge.outcome}>
              {challenge.outcome.replaceAll("_", " ")}; challenger{" "}
              {challenge.challengerSpecId} v{challenge.challengerSpecVersion};
              conclusion hidden:{" "}
              {challenge.sawChallengedConclusion ? "no" : "yes"}
            </p>
            <p className="sl-record-meta">
              Blind input <Hash>{challenge.blindInputSha256}</Hash>; independent
              output <Hash>{challenge.independentOutputSha256}</Hash>
            </p>
            <p className="sl-record-meta">
              Initiated by {challenge.authorization.principalId}; capability{" "}
              {challenge.authorization.capability}; decision{" "}
              {challenge.authorization.decisionId}; revocation snapshot{" "}
              {challenge.authorization.revocationRevision}
            </p>
            {challenge.defects.length > 0 && (
              <ul className="sl-record-list" role="alert">
                {challenge.defects.map((defect) => (
                  <li key={`${defect.defectKind}-${defect.evidenceSha256}`}>
                    <strong>{defect.defectKind.replaceAll("_", " ")}</strong>:{" "}
                    {defect.description} <Hash>{defect.evidenceSha256}</Hash>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <button
          type="button"
          onClick={() => props.onRequestChallenge?.(recommendation)}
        >
          Request independent blind challenge
        </button>
      </section>
      <section aria-label="Human disposition">
        <h5>Human disposition</h5>
        {disposition === undefined ? (
          <p role="status">No attributable human disposition is recorded.</p>
        ) : (
          <p>
            {disposition.decision.replaceAll("_", " ")} by{" "}
            {disposition.reviewerPrincipalId}: {disposition.rationale}
          </p>
        )}
        {disposition !== undefined && (
          <p className="sl-record-meta">
            Review capability {disposition.authorization.capability}; decision{" "}
            {disposition.authorization.decisionId}; verifier{" "}
            {disposition.authorization.verifierPolicyVersion}
          </p>
        )}
        <div className="sl-action-row">
          <button
            type="button"
            disabled={!challengePassed}
            onClick={() =>
              props.onDisposition?.(recommendation, "accept_as_proposed_task")
            }
          >
            Accept as proposed Task
          </button>
          <button
            type="button"
            disabled={!challengePassed}
            onClick={() =>
              props.onDisposition?.(
                recommendation,
                "request_work_product_proposal",
              )
            }
          >
            Request Work Product proposal
          </button>
          <button
            type="button"
            onClick={() =>
              props.onDisposition?.(recommendation, "changes_requested")
            }
          >
            Request changes
          </button>
          <button
            type="button"
            onClick={() => props.onDisposition?.(recommendation, "reject")}
          >
            Reject proposal
          </button>
        </div>
        <p className="sl-record-meta">
          Disposition records review only. It does not create a Task or Work
          Product and cannot dispatch an external action.
        </p>
      </section>
    </article>
  );
}

export function AgentRunsPanel(props: AgentRunsPanelProps) {
  const { run } = props;
  if (run === null) {
    return (
      <section className="sl-panel" aria-labelledby="agent-runs-heading">
        <h3 id="agent-runs-heading">Agent Runs</h3>
        <p role="status">No governed analysis run is selected.</p>
      </section>
    );
  }
  return (
    <section className="sl-panel" aria-labelledby="agent-runs-heading">
      <header>
        <h3 id="agent-runs-heading">Agent Runs</h3>
        <p>
          Typed public-synthetic proposals with exact route, source, and review
          evidence. Models do not own workflow state.
        </p>
      </header>
      <div role={run.status === "completed" ? "status" : "alert"}>
        Run {run.runId}; version {run.version};{" "}
        {run.status.replaceAll("_", " ")}
      </div>
      <dl>
        <dt>Workflow</dt>
        <dd>
          {run.workflowId} at {run.workflowRevision}
        </dd>
        <dt>Classification</dt>
        <dd>{run.request.classification}; public synthetic only</dd>
        <dt>Agent specification</dt>
        <dd>
          {run.request.agentSpecification.specId} v
          {run.request.agentSpecification.specVersion}{" "}
          <Hash>{run.request.agentSpecification.specSha256}</Hash>
        </dd>
        <dt>Prompt template</dt>
        <dd>
          {run.request.promptTemplateId}{" "}
          <Hash>{run.request.promptTemplateSha256}</Hash>
        </dd>
        <dt>Output schema</dt>
        <dd>
          {run.request.outputSchemaId}{" "}
          <Hash>{run.request.outputSchemaSha256}</Hash>
        </dd>
      </dl>
      {run.route !== null && (
        <section aria-label="Model route evidence">
          <h4>Model route evidence</h4>
          <p>
            Logical route {run.route.logicalRouteId}; backend{" "}
            {run.route.backend}; requested {run.route.requestedModelOrBucket};
            served {run.route.servedModelName} at{" "}
            {run.route.servedModelRevision}
          </p>
          <p className="sl-record-meta">
            Gateway {run.route.gatewayRevision}; transport{" "}
            {run.route.transportProfileRevision}; catalog{" "}
            {run.route.catalogRevision}; route policy{" "}
            {run.route.routePolicyRevision}; latency {run.route.latencyMs} ms
          </p>
        </section>
      )}
      <section aria-label="Attempt and tool evidence">
        <h4>Attempt and tool evidence</h4>
        <ol className="sl-record-list">
          {run.attempts.map((attempt) => (
            <li key={attempt.attemptId}>
              Attempt {attempt.attemptNumber}:{" "}
              {attempt.outcome.replaceAll("_", " ")}
              {attempt.errorCode === null ? "" : `; ${attempt.errorCode}`}
            </li>
          ))}
        </ol>
        {run.toolCalls.length === 0 ? (
          <p>No tool calls were recorded.</p>
        ) : (
          <ol className="sl-record-list">
            {run.toolCalls.map((call) => (
              <li key={call.toolCallId}>
                {call.sequence}. {call.toolId}: {call.outcome}; arguments{" "}
                <Hash>{call.argumentsSha256}</Hash>; result{" "}
                <Hash>{call.resultSha256}</Hash>
              </li>
            ))}
          </ol>
        )}
      </section>
      <section aria-label="Recommendations">
        <h4>Recommendations</h4>
        {run.recommendations.length === 0 ? (
          <p role="status">No typed recommendation was produced.</p>
        ) : (
          run.recommendations.map((recommendation) => (
            <RecommendationCard
              {...props}
              key={recommendation.recommendationId}
              recommendation={recommendation}
            />
          ))
        )}
      </section>
    </section>
  );
}
