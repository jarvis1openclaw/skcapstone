import { useMemo, useState } from "react";
import type {
  ApprovalValidity,
  WorkProductAggregate,
  WorkProductComparison,
} from "./contracts";

export interface WorkProductsPanelProps {
  products: WorkProductAggregate[];
  comparison?: WorkProductComparison | null;
  validity?: Record<string, ApprovalValidity>;
  busy?: boolean;
  error?: string | null;
  onCompare?: (
    product: WorkProductAggregate,
    leftVersionId: string,
    rightVersionId: string,
  ) => void;
  onValidate?: (product: WorkProductAggregate) => void;
  onRequestApproval?: (product: WorkProductAggregate) => void;
  onDecideApproval?: (
    product: WorkProductAggregate,
    approvalId: string,
    decision: "approved" | "rejected",
  ) => void;
  onRevokeApproval?: (
    product: WorkProductAggregate,
    approvalId: string,
  ) => void;
}

export function WorkProductsPanel({
  products,
  comparison,
  validity = {},
  busy = false,
  error,
  onCompare,
  onValidate,
  onRequestApproval,
  onDecideApproval,
  onRevokeApproval,
}: WorkProductsPanelProps) {
  const [selectedId, setSelectedId] = useState(
    products[0]?.workProductId ?? "",
  );
  const selected = useMemo(
    () =>
      products.find((item) => item.workProductId === selectedId) ?? products[0],
    [products, selectedId],
  );
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");

  if (error) {
    return (
      <section aria-label="Work Products">
        <p role="alert">{error}</p>
      </section>
    );
  }
  if (!selected) {
    return (
      <section aria-label="Work Products">
        <h2>Work Products</h2>
        <p>No Work Products are available for this Matter.</p>
      </section>
    );
  }
  const current = selected.versions.find(
    (item) => item.versionId === selected.currentVersionId,
  );
  const currentValidation = [...selected.validations]
    .reverse()
    .find(
      (item) => item.binding.workProductVersionId === selected.currentVersionId,
    );

  return (
    <section aria-label="Work Products" aria-busy={busy}>
      <header>
        <h2>Work Products</h2>
        <label>
          Work Product
          <select
            aria-label="Work Product"
            value={selected.workProductId}
            onChange={(event) => setSelectedId(event.target.value)}
          >
            {products.map((item) => (
              <option key={item.workProductId} value={item.workProductId}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <span>{selected.status.replace("_", " ")}</span>
      </header>

      <article>
        <h3>{selected.title}</h3>
        <p>{selected.workProductKind}</p>
        <p>
          Version {current?.versionNumber ?? "unknown"} with{" "}
          {selected.groundings.length} sentence groundings
        </p>
        <pre aria-label="Current Work Product content">{current?.content}</pre>
      </article>

      <section aria-label="Validation">
        <h3>Validation</h3>
        <p>
          {currentValidation
            ? `${currentValidation.outcome}: ${currentValidation.rationale}`
            : "Not validated"}
        </p>
        {currentValidation?.missingSentenceKeys.length ? (
          <p>
            {currentValidation.missingSentenceKeys.length} sentences need
            grounding
          </p>
        ) : null}
        <button disabled={busy} onClick={() => onValidate?.(selected)}>
          Validate exact version
        </button>
        <button
          disabled={busy || currentValidation?.outcome !== "passed"}
          onClick={() => onRequestApproval?.(selected)}
        >
          Request Approval
        </button>
      </section>

      <section aria-label="Version comparison">
        <h3>Compare immutable versions</h3>
        <select
          aria-label="Left version"
          value={left}
          onChange={(event) => setLeft(event.target.value)}
        >
          <option value="">Select left version</option>
          {selected.versions.map((item) => (
            <option key={item.versionId} value={item.versionId}>
              Version {item.versionNumber}
            </option>
          ))}
        </select>
        <select
          aria-label="Right version"
          value={right}
          onChange={(event) => setRight(event.target.value)}
        >
          <option value="">Select right version</option>
          {selected.versions.map((item) => (
            <option key={item.versionId} value={item.versionId}>
              Version {item.versionNumber}
            </option>
          ))}
        </select>
        <button
          disabled={busy || !left || !right || left === right}
          onClick={() => onCompare?.(selected, left, right)}
        >
          Compare
        </button>
        {comparison?.workProductId === selected.workProductId ? (
          <pre aria-label="Version comparison result">
            {comparison.unifiedDiff.join("\n") || "No content changes"}
          </pre>
        ) : null}
      </section>

      <section aria-label="Approvals">
        <h3>Approvals</h3>
        {selected.approvals.length === 0 ? <p>No Approval requested.</p> : null}
        {selected.approvals.map((approval) => {
          const exact = validity[approval.approvalId];
          return (
            <article key={approval.approvalId}>
              <h4>Approval {approval.status}</h4>
              <p>
                Version {approval.binding.versionNumber}:{" "}
                {exact?.reasonCode ?? "validity pending"}
              </p>
              {approval.status === "pending" ? (
                <>
                  <button
                    disabled={busy}
                    onClick={() =>
                      onDecideApproval?.(
                        selected,
                        approval.approvalId,
                        "approved",
                      )
                    }
                  >
                    Approve exact version
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      onDecideApproval?.(
                        selected,
                        approval.approvalId,
                        "rejected",
                      )
                    }
                  >
                    Reject
                  </button>
                </>
              ) : null}
              {approval.status === "approved" && exact?.valid ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    onRevokeApproval?.(selected, approval.approvalId)
                  }
                >
                  Revoke Approval
                </button>
              ) : null}
            </article>
          );
        })}
      </section>
    </section>
  );
}
