import type {
  ClaimLedger,
  MatterWorkspace,
  WorkspaceWorkProduct,
} from "../api/types";
import type { ReactNode } from "react";

type CockpitProps = {
  workspace: MatterWorkspace;
  claimLedger: ClaimLedger | null;
};

type SurfaceState = "available" | "proposal" | "safely-unavailable";

export function workProductApprovalState(
  workProduct: WorkspaceWorkProduct,
): "unbound" | "current" | "invalidated" {
  const binding = workProduct.approvalBinding;
  if (binding === null) return "unbound";
  const current = workProduct.currentVersion;
  return binding.versionId === current.versionId &&
    binding.versionNumber === current.versionNumber &&
    binding.contentSha256 === current.contentSha256
    ? "current"
    : "invalidated";
}

function Pill(props: { children: string; tone?: string }) {
  return (
    <span className={`sl-v2-pill sl-v2-pill-${props.tone ?? "neutral"}`}>
      {props.children}
    </span>
  );
}

function Surface(props: {
  id: string;
  title: string;
  eyebrow: string;
  state?: SurfaceState;
  children: ReactNode;
}) {
  const state = props.state ?? "available";
  return (
    <section
      id={props.id}
      className="sl-v2-surface"
      aria-label={props.title}
      tabIndex={-1}
      data-feature-state={state}
    >
      <header className="sl-v2-surface-head">
        <p className="sl-v2-eyebrow">{props.eyebrow}</p>
        <h2>{props.title}</h2>
        {state !== "available" && (
          <Pill tone={state === "proposal" ? "model" : "warn"}>{state}</Pill>
        )}
      </header>
      {props.children}
    </section>
  );
}

const operatingSteps = [
  [
    "Observe",
    "Watch authorized Matter Events, Evidence Items, orders, Deadlines, and Work Product changes.",
  ],
  [
    "Analyze",
    "Rebuild the Issue map, proof coverage, contradictions, Authority state, and record gaps.",
  ],
  [
    "Prepare",
    "Offer inert Tasks, research queries, evidence requests, and draft Work Products.",
  ],
  [
    "Bind or act",
    "Require an attributable human decision and the existing exact-version gate.",
  ],
] as const;

const team = [
  [
    "Intake analyst",
    "Classifies Matter type, Proceeding phase, parties, events, and immediate gaps.",
  ],
  [
    "Proof strategist",
    "Maps Claims, Defenses, Elements, burdens, Fact Assertions, and Evidence Items.",
  ],
  [
    "Course researcher",
    "Routes source-derived instruction by phase and Matter type with exact spans.",
  ],
  [
    "Authority reviewer",
    "Checks official sources, hierarchy, effective date, applicability, and contrary Authority.",
  ],
  [
    "Deadline analyst",
    "Proposes triggers while deterministic calculation and human review remain required.",
  ],
  [
    "Adversarial challenger",
    "Tests prerequisites, proof, admissibility, remedy, procedure, and downside risk.",
  ],
  [
    "Work Product assembler",
    "Uses only reviewed Claims, bracketed unknowns, and verified citations.",
  ],
  [
    "Orchestrator",
    "Owns bounded retries and waits. A model never owns workflow state.",
  ],
] as const;

const failureRows = [
  [
    "Policy unavailable or access denied",
    "Uniform denial with no source oracle",
    "All protected analysis",
  ],
  [
    "Authority missing, stale, or contrary",
    "Show the exact defect beside the proposal",
    "Deadline and filing-ready state",
  ],
  [
    "Fact tension or Evidence gap",
    "Show both assertions and the missing proof",
    "Affected Element and grounded sentence",
  ],
  [
    "Model timeout or schema failure",
    "Typed failure with bounded retry state",
    "Partial output never enters state",
  ],
  [
    "Artifact changed after Approval",
    "Invalidate the Approval and highlight the new hash",
    "Queue and dispatch",
  ],
] as const;

