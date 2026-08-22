/**
 * Corpus research page (SKL-S4-03A).
 *
 * The /corpus research surface: a governed search bar with explicit
 * scope chips, a result list rendering the full retrieval trace
 * (release, projection generation, query template, watermark, rank
 * path), and the exact source-span viewer. An inaccessible source
 * renders a denial state and never leaks content. A corpus row is an
 * unverified research proposal, never controlling Authority.
 *
 * The server enforces capability, tenant scope, and matter membership
 * on every request; this page is presentation only.
 */

import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { useApiClient } from "../api/ApiContext";
import type {
  ClaimLedger,
  ClaimLedgerEntry,
  ClaimSupportRecord,
  CorpusResult,
  CorpusScopeOption,
  CorpusSearchResponse,
  CorpusSpan,
  CorpusTrace,
} from "../api/types";
import { QueryBoundary } from "../components/QueryBoundary";
import { StatusBadge } from "../components/StatusBadge";
import {
  challengeIndependence,
  challengeOutcome,
  claimGateOutcome,
  claimReviewDecision,
  claimStatus,
  spanAccessibility,
  statusOrFallback,
  supportVerificationState,
  supersessionStatus,
} from "../design/tokens";

function CorpusTraceSummary(props: { trace: CorpusTrace }) {
  const { trace } = props;
  return (
    <dl className="sl-detail-list">
      <dt>Scope</dt>
      <dd>
        {trace.scopeKind} / tenant {trace.tenantId}
      </dd>
      <dt>Release</dt>
      <dd>{trace.releaseId}</dd>
      <dt>Projection generation</dt>
      <dd>{trace.projectionGeneration}</dd>
      <dt>Current projection generation</dt>
      <dd>{trace.currentProjectionGeneration}</dd>
      <dt>Backend watermark</dt>
      <dd>{trace.backendWatermark}</dd>
      <dt>Query template</dt>
      <dd>
        {trace.queryTemplateId} ({trace.queryTemplateVersion})
      </dd>
      <dt>Query template hash</dt>
      <dd className="sl-hash">{trace.queryTemplateSha256}</dd>
      <dt>Rank path</dt>
      <dd>{trace.rankPath.join(" then ")}</dd>
      <dt>Source hashes</dt>
      <dd className="sl-hash">{trace.sourceHashes.join(", ")}</dd>
      <dt>Retrieval adapter</dt>
      <dd>{trace.retrievalAdapterVersion}</dd>
    </dl>
  );
}

