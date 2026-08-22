import type {
  WorkspaceDraftCompareRow,
  WorkspaceDraftSentence,
  WorkspaceWorkProduct,
} from "../api/types";
import { StatusBadge } from "./StatusBadge";
import {
  approvalState,
  draftCompareState,
  draftGroundingStatus,
  statusOrFallback,
} from "../design/tokens";

const unknownPattern = /(\[\?[a-z][a-z0-9_]{0,63}(?:: [^\]\r\n]+)?\])/g;
const exactUnknownPattern = /^\[\?[a-z][a-z0-9_]{0,63}(?:: [^\]\r\n]+)?\]$/;

export function approvalIsInvalidated(
  workProduct: WorkspaceWorkProduct,
): boolean {
  const approval = workProduct.approvalBinding;
  const current = workProduct.currentVersion;
  return (
    approval !== null &&
    (approval.versionId !== current.versionId ||
      approval.versionNumber !== current.versionNumber ||
      approval.contentSha256 !== current.contentSha256)
  );
}

function DraftPreview(props: { content: string }) {
  const runs = props.content.split(unknownPattern);
  return (
    <div className="sl-draft-preview" aria-label="Draft preview">
      {runs.map((run, index) =>
        exactUnknownPattern.test(run) ? (
          <mark
            className="sl-unknown"
            data-unknown="unresolved"
            key={`${index}-${run}`}
          >
            {run}
          </mark>
        ) : (
          run
        ),
      )}
    </div>
  );
}

function SentenceGroundingRow(props: { sentence: WorkspaceDraftSentence }) {
  const { sentence } = props;
  const warning = sentence.warning;
  return (
    <li data-grounding-status={sentence.groundingStatus}>
      <span>{sentence.text}</span>
      <StatusBadge
        status={statusOrFallback(
          draftGroundingStatus,
          sentence.groundingStatus,
        )}
      />
      {sentence.claimId !== null && (
        <span className="sl-record-meta">
          <a href={`#claim-${sentence.claimId}`}>
            Claim ledger entry {sentence.claimId}
          </a>
          : {sentence.claimStatement}
          {sentence.claimStatus === null ? "" : ` (${sentence.claimStatus})`}
        </span>
      )}
      {warning !== null && (
        <span
          className="sl-grounding-warning"
          role={
            sentence.groundingStatus === "deferred_unknown" ? "status" : "alert"
          }
        >
          {warning}
        </span>
      )}
    </li>
  );
}

function CompareCell(props: {
  row: WorkspaceDraftCompareRow;
  side: "previous" | "current";
}) {
  const text =
    props.side === "previous" ? props.row.previousText : props.row.currentText;
  return <td>{text ?? <span className="sl-record-meta">No text</span>}</td>;
}

function VersionCompare(props: { workProduct: WorkspaceWorkProduct }) {
  const { currentVersion, previousVersionNumber } = props.workProduct;
  if (previousVersionNumber === null) {
    return <p>No earlier version is available for comparison.</p>;
  }
  return (
    <div className="sl-version-compare">
      <h4>
        Version compare v{previousVersionNumber} to v
        {currentVersion.versionNumber}
      </h4>
      <table>
        <caption>
          Sentence-level changes from version {previousVersionNumber} to version{" "}
          {currentVersion.versionNumber}
        </caption>
        <thead>
          <tr>
            <th scope="col">Change</th>
            <th scope="col">Previous version</th>
            <th scope="col">Current version</th>
          </tr>
        </thead>
        <tbody>
          {currentVersion.compareRows.map((row, index) => (
            <tr key={`${index}-${row.change}`} data-change={row.change}>
              <th scope="row">
                <StatusBadge status={draftCompareState[row.change]} />
              </th>
              <CompareCell row={row} side="previous" />
              <CompareCell row={row} side="current" />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ApprovalState(props: { workProduct: WorkspaceWorkProduct }) {
  const { workProduct } = props;
  if (approvalIsInvalidated(workProduct)) {
    return (
      <div className="sl-approval-invalidated" role="alert">
        <StatusBadge status={approvalState.invalidated} />
        <p>
          The recorded Approval names v
          {workProduct.approvalBinding?.versionNumber} and no longer matches v
          {workProduct.currentVersion.versionNumber}. The edit invalidated that
          Approval. Re-validation and a new exact-version Approval are required.
        </p>
        <p className="sl-record-meta">
          Approval hash{" "}
          <code className="sl-hash">
            {workProduct.approvalBinding?.contentSha256}
          </code>
          ; current hash{" "}
          <code className="sl-hash">
            {workProduct.currentVersion.contentSha256}
          </code>
        </p>
      </div>
    );
  }
  if (workProduct.approvalBinding === null) {
    return <StatusBadge status={approvalState.pending} />;
  }
  return <StatusBadge status={approvalState.approved} />;
}

function WorkProductEditor(props: { workProduct: WorkspaceWorkProduct }) {
  const { workProduct } = props;
  const version = workProduct.currentVersion;
  const blockers = version.sentences.filter(
    (sentence) => sentence.groundingStatus !== "grounded",
  );
  return (
    <article
      className="sl-work-product"
      id={`work-product-${workProduct.workProductId}`}
    >
      <header>
        <h3>{workProduct.title}</h3>
        <span className="sl-record-meta">
          {workProduct.workProductKind}; v{version.versionNumber};{" "}
          {version.status}
        </span>
        <code className="sl-hash">{version.contentSha256}</code>
        <ApprovalState workProduct={workProduct} />
      </header>
      <div className="sl-drafting-grid">
        <div>
          <h4>Claim-grounded editor</h4>
          <label htmlFor={`draft-${workProduct.workProductId}`}>
            Draft text
          </label>
          <textarea
            id={`draft-${workProduct.workProductId}`}
            className="sl-draft-editor"
            defaultValue={version.content}
            rows={10}
          />
          <p className="sl-record-meta">
            Saving an edit creates a new hash-pinned version. Existing sentence
            bindings and Approval never carry forward to changed bytes.
          </p>
          <h5>Bracketed unknown preview</h5>
          <DraftPreview content={version.content} />
        </div>
        <div>
          <h4>Factual sentence grounding</h4>
          {blockers.length > 0 && (
            <p role="alert">
              DRAFT_READY is blocked: {blockers.length} factual sentence
              {blockers.length === 1 ? " requires" : "s require"} review.
            </p>
          )}
          <ol className="sl-record-list sl-grounding-list">
            {version.sentences.map((sentence) => (
              <SentenceGroundingRow
                key={sentence.sentenceKey}
                sentence={sentence}
              />
            ))}
          </ol>
        </div>
      </div>
      <VersionCompare workProduct={workProduct} />
    </article>
  );
}

export function WorkProductDraftingSection(props: {
  workProducts: readonly WorkspaceWorkProduct[];
}) {
  return (
    <section id="work-products" aria-label="Work products" tabIndex={-1}>
      <h2>Documents</h2>
      {props.workProducts.length === 0 ? (
        <p>No work products are recorded for this matter.</p>
      ) : (
        props.workProducts.map((workProduct) => (
          <WorkProductEditor
            key={workProduct.workProductId}
            workProduct={workProduct}
          />
        ))
      )}
    </section>
  );
}