export const v2CockpitSections = [
  { id: "decision", label: "Decision" },
  { id: "ai-operating-model", label: "Operating model" },
  { id: "corpus-map", label: "Corpus map" },
  { id: "ai-cockpit", label: "AI cockpit" },
  { id: "ai-intake", label: "AI intake" },
  { id: "artifact-intake", label: "Artifacts" },
  { id: "element-matrix", label: "Element matrix" },
  { id: "recommendation", label: "Recommendation" },
  { id: "strategy-authority", label: "Strategy and Authority" },
  { id: "agent-team", label: "Agent team" },
  { id: "blind-challenge", label: "Blind challenge" },
  { id: "model-routing", label: "Model routing" },
  { id: "work-product-assembly", label: "Work Product" },
  { id: "deadline-actions", label: "Deadlines and actions" },
  { id: "source-run-evidence", label: "Source and run evidence" },
  { id: "case-log", label: "Case log" },
  { id: "failure-states", label: "Failure states" },
  { id: "delivery-map", label: "Delivery map" },
] as const;

export function MatterCockpit(props: CockpitProps) {
  const { workspace, claimLedger } = props;
  const claims = claimLedger?.claims ?? [];
  const supportedClaims = claims.filter(
    (claim) => claim.support.length > 0,
  ).length;
  const evidenceCount = workspace.evidence.length;
  const unresolvedTensions = workspace.tensions.filter(
    (tension) => tension.reviewRequired,
  ).length;
  const authorityCount = claims.reduce(
    (count, claim) =>
      count + claim.support.length + claim.counterSupport.length,
    0,
  );
  const cockpitSurface = (
    <Surface
      id="ai-cockpit"
      title="AI Matter cockpit"
      eyebrow="Matter scope and pinned context"
    >
      <div className="sl-v2-scope">
        <div>
          <p className="sl-v2-kicker">AI-first public-synthetic workspace</p>
          <h2>{workspace.matter.title}</h2>
          <p>{workspace.matter.summary}</p>
        </div>
        <div className="sl-v2-pills" aria-label="Matter scope status">
          <Pill tone="good">Matter member</Pill>
          <Pill>{workspace.matter.status}</Pill>
          <Pill tone="info">source grounded</Pill>
          <Pill tone="warn">Authority verification incomplete</Pill>
        </div>
      </div>
      <p className="sl-v2-context">
        Snapshot <code>{workspace.provenance.sourceSnapshot}</code> | adapter{" "}
        <code>{workspace.provenance.adapterVersion}</code> | observed{" "}
        {workspace.provenance.observedAt}
      </p>
      <div className="sl-v2-composer" data-feature-state="safely-unavailable">
        <div>
          <p className="sl-v2-eyebrow">Ask the AI case team</p>
          <strong>
            What should we do next to close the highest-impact proof gap?
          </strong>
          <p>
            Analysis execution is safely unavailable until a reviewed Agent
            input and recommendation contract is mounted.
          </p>
        </div>
        <button className="sl-button" type="button" disabled>
          Analyze Matter
        </button>
      </div>
      <div className="sl-v2-ribbon" aria-label="Continuous analysis workflow">
        <Pill tone="good">scope passed</Pill>
        <Pill tone="good">snapshot pinned</Pill>
        <Pill tone="good">Issues mapped</Pill>
        <Pill tone="warn">Authority review</Pill>
        <Pill>challenge unavailable</Pill>
        <Pill>human decision required</Pill>
      </div>
      <div className="sl-v2-metrics">
        <div>
          <span>Claims with support</span>
          <strong>{supportedClaims}</strong>
          <small>raw authorized ledger count</small>
        </div>
        <div>
          <span>Evidence Items</span>
          <strong>{evidenceCount}</strong>
          <small>authorized Matter records</small>
        </div>
        <div>
          <span>Record tensions</span>
          <strong>{unresolvedTensions}</strong>
          <small>review required</small>
        </div>
        <div>
          <span>Source links</span>
          <strong>{authorityCount}</strong>
          <small>support and counter-support</small>
        </div>
      </div>
      <div
        className="sl-v2-unavailable"
        data-feature-state="safely-unavailable"
      >
        <h3>Ranked recommendation queue unavailable</h3>
        <p>
          No typed recommendation or scoring-policy response is mounted. React
          does not calculate proof coverage, rank next steps, or infer urgency
          from adjacent Matter records.
        </p>
      </div>
    </Surface>
  );

  return (
    <div className="sl-v2-cockpit">
      <Surface
        id="decision"
        title="SKLegal as an active AI case team"
        eyebrow="V2 product decision and evidence boundary"
      >
        <p className="sl-v2-lead">
          AI prepares source-grounded typed proposals inside the authorized
          Matter. Humans set the objective and make the decisions that can bind
          strategy, a Work Product, or an external action.
        </p>
        <div className="sl-v2-decision">
          <div>
            <strong>Already coherent</strong>
            <span>
              Matter records, immutable provenance, policy-filtered retrieval,
              Claims, Work Products, Approval, and audit.
            </span>
          </div>
          <div>
            <strong>Product gap</strong>
            <span>
              Recommendation, joined Issue and Authority, challenge, and Agent
              Run HTTP contracts remain unavailable.
            </span>
          </div>
          <div>
            <strong>V2 decision</strong>
            <span>
              Keep the authorized Matter at the center and never infer missing
              workflow state in React.
            </span>
          </div>
        </div>
        <h3>Non-negotiable evidence equation</h3>
        <div className="sl-v2-source-roles" aria-label="Proposal source roles">
          <Pill tone="course">Course instruction</Pill>
          <Pill tone="warn">Current Authority</Pill>
          <Pill tone="info">Matter record</Pill>
          <Pill tone="model">Model inference</Pill>
          <Pill tone="good">Human decision</Pill>
        </div>
        <p className="sl-v2-callout">
          No source role silently impersonates another. Research counts and
          corpus coverage remain in their reviewed planning artifacts until a
          pinned product response exposes them.
        </p>
      </Surface>

      <Surface
        id="ai-operating-model"
        title="AI operating model"
        eyebrow="Automatic analysis, human-controlled effect"
      >
        <div className="sl-v2-decision">
          <div>
            <strong>Architecture decision</strong>
            <span>
              Keep the authorized Matter as the center of the AI-first case
              team.
            </span>
          </div>
          <div>
            <strong>Existing foundation</strong>
            <span>
              Typed records, provenance, policy, Claims, Work Products,
              Approval, and audit.
            </span>
          </div>
          <div>
            <strong>Current boundary</strong>
            <span>
              Models propose. Deterministic services and humans own state and
              legal effect.
            </span>
          </div>
        </div>
        <div className="sl-v2-grid sl-v2-grid-4">
          {operatingSteps.map(([title, detail], index) => (
            <article className="sl-v2-card" key={title}>
              <span className="sl-v2-step">{index + 1}</span>
              <h3>{title}</h3>
              <p>{detail}</p>
            </article>
          ))}
        </div>
        <p className="sl-v2-callout">
          Course instruction + current Authority + Matter record + procedural
          posture + model inference = ranked typed proposal. No lane silently
          impersonates another.
        </p>
      </Surface>

      <Surface
        id="corpus-map"
        title="Private corpus strategy map"
        eyebrow="HammerTime provenance, governed SKLegal retrieval"
        state="proposal"
      >
        <div className="sl-v2-grid sl-v2-grid-3">
          <article className="sl-v2-card sl-v2-lane-course">
            <h3>Foundation</h3>
            <p>
              Elements, burdens, planning, legal research, citations, and court
              rules.
            </p>
          </article>
          <article className="sl-v2-card sl-v2-lane-course">
            <h3>Proceeding phase</h3>
            <p>
              Pleadings, discovery, motions, evidence, trial, judgment, and
              appeal.
            </p>
          </article>
          <article className="sl-v2-card sl-v2-lane-course">
            <h3>Matter type</h3>
            <p>
              Contracts, debt, foreclosure, family, property, criminal, and
              general civil.
            </p>
          </article>
        </div>
        <p className="sl-v2-unavailable">
          No private corpus text is embedded here. Governed retrieval remains
          available only through the existing Matter-scoped corpus route.
        </p>
      </Surface>

      {cockpitSurface}

      <Surface
        id="ai-intake"
        title="AI-guided Matter intake"
        eyebrow="Structure may be proposed, scope must be confirmed"
        state="safely-unavailable"
      >
        <div className="sl-v2-grid sl-v2-grid-2">
          <article className="sl-v2-card">
            <h3>Provide the case record</h3>
            <p>
              Unified artifact intake is not mounted. Existing Evidence Items
              remain read-only below.
            </p>
            <button className="sl-button" type="button" disabled>
              Start AI intake proposal
            </button>
          </article>
          <article className="sl-v2-card">
            <h3>Highest-value questions</h3>
            <ol>
              <li>Which court and division own the Proceeding?</li>
              <li>What relief is requested?</li>
              <li>
                Which facts are admitted, disputed, unknown, or contradicted?
              </li>
            </ol>
          </article>
        </div>
      </Surface>

      <Surface
        id="artifact-intake"
        title="Matter artifacts and lineage"
        eyebrow="Original preserved, derived artifacts separately traced"
        state="safely-unavailable"
      >
        <div
          className="sl-v2-table-wrap"
          tabIndex={0}
          aria-label="Scrollable Evidence Item inventory"
        >
          <table>
            <caption>Authorized Evidence Item inventory</caption>
            <thead>
              <tr>
                <th>Evidence Item</th>
                <th>Media type</th>
                <th>Content hash</th>
                <th>Review state</th>
              </tr>
            </thead>
            <tbody>
              {workspace.evidence.length === 0 ? (
                <tr>
                  <td colSpan={4}>
                    The authorized API returned no Evidence Items for this
                    Matter.
                  </td>
                </tr>
              ) : (
                workspace.evidence.map((item) => (
                  <tr key={item.evidenceItemId}>
                    <th scope="row">{item.title}</th>
                    <td>{item.mediaType}</td>
                    <td>
                      <code>{item.contentSha256}</code>
                    </td>
                    <td>{item.status}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="sl-v2-unavailable">
          Add, upload, OCR, transcription, classification, and request dispatch
          are not authorized by the current API.
        </p>
      </Surface>

      <Surface
        id="element-matrix"
        title="Issue and essential Elements proof matrix"
        eyebrow="Existing Claim support only, no invented Element state"
        state="proposal"
      >
        <div
          className="sl-v2-table-wrap"
          tabIndex={0}
          aria-label="Scrollable public-synthetic proof map"
        >
          <table>
            <caption>Public-synthetic proof map</caption>
            <thead>
              <tr>
                <th>Claim</th>
                <th>Support</th>
                <th>Counter-support</th>
                <th>Verification</th>
                <th>Next safe step</th>
              </tr>
            </thead>
            <tbody>
              {claims.length > 0 ? (
                claims.map((claim) => (
                  <tr key={claim.claimId}>
                    <th scope="row">{claim.statement}</th>
                    <td>{claim.support.length}</td>
                    <td>{claim.counterSupport.length}</td>
                    <td>{claim.supportVerificationState}</td>
                    <td>Review exact source records</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5}>
                    Safely unavailable: no authorized Claim ledger answer.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface
        id="recommendation"
        title="Ranked recommendation detail"
        eyebrow="Visible dimensions, prerequisites, and consequences"
        state="safely-unavailable"
      >
        <div className="sl-v2-grid sl-v2-grid-2">
          <article className="sl-v2-card">
            <h3>Recommendation schema is not mounted</h3>
            <p>
              The cockpit does not invent a score, urgency, challenge
              disposition, or review state from adjacent records.
            </p>
            <div className="sl-v2-score-lines">
              <span>
                Element impact <b>unknown</b>
              </span>
              <span>
                Authority fit <b>blocked</b>
              </span>
              <span>
                Evidence ready <b>unknown</b>
              </span>
            </div>
          </article>
          <article className="sl-v2-card">
            <h3>Available controls</h3>
            <p>
              Challenge, accept as proposed Task, request Work Product, and
              reject remain disabled until exact typed contracts exist.
            </p>
            <button className="sl-button" type="button" disabled>
              Accept as proposed Task
            </button>
          </article>
        </div>
        <div
          className="sl-v2-source-roles"
          aria-label="Recommendation source-role availability"
        >
          <Pill tone="course">Course instruction: not evaluated</Pill>
          <Pill tone="warn">Current Authority: not evaluated</Pill>
          <Pill tone="info">Matter record: not joined</Pill>
          <Pill tone="model">Model inference: unavailable</Pill>
          <Pill>Human decision: absent</Pill>
        </div>
      </Surface>

      <Surface
        id="strategy-authority"
        title="Course strategy and jurisdiction overlay"
        eyebrow="Source roles never blend"
        state="proposal"
      >
        <div className="sl-v2-grid sl-v2-grid-2">
          <article className="sl-v2-card sl-v2-lane-course">
            <h3>Course instruction</h3>
            <p>
              Instructional strategy remains separately labeled and requires an
              exact governed source span.
            </p>
            <Pill tone="course">instructional only</Pill>
          </article>
          <article className="sl-v2-card sl-v2-lane-authority">
            <h3>Current Authority</h3>
            <p>
              {authorityCount > 0
                ? `${authorityCount} source-linked support records require applicability review.`
                : "No joined current-Authority answer is available."}
            </p>
            <Pill tone="warn">applicability not ready</Pill>
          </article>
        </div>
      </Surface>

      <Surface
        id="agent-team"
        title="AI case team and challenge swarm"
        eyebrow="Bounded specifications, typed outputs only"
        state="safely-unavailable"
      >
        <div className="sl-v2-grid sl-v2-grid-4">
          {team.map(([title, detail]) => (
            <article className="sl-v2-card" key={title}>
              <h3>{title}</h3>
              <p>{detail}</p>
              <Pill tone="model">not running</Pill>
            </article>
          ))}
        </div>
      </Surface>

      <Surface
        id="blind-challenge"
        title="Blind challenge and human disposition"
        eyebrow="Independent defects, typed result, attributable decision"
        state="safely-unavailable"
      >
        <div className="sl-v2-grid sl-v2-grid-3">
          <article className="sl-v2-card">
            <h3>Challenge input</h3>
            <p>
              Exact proposal version, Matter snapshot, supporting records, and
              contrary material are required.
            </p>
          </article>
          <article className="sl-v2-card">
            <h3>Defect classes</h3>
            <p>
              Prerequisite, proof, admissibility, Authority, procedure, remedy,
              and downside risk remain separately reported.
            </p>
          </article>
          <article className="sl-v2-card">
            <h3>Challenge result</h3>
            <p>
              No challenge contract is mounted, so there is no result,
              confidence, rerun, or human disposition to display.
            </p>
          </article>
        </div>
      </Surface>

      <Surface
        id="model-routing"
        title="Provider-neutral model routing seam"
        eyebrow="Logical routes, never private hosts"
        state="safely-unavailable"
      >
        <div className="sl-v2-flow" aria-label="Model routing evidence flow">
          <span>Agent specification</span>
          <b>to</b>
          <span>logical route</span>
          <b>to</b>
          <span>policy and rights</span>
          <b>to</b>
          <span>pinned schema</span>
          <b>to</b>
          <span>transport profile</span>
          <b>to</b>
          <span>served-model evidence</span>
        </div>
        <div className="sl-v2-grid sl-v2-grid-3">
          <article className="sl-v2-card">
            <h3>Deployment binding</h3>
            <p>
              Logical route, transport profile, classification ceiling, timeout,
              retry class, and workload class.
            </p>
            <Pill>not returned</Pill>
          </article>
          <article className="sl-v2-card">
            <h3>Route and execution evidence</h3>
            <p>
              Gateway revision, requested bucket, backend, served model,
              failover, usage, latency, prompt hash, and schema hash.
            </p>
            <Pill>not returned</Pill>
          </article>
          <article className="sl-v2-card">
            <h3>Qualification and policy evidence</h3>
            <p>
              Matter scope, source rights, classification, egress, policy
              decision, and provider attribution must all be present.
            </p>
            <Pill tone="warn">required before use</Pill>
          </article>
        </div>
        <p className="sl-v2-unavailable">
          No model request is sent. Direct Qwen, SKGateway, and OpenAI bindings
          remain outside this React state.
        </p>
      </Surface>

      <Surface
        id="work-product-assembly"
        title="AI Work Product assembly"
        eyebrow="Exact versions, sentence grounding, and Approval invalidation"
        state="proposal"
      >
        {workspace.workProducts.length === 0 ? (
          <p className="sl-v2-unavailable">
            No authorized Work Product is recorded for this Matter.
          </p>
        ) : (
          <div className="sl-v2-grid sl-v2-grid-2">
            {workspace.workProducts.map((workProduct) => (
              <article className="sl-v2-card" key={workProduct.workProductId}>
                <h3>{workProduct.title}</h3>
                <p>
                  {workProduct.workProductKind} | {workProduct.status}
                </p>
                <dl>
                  <dt>Version</dt>
                  <dd>{workProduct.currentVersion.versionNumber}</dd>
                  <dt>Hash</dt>
                  <dd>
                    <code>{workProduct.currentVersion.contentSha256}</code>
                  </dd>
                  <dt>Approval</dt>
                  <dd>
                    {workProductApprovalState(workProduct) === "unbound"
                      ? "not bound"
                      : workProductApprovalState(workProduct) === "current"
                        ? `bound to current version ${workProduct.currentVersion.versionNumber}`
                        : `invalidated: binding names version ${workProduct.approvalBinding?.versionNumber}, current version is ${workProduct.currentVersion.versionNumber}`}
                  </dd>
                </dl>
                <Pill
                  tone={
                    workProductApprovalState(workProduct) === "current"
                      ? "good"
                      : "warn"
                  }
                >
                  {workProductApprovalState(workProduct) === "current"
                    ? "exact version bound"
                    : workProductApprovalState(workProduct) === "invalidated"
                      ? "Approval invalidated"
                      : "human review required"}
                </Pill>
              </article>
            ))}
          </div>
        )}
        <p className="sl-v2-unavailable">
          AI assembly and Approval mutation are not mounted. Editing an approved
          artifact must create a new version and invalidate prior Approval.
        </p>
      </Surface>

      <Surface
        id="deadline-actions"
        title="Tasks, Deadlines, and external-action handoff"
        eyebrow="AI proposes, deterministic services calculate, humans approve"
        state="safely-unavailable"
      >
        <div className="sl-v2-grid sl-v2-grid-3">
          <article className="sl-v2-card">
            <h3>Proposed Tasks</h3>
            <p>No reviewed Task mutation contract is mounted.</p>
          </article>
          <article className="sl-v2-card">
            <h3>Candidate Deadline</h3>
            <p>
              Rule, trigger, timezone, deterministic calculation, and reviewer
              are required before any date is operative.
            </p>
          </article>
          <article className="sl-v2-card">
            <h3>External action</h3>
            <p>
              Accepting a recommendation never files, serves, emails, schedules,
              or dispatches.
            </p>
          </article>
        </div>
        <div className="sl-v2-flow" aria-label="External action state machine">
          <span>draft</span>
          <b>to</b>
          <span>validated</span>
          <b>to</b>
          <span>approved</span>
          <b>to</b>
          <span>queued</span>
          <b>to</b>
          <span>dispatched</span>
          <b>to</b>
          <span>receipt verified</span>
        </div>
      </Surface>

      <Surface
        id="source-run-evidence"
        title="Exact source and run evidence"
        eyebrow="Replayable source, policy, and execution identity"
        state="proposal"
      >
        <div className="sl-v2-grid sl-v2-grid-2">
          <article className="sl-v2-card">
            <h3>Source evidence</h3>
            <dl>
              <dt>Snapshot</dt>
              <dd>
                <code>{workspace.provenance.sourceSnapshot}</code>
              </dd>
              <dt>Source files</dt>
              <dd>{workspace.provenance.sourceFiles.length}</dd>
              <dt>Observed</dt>
              <dd>{workspace.provenance.observedAt}</dd>
            </dl>
          </article>
          <article className="sl-v2-card">
            <h3>Agent Run evidence</h3>
            <p>
              Safely unavailable: no Agent Run HTTP surface returned route,
              prompt, schema, served model, usage, or policy evidence.
            </p>
          </article>
        </div>
      </Surface>

      <Surface
        id="case-log"
        title="Matter activity and provenance log"
        eyebrow="Readable projection over immutable records"
      >
        <div className="sl-v2-log">
          {workspace.audit.length === 0 ? (
            <p className="sl-v2-unavailable">
              The authorized API returned no Matter activity entries.
            </p>
          ) : (
            workspace.audit.map((entry) => (
              <article key={entry.auditId}>
                <time>{entry.occurredAt}</time>
                <div>
                  <strong>{entry.action}</strong>
                  <p>
                    {entry.actor} | outcome {entry.outcome}
                  </p>
                  {entry.detail && <p>{entry.detail}</p>}
                </div>
              </article>
            ))
          )}
        </div>
        <p className="sl-v2-unavailable">
          Chronology, manifest, dossier, and action-log exports are safely
          unavailable until versioned Work Product contracts exist.
        </p>
      </Surface>

      <Surface
        id="failure-states"
        title="Failure and incomplete states"
        eyebrow="Useful work continues only where authorized"
      >
        <div
          className="sl-v2-table-wrap"
          tabIndex={0}
          aria-label="Scrollable failure and incomplete-state table"
        >
          <table>
            <caption>Explicit failure and incomplete-state behavior</caption>
            <thead>
              <tr>
                <th>State</th>
                <th>UI treatment</th>
                <th>Blocked result</th>
              </tr>
            </thead>
            <tbody>
              {failureRows.map(([state, treatment, blocked]) => (
                <tr key={state}>
                  <th scope="row">{state}</th>
                  <td>{treatment}</td>
                  <td>{blocked}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface
        id="delivery-map"
        title="V2 delivery map"
        eyebrow="Current reality and explicit dependencies"
      >
        <div className="sl-v2-grid sl-v2-grid-3">
          <article className="sl-v2-card">
            <h3>Implemented now</h3>
            <p>
              Matter shell, authorized records, Claims, source links, Work
              Products, audit, provenance, and explicit negative state.
            </p>
          </article>
          <article className="sl-v2-card">
            <h3>Safely unavailable</h3>
            <p>
              Recommendation schema, strategist, challenge, joined Authority,
              unified intake, model evidence, and general decision surface.
            </p>
          </article>
          <article className="sl-v2-card">
            <h3>Separately gated</h3>
            <p>
              Provider traffic, protected context, Deadline calculation,
              Approval mutation, connectors, dispatch, and external effects.
            </p>
          </article>
        </div>
      </Surface>
    </div>
  );
}