function ScopeChips(props: { options: readonly CorpusScopeOption[] }) {
  return (
    <ul className="sl-record-list" aria-label="Search scope options">
      {props.options.map((option) => {
        const active = option.state === "active";
        return (
          <li key={option.scope}>
            <span className="sl-record-meta">
              {active ? (
                <strong>
                  {option.scope === "this_matter"
                    ? "This matter"
                    : option.scope}
                </strong>
              ) : (
                option.scope
              )}
            </span>
            {active ? (
              <span aria-label="Selected scope">
                <StatusBadge
                  status={{
                    label: "Active",
                    glyph: "\u2713",
                    tone: "positive",
                  }}
                />
              </span>
            ) : (
              <span>
                <StatusBadge
                  status={{
                    label: "Unavailable",
                    glyph: "\u2715",
                    tone: "neutral",
                  }}
                />
                {option.reason !== null && (
                  <span className="sl-record-meta"> {option.reason}</span>
                )}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function CorpusResultRow(props: { result: CorpusResult }) {
  const { result } = props;
  const spanHref = `#span-${result.sourceId}`;
  return (
    <li id={`result-${result.sourceId}`}>
      <span>
        <a
          className="sl-record-link"
          href={spanHref}
          aria-label={`View exact source span for ${result.citation}`}
        >
          {result.citation}
        </a>
        <span className="sl-record-meta"> {result.title}</span>
      </span>
      <StatusBadge
        status={statusOrFallback(supersessionStatus, result.supersessionStatus)}
      />
      <span className="sl-record-meta">
        Rank {result.rank} / score {result.score} / {result.origin}
      </span>
      <p>{result.snippet}</p>
      <span className="sl-record-meta">
        Source hash <span className="sl-hash">{result.sourceSha256}</span> /{" "}
        {result.sourceLocator} / {result.spanKind} span {result.spanStart} to{" "}
        {result.spanEnd}
        {result.spanPage !== null ? ` / page ${result.spanPage}` : ""}
      </span>
      <span className="sl-record-meta">
        Research proposal, not verified Authority ({result.verificationState}).
      </span>
    </li>
  );
}

export function CorpusSpanView(props: { span: CorpusSpan }) {
  const { span } = props;
  if (span.state === "denied") {
    return (
      <section
        id={`span-${span.sourceId}`}
        aria-label="Source span viewer"
        tabIndex={-1}
      >
        <h3>Source span</h3>
        <StatusBadge status={statusOrFallback(spanAccessibility, "denied")} />
        <p>{span.denialMessage}</p>
        <p className="sl-record-meta">
          Denial reason: {span.denialReason}. No span content is available.
        </p>
      </section>
    );
  }
  return (
    <section
      id={`span-${span.sourceId}`}
      aria-label="Source span viewer"
      tabIndex={-1}
    >
      <h3>Source span</h3>
      <StatusBadge status={statusOrFallback(spanAccessibility, span.state)} />
      <StatusBadge
        status={statusOrFallback(supersessionStatus, span.supersessionStatus)}
      />
      <blockquote>
        <mark>{span.spanText}</mark>
      </blockquote>
      <dl className="sl-detail-list">
        <dt>Citation</dt>
        <dd>{span.citation}</dd>
        <dt>Exact locator</dt>
        <dd>
          {span.sourceLocator} / {span.spanKind} span {span.spanStart} to{" "}
          {span.spanEnd}
          {span.spanPage !== null ? ` / page ${span.spanPage}` : ""}
        </dd>
        <dt>Source hash</dt>
        <dd className="sl-hash">{span.sourceSha256}</dd>
        <dt>Supersession status</dt>
        <dd>{span.supersessionStatus}</dd>
        {span.jurisdiction !== null && (
          <>
            <dt>Jurisdiction</dt>
            <dd>{span.jurisdiction}</dd>
          </>
        )}
      </dl>
    </section>
  );
}

/** Presentational corpus research view; data loading lives in CorpusPage. */
export function CorpusResearchView(props: {
  query: string;
  response: CorpusSearchResponse;
  span: CorpusSpan | null;
  queryDraft?: string;
  onQueryDraftChange?: (value: string) => void;
  onSubmitQuery?: () => void;
}) {
  const { response, span } = props;
  const stale = response.trace.projectionStale;
  return (
    <div className="sl-corpus-research">
      <h1>Corpus research</h1>
      <form
        role="search"
        aria-label="Corpus search"
        onSubmit={(event) => {
          event.preventDefault();
          props.onSubmitQuery?.();
        }}
      >
        <label htmlFor="corpus-search-input">Search the matter corpus</label>
        <input
          id="corpus-search-input"
          name="query"
          type="search"
          value={props.queryDraft ?? props.query}
          onChange={(event) =>
            props.onQueryDraftChange?.(event.currentTarget.value)
          }
        />
        <button className="sl-button" type="submit">
          Search
        </button>
      </form>
      <section aria-label="Search scope">
        <h2>Scope</h2>
        <ScopeChips options={response.scopeOptions} />
      </section>
      <section aria-label="Retrieval trace">
        <h2>Retrieval trace</h2>
        {stale && (
          <p role="alert">
            <StatusBadge
              status={{
                label: "Stale projection generation",
                glyph: "!",
                tone: "caution",
              }}
            />{" "}
            Results were read from projection generation{" "}
            {response.trace.projectionGeneration}; the current generation is{" "}
            {response.trace.currentProjectionGeneration}. Confirm before relying
            on these results.
          </p>
        )}
        <CorpusTraceSummary trace={response.trace} />
      </section>
      <section aria-label="Corpus results">
        <h2>Results</h2>
        {response.results.length === 0 ? (
          <p>No corpus results were recorded for this query.</p>
        ) : (
          <ol className="sl-record-list">
            {response.results.map((result) => (
              <CorpusResultRow key={result.sourceId} result={result} />
            ))}
          </ol>
        )}
      </section>
      <section aria-label="Source span viewer">
        <h2>Source span viewer</h2>
        {span === null ? (
          <p>Select a result citation above to view its exact source span.</p>
        ) : (
          <CorpusSpanView span={span} />
        )}
      </section>
    </div>
  );
}

function SupportPanel(props: {
  id: string;
  title: string;
  records: readonly ClaimSupportRecord[];
  emptyLabel: string;
}) {
  const { id, title, records, emptyLabel } = props;
  return (
    <section id={id} aria-label={title} tabIndex={-1}>
      <h4>{title}</h4>
      {records.length === 0 ? (
        <p className="sl-record-meta">{emptyLabel}</p>
      ) : (
        <ul className="sl-record-list">
          {records.map((record) => (
            <li key={record.supportId}>
              <span className="sl-record-meta">
                {record.sourceLocator} / span {record.spanStart} to{" "}
                {record.spanEnd}
              </span>
              <span className="sl-record-meta">
                Source hash{" "}
                <span className="sl-hash">{record.contentSha256}</span> /
                excerpt <span className="sl-hash">{record.excerptSha256}</span>
              </span>
              <span className="sl-record-meta">
                Recorded by principal {record.recordedByPrincipalId} at{" "}
                {record.recordedAt} under policy revision{" "}
                <span className="sl-hash">{record.policyRevision}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ApplicabilityFactors(props: {
  factors: ClaimLedgerEntry["applicability"];
}) {
  const { factors } = props;
  if (factors.length === 0) {
    return (
      <p className="sl-record-meta">
        No authority applicability factors are recorded for this claim yet.
      </p>
    );
  }
  return (
    <ul className="sl-record-list">
      {factors.map((factor) => (
        <li key={`${factor.checkId}-${factor.subjectId ?? "claim"}`}>
          <StatusBadge
            status={
              factor.outcome === "passed"
                ? {
                    label: "Factor satisfied",
                    glyph: "\u2713",
                    tone: "positive",
                  }
                : {
                    label: "Factor failed",
                    glyph: "\u2715",
                    tone: "critical",
                  }
            }
          />
          <span className="sl-record-meta">
            {" "}
            {factor.checkId}
            {factor.subjectId !== null ? ` (${factor.subjectId})` : ""}
            {factor.reasons.length > 0 ? `: ${factor.reasons.join(", ")}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ChallengeHistory(props: {
  claimId: string;
  challenges: ClaimLedgerEntry["challenges"];
}) {
  const { claimId, challenges } = props;
  if (challenges.length === 0) {
    return (
      <p className="sl-record-meta">
        No blind challenge has been recorded for this claim yet.
      </p>
    );
  }
  return (
    <div className="sl-challenge-list">
      {challenges.map((challenge, index) => {
        const blind =
          challenge.independence === "independent" &&
          !challenge.sawChallengedConclusion;
        return (
          <details key={challenge.challengeId} open>
            <summary>
              Challenge {index + 1}: {challenge.outcome}
            </summary>
            <div className="sl-challenge-detail">
              <StatusBadge
                status={statusOrFallback(
                  challengeIndependence,
                  challenge.independence,
                )}
              />
              <StatusBadge
                status={statusOrFallback(challengeOutcome, challenge.outcome)}
              />
              <dl className="sl-detail-list">
                <dt>Challenge record</dt>
                <dd>{challenge.challengeId}</dd>
                <dt>Challenger</dt>
                <dd>
                  {challenge.challengerProvider}/{challenge.challengerModelName}
                  /{challenge.challengerModelRevision}
                </dd>
                <dt>Issued</dt>
                <dd>{challenge.issuedAt}</dd>
                <dt>Independence check</dt>
                <dd>{challenge.independence}</dd>
                <dt>Conclusion exposure</dt>
                <dd>
                  {challenge.sawChallengedConclusion
                    ? "Challenger saw the challenged conclusion"
                    : "Challenger did not see the challenged conclusion"}
                </dd>
                <dt>Challenge outcome</dt>
                <dd>{challenge.outcome}</dd>
              </dl>
              {!blind && (
                <p role="alert">
                  This record does not satisfy a blind, independent challenge.
                  It remains visible as a review defect.
                </p>
              )}
              {challenge.defects.length === 0 ? (
                <p className="sl-record-meta">
                  No challenge defect is recorded.
                </p>
              ) : (
                <ul className="sl-record-list">
                  {challenge.defects.map((defect) => (
                    <li
                      key={`${challenge.challengeId}-${defect.defectKind}`}
                      id={`${claimId}-defect-${challenge.challengeId}-${defect.defectKind}`}
                    >
                      <span className="sl-record-meta">
                        Defect {defect.defectKind}:{" "}
                      </span>
                      {defect.description}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function StateTransitionHistory(props: {
  transitions: ClaimLedgerEntry["stateTransitions"];
}) {
  const { transitions } = props;
  if (transitions.length === 0) {
    return (
      <p className="sl-record-meta">No state transitions are recorded yet.</p>
    );
  }
  return (
    <ol className="sl-record-list">
      {transitions.map((transition) => (
        <li key={`${transition.toStatus}-${transition.version}`}>
          <span className="sl-record-meta">
            {transition.fromStatus === null
              ? "Initially proposed"
              : `${transition.fromStatus} to ${transition.toStatus}`}{" "}
            at {transition.at} (version {transition.version})
          </span>
        </li>
      ))}
    </ol>
  );
}

function ReviewerHistory(props: {
  reviews: ClaimLedgerEntry["reviewHistory"];
}) {
  const { reviews } = props;
  if (reviews.length === 0) {
    return (
      <p className="sl-record-meta">
        No human review decision is recorded for this claim yet.
      </p>
    );
  }
  return (
    <ol className="sl-record-list">
      {reviews.map((review) => (
        <li key={review.reviewId}>
          <StatusBadge
            status={statusOrFallback(claimReviewDecision, review.decision)}
          />
          <span className="sl-record-meta">
            {" "}
            Reviewer principal {review.reviewerPrincipalId} at{" "}
            {review.reviewedAt}
            {" / "}claim version {review.claimVersion} / policy revision{" "}
            <span className="sl-hash">{review.policyRevision}</span>
          </span>
          <p>{review.note}</p>
        </li>
      ))}
    </ol>
  );
}

function ClaimGateSummary(props: { gate: ClaimLedgerEntry["gate"] }) {
  const { gate } = props;
  if (gate === null) {
    return (
      <p className="sl-record-meta">
        No claim gate evaluation is recorded for this claim yet.
      </p>
    );
  }
  return (
    <div>
      <StatusBadge status={statusOrFallback(claimGateOutcome, gate.outcome)} />
      <span className="sl-record-meta">
        {" "}
        Gate {gate.gate} evaluated at {gate.evaluatedAt}.
      </span>
      {gate.failedChecks.map((check) => (
        <p key={`${check.checkId}-${check.subjectId ?? "claim"}`}>
          <span className="sl-record-meta">
            Failed check {check.checkId}
            {check.subjectId !== null ? ` (${check.subjectId})` : ""}:{" "}
            {check.reasons.join(", ")}
          </span>
        </p>
      ))}
    </div>
  );
}

const claimReviewSteps = [
  ["summary", "Claim and gate"],
  ["support", "Support"],
  ["counter-support", "Counter-support"],
  ["applicability", "Authority applicability"],
  ["challenges", "Blind challenges"],
  ["record-gaps", "Record gaps"],
  ["reviewer-history", "Reviewer history"],
  ["state-transitions", "State transitions"],
] as const;

/** Resolve arrow, Home, and End keys for the claim review navigation. */
export function reviewStepIndexForKey(
  key: string,
  currentIndex: number,
  stepCount: number,
): number | null {
  if (stepCount <= 0) {
    return null;
  }
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return currentIndex < 0 ? 0 : (currentIndex + 1) % stepCount;
    case "ArrowLeft":
    case "ArrowUp":
      return currentIndex < 0 ? 0 : (currentIndex - 1 + stepCount) % stepCount;
    case "Home":
      return 0;
    case "End":
      return stepCount - 1;
    default:
      return null;
  }
}

function ClaimReviewPath(props: { claimId: string }) {
  const listRef = useRef<HTMLUListElement>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const links = listRef.current?.querySelectorAll<HTMLAnchorElement>(
      "a[data-claim-review-step]",
    );
    if (links === undefined || links.length === 0) {
      return;
    }
    const currentIndex = Array.from(links).findIndex(
      (link) => link === document.activeElement,
    );
    const nextIndex = reviewStepIndexForKey(
      event.key,
      currentIndex,
      links.length,
    );
    if (nextIndex === null) {
      return;
    }
    event.preventDefault();
    links.item(nextIndex).focus();
  };

  return (
    <nav aria-label="Claim keyboard review path" className="sl-review-path">
      <p className="sl-record-meta" id={`${props.claimId}-review-help`}>
        Review every step in order. Use Tab or the arrow keys to move through
        the path, Home or End to jump, and Enter to open a step.
      </p>
      <ul
        ref={listRef}
        onKeyDown={onKeyDown}
        aria-describedby={`${props.claimId}-review-help`}
      >
        {claimReviewSteps.map(([key, label], index) => (
          <li key={key}>
            <a href={`#claim-${props.claimId}-${key}`} data-claim-review-step>
              {index + 1}. {label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

interface ClaimRecordGap {
  key: string;
  description: string;
}

/** Derive visible review gaps without changing or harmonizing ledger data. */
export function claimRecordGaps(
  entry: ClaimLedgerEntry,
): readonly ClaimRecordGap[] {
  const gaps: ClaimRecordGap[] = [];
  if (entry.support.length === 0) {
    gaps.push({
      key: "support_missing",
      description: "No exact supporting source span is recorded.",
    });
  }
  if (entry.applicability.length === 0) {
    gaps.push({
      key: "applicability_missing",
      description: "No Authority applicability evaluation is recorded.",
    });
  }
  if (entry.challenges.length === 0) {
    gaps.push({
      key: "blind_challenge_missing",
      description: "No blind challenge record is available.",
    });
  }
  if (entry.reviewHistory.length === 0) {
    gaps.push({
      key: "human_review_missing",
      description: "No attributable human reviewer decision is recorded.",
    });
  }
  if (entry.gate === null) {
    gaps.push({
      key: "claim_gate_missing",
      description: "No deterministic claim gate evaluation is recorded.",
    });
  }
  if (entry.stateTransitions.length === 0) {
    gaps.push({
      key: "state_history_missing",
      description: "No claim state transition history is recorded.",
    });
  }
  return gaps;
}

function ReviewSection(props: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <section id={props.id} aria-label={props.label} tabIndex={-1}>
      {props.children}
    </section>
  );
}

function ClaimRecordGaps(props: { entry: ClaimLedgerEntry }) {
  const gaps = claimRecordGaps(props.entry);
  if (gaps.length === 0) {
    return (
      <p role="status">
        No missing review records were detected for this claim version.
      </p>
    );
  }
  return (
    <div role="alert">
      <p>
        This claim has {gaps.length} record {gaps.length === 1 ? "gap" : "gaps"}
        . Missing records remain unresolved and block a complete review.
      </p>
      <ul className="sl-record-list">
        {gaps.map((gap) => (
          <li key={gap.key} id={`claim-${props.entry.claimId}-gap-${gap.key}`}>
            <strong>{gap.key}</strong>
            <span>{gap.description}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ClaimLedgerEntryView(props: { entry: ClaimLedgerEntry }) {
  const { entry } = props;
  const hasTraceableSource =
    entry.support.length > 0 || entry.counterSupport.length > 0;
  return (
    <li id={`claim-${entry.claimId}`}>
      <article
        className="sl-claim-review"
        aria-labelledby={`claim-${entry.claimId}-heading`}
      >
        <h3 id={`claim-${entry.claimId}-heading`}>
          Claim review {entry.claimId}
        </h3>
        <ClaimReviewPath claimId={entry.claimId} />
        <ReviewSection
          id={`claim-${entry.claimId}-summary`}
          label="Claim and gate"
        >
          <h4>Claim and gate</h4>
          {hasTraceableSource ? (
            <p>
              <strong>{entry.statement}</strong>
            </p>
          ) : (
            <div role="alert">
              <strong>Untraceable claim statement withheld</strong>
              <p>
                No exact support or counter-support source span is recorded, so
                this interface will not render a summary-only claim answer.
              </p>
            </div>
          )}
          <StatusBadge status={statusOrFallback(claimStatus, entry.status)} />
          <StatusBadge
            status={statusOrFallback(
              supportVerificationState,
              entry.supportVerificationState,
            )}
          />
          <span className="sl-record-meta">
            Claim {entry.claimId} / version {entry.version} / updated{" "}
            {entry.updatedAt} / policy revision{" "}
            <span className="sl-hash">{entry.policyRevision}</span>
          </span>
          <ClaimGateSummary gate={entry.gate} />
        </ReviewSection>
        <SupportPanel
          id={`claim-${entry.claimId}-support`}
          title="Support"
          records={entry.support}
          emptyLabel="No supporting source span is recorded for this claim."
        />
        <SupportPanel
          id={`claim-${entry.claimId}-counter-support`}
          title="Counter-support"
          records={entry.counterSupport}
          emptyLabel="No counter-support source span is recorded for this claim."
        />
        <section
          id={`claim-${entry.claimId}-applicability`}
          aria-label="Authority applicability factors"
          tabIndex={-1}
        >
          <h4>Authority applicability factors</h4>
          <ApplicabilityFactors factors={entry.applicability} />
        </section>
        <section
          id={`claim-${entry.claimId}-challenges`}
          aria-label="Blind challenge workflow"
          tabIndex={-1}
        >
          <h4>Blind challenge workflow</h4>
          <p className="sl-record-meta">
            Challenge records are read-only. Model findings cannot approve the
            claim or alter workflow state.
          </p>
          <ChallengeHistory
            claimId={entry.claimId}
            challenges={entry.challenges}
          />
        </section>
        <section
          id={`claim-${entry.claimId}-record-gaps`}
          aria-label="Record gaps"
          tabIndex={-1}
        >
          <h4>Record gaps</h4>
          <ClaimRecordGaps entry={entry} />
        </section>
        <section
          id={`claim-${entry.claimId}-reviewer-history`}
          aria-label="Reviewer history"
          tabIndex={-1}
        >
          <h4>Reviewer history</h4>
          <ReviewerHistory reviews={entry.reviewHistory} />
        </section>
        <section
          id={`claim-${entry.claimId}-state-transitions`}
          aria-label="Claim state transitions"
          tabIndex={-1}
        >
          <h4>Claim state transitions</h4>
          <StateTransitionHistory transitions={entry.stateTransitions} />
        </section>
      </article>
    </li>
  );
}

/** Presentational claim ledger view; data loading lives in CorpusPage. */
export function ClaimLedgerView(props: { ledger: ClaimLedger }) {
  const { ledger } = props;
  return (
    <div className="sl-claim-ledger">
      <section aria-label="Claim ledger for this matter">
        <h2>Claim ledger for this matter</h2>
        <p className="sl-record-meta">
          Every material claim below exposes its supporting and contrary source
          spans, the deterministic authority applicability factors behind its
          qualification, the blind challenges that reviewed it, and its state
          history. Similarity alone never qualifies authority, and a failed gate
          cannot be waived by model output.
        </p>
        {ledger.claims.length === 0 ? (
          <p role="status">
            No claim has been recorded for this matter yet. There is no claim
            answer to review.
          </p>
        ) : (
          <ol className="sl-record-list">
            {ledger.claims.map((entry) => (
              <ClaimLedgerEntryView key={entry.claimId} entry={entry} />
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

/** Selects the span for the highest-ranked result when none is chosen. */
function defaultSpanSourceId(response: CorpusSearchResponse): string | null {
  if (response.results.length === 0) {
    return null;
  }
  const ranked = [...response.results].sort((a, b) => a.rank - b.rank);
  return ranked[0]?.sourceId ?? null;
}

export function CorpusPage(props: { matterId: string | null }) {
  const api = useApiClient();
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const ledgerQuery = useQuery({
    queryKey: ["claims", props.matterId],
    queryFn: () => api.getClaimLedger(props.matterId ?? ""),
    enabled: props.matterId !== null,
  });
  const searchQuery = useQuery({
    queryKey: ["corpus", props.matterId, submittedQuery],
    queryFn: () => api.searchCorpus(props.matterId ?? "", submittedQuery),
    enabled: props.matterId !== null && submittedQuery.length > 0,
  });
  const activeSourceId =
    searchQuery.status === "success" && searchQuery.data
      ? defaultSpanSourceId(searchQuery.data.data)
      : null;
  const spanQuery = useQuery({
    queryKey: ["corpus", props.matterId, "span", activeSourceId],
    queryFn: () =>
      api.getCorpusSpan(props.matterId ?? "", activeSourceId ?? ""),
    enabled: props.matterId !== null && activeSourceId !== null,
  });

  if (props.matterId === null) {
    return (
      <div className="sl-corpus-research">
        <h1>Corpus research</h1>
        <p>
          Corpus research is matter-scoped. Select a matter to search its
          corpus.
        </p>
        <Link to="/matters">Go to matters</Link>
      </div>
    );
  }

  return (
    <div className="sl-corpus-page">
      <QueryBoundary
        query={ledgerQuery}
        loadingLabel="Loading the claim ledger"
      >
        {(result) => <ClaimLedgerView ledger={result.data} />}
      </QueryBoundary>
      <QueryBoundary
        query={searchQuery}
        loadingLabel="Searching the matter corpus"
      >
        {(result) => (
          <CorpusResearchView
            query={submittedQuery}
            response={result.data}
            span={
              spanQuery.status === "success"
                ? (spanQuery.data?.data ?? null)
                : null
            }
            queryDraft={query}
            onQueryDraftChange={setQuery}
            onSubmitQuery={() => setSubmittedQuery(query)}
          />
        )}
      </QueryBoundary>
    </div>
  );
}
