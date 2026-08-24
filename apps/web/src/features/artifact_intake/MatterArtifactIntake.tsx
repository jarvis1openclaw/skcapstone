import { type FormEvent, useState } from "react";

import {
  artifactCommandFromFile,
  ArtifactIntakeFeatureClient,
  ArtifactIntakeFeatureError,
} from "./client";
import type {
  ArtifactErrorCode,
  ArtifactIntakeReceipt,
  ArtifactRead,
} from "./types";

export type ArtifactPanelState = "idle" | "submitting" | "success" | "error";

const errorText: Record<ArtifactErrorCode, string> = {
  authentication_required: "Sign in again before adding an artifact.",
  access_denied: "You do not have access to manage artifacts for this Matter.",
  validation_failed: "The artifact metadata or bytes did not pass validation.",
  precondition_failed: "The artifact failed a required scan or lineage check.",
  idempotency_conflict: "This retry key already names a different request.",
  policy_unavailable:
    "Policy is temporarily unavailable. No artifact was changed.",
  dependency_unavailable:
    "Artifact storage or audit is temporarily unavailable.",
  resource_unavailable: "The requested artifact is unavailable.",
  internal_error: "Artifact intake failed closed. No artifact was changed.",
};

function Hash(props: { label: string; value: string }) {
  return (
    <span className="sl-record-meta">
      {props.label} <code className="sl-hash">{props.value}</code>
    </span>
  );
}

function scanLabel(artifact: ArtifactRead): string {
  if (
    artifact.scan.state === "clean" &&
    artifact.quarantineState === "released"
  ) {
    return "Clean and released";
  }
  if (artifact.scan.state === "pending") return "Scan pending in quarantine";
  if (artifact.scan.state === "unsafe") return "Unsafe and rejected";
  return "Scan failed closed";
}

function CustodyTimeline(props: { artifact: ArtifactRead }) {
  return (
    <section aria-labelledby={`custody-${props.artifact.artifactId}`}>
      <h4 id={`custody-${props.artifact.artifactId}`}>Custody timeline</h4>
      <ol>
        {props.artifact.custody.map((event) => (
          <li key={event.custodyEventId}>
            <strong>{event.action.replaceAll("_", " ")}</strong> by principal{" "}
            <code>{event.custodianPrincipalId}</code> at {event.occurredAt}.
            Source {event.sourceIdentity}.
          </li>
        ))}
      </ol>
      <Hash label="Custody revision" value={props.artifact.custodyRevision} />
    </section>
  );
}

function ArtifactCard(props: { artifact: ArtifactRead }) {
  const { artifact } = props;
  return (
    <article data-artifact-kind={artifact.artifactKind}>
      <header>
        <h3>{artifact.filename}</h3>
        <p>
          {artifact.artifactKind}; {artifact.mediaType}; {artifact.byteCount}{" "}
          bytes; review {artifact.reviewState}
        </p>
      </header>
      <p>
        <strong>{scanLabel(artifact)}</strong>. Extraction{" "}
        {artifact.extractionState}.
      </p>
      <p>
        Source {artifact.source.sourceSystem}: {artifact.source.sourceIdentity},
        version {artifact.source.sourceVersion}.
      </p>
      <Hash label="Content SHA-256" value={artifact.contentSha256} />
      <br />
      <Hash label="Original SHA-256" value={artifact.originalSha256} />
      <dl>
        <dt>Classification</dt>
        <dd>{artifact.governance.classification}</dd>
        <dt>Privilege</dt>
        <dd>{artifact.governance.privilegeState}</dd>
        <dt>Retention policy</dt>
        <dd>
          <code>{artifact.governance.retentionPolicyId}</code>
        </dd>
        <dt>Legal holds {artifact.governance.legalHoldIds.length}</dt>
        <dd>{artifact.governance.legalHoldIds.join(", ") || "None"}</dd>
        <dt>Ethical walls {artifact.governance.ethicalWallIds.length}</dt>
        <dd>{artifact.governance.ethicalWallIds.join(", ") || "None"}</dd>
      </dl>
      <CustodyTimeline artifact={artifact} />
      {artifact.derivation !== null && (
        <section>
          <h4>Derived lineage</h4>
          <p>
            {artifact.derivation.kind} from{" "}
            <code>{artifact.derivation.parentArtifactId}</code> using{" "}
            {artifact.derivation.toolEvidence.toolName} v
            {artifact.derivation.toolEvidence.toolVersion}.
          </p>
          <Hash label="Lineage revision" value={artifact.lineageRevision} />
        </section>
      )}
      {artifact.proposedLinks.length > 0 && (
        <section>
          <h4>Proposed record links</h4>
          <ul>
            {artifact.proposedLinks.map((link) => (
              <li key={link.linkId}>
                {link.targetType} <code>{link.targetId}</code>: {link.rationale}{" "}
                ({link.reviewState})
              </li>
            ))}
          </ul>
        </section>
      )}
      {artifact.corrections.length > 0 && (
        <section>
          <h4>Human corrections</h4>
          <ul>
            {artifact.corrections.map((correction) => (
              <li key={correction.correctionId}>
                {correction.reason} New derived artifact{" "}
                <code>{correction.correctedArtifactId}</code>.
              </li>
            ))}
          </ul>
        </section>
      )}
      {artifact.supersession !== null && (
        <p>
          <strong>Superseded by</strong>{" "}
          <code>{artifact.supersession.successorArtifactId}</code>.{" "}
          {artifact.supersession.reason}
        </p>
      )}
      <Hash label="Projection SHA-256" value={artifact.projectionSha256} />
    </article>
  );
}

export function MatterArtifactIntakePanel(props: {
  state: ArtifactPanelState;
  receipt: ArtifactIntakeReceipt | null;
  errorCode?: ArtifactErrorCode;
  onSubmit?: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <section
      id="matter-artifact-intake"
      aria-labelledby="artifact-intake-title"
    >
      <header>
        <p>Public-synthetic only</p>
        <h2 id="artifact-intake-title">Matter artifact intake</h2>
        <p>
          Original bytes are immutable. Reprocessing and correction create
          derived, hash-pinned lineage records.
        </p>
      </header>
      <form onSubmit={props.onSubmit} aria-busy={props.state === "submitting"}>
        <label htmlFor="artifact-file">Add artifact to this Matter</label>
        <input id="artifact-file" name="artifact" type="file" required />
        <label htmlFor="artifact-source">Source identity</label>
        <input
          id="artifact-source"
          name="sourceIdentity"
          pattern="[a-z0-9][a-z0-9._:@/-]{0,254}"
          defaultValue="synthetic-user-upload"
          required
        />
        <label>
          <input name="requestOcr" type="checkbox" /> Request OCR
        </label>
        <label>
          <input name="requestTranscript" type="checkbox" /> Request transcript
        </label>
        <button type="submit" disabled={props.state === "submitting"}>
          Add to this Matter
        </button>
      </form>
      <div aria-live="polite">
        {props.state === "submitting" && (
          <p>
            Hashing and scanning the selected artifact. No record is visible
            yet.
          </p>
        )}
        {props.state === "error" && (
          <p role="alert">{errorText[props.errorCode ?? "internal_error"]}</p>
        )}
      </div>
      {props.receipt === null ? (
        <p>No artifact intake has been recorded for this Matter.</p>
      ) : (
        <div>
          <ArtifactCard artifact={props.receipt.artifact} />
          {props.receipt.derivedArtifacts.map((artifact) => (
            <ArtifactCard artifact={artifact} key={artifact.artifactId} />
          ))}
          <section>
            <h3>Intake receipt</h3>
            <p>
              {props.receipt.duplicate
                ? "Duplicate custody recorded"
                : "New artifact recorded"}
              ;{" "}
              {props.receipt.replayed ? "idempotent replay" : "first execution"}
              .
            </p>
            <Hash label="Request SHA-256" value={props.receipt.requestSha256} />
            <p>
              Audit event <code>{props.receipt.auditEventId}</code>; outbox{" "}
              <code>{props.receipt.outboxId}</code>.
            </p>
          </section>
        </div>
      )}
    </section>
  );
}

export function MatterArtifactIntakeFeature(props: {
  matterId: string;
  retentionPolicyId: string;
  client: ArtifactIntakeFeatureClient;
  initialReceipt?: ArtifactIntakeReceipt | null;
}) {
  const [state, setState] = useState<ArtifactPanelState>("idle");
  const [receipt, setReceipt] = useState<ArtifactIntakeReceipt | null>(
    props.initialReceipt ?? null,
  );
  const [errorCode, setErrorCode] = useState<ArtifactErrorCode>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const file = form.get("artifact");
    const sourceIdentity = form.get("sourceIdentity");
    if (!(file instanceof File) || typeof sourceIdentity !== "string") {
      setState("error");
      setErrorCode("validation_failed");
      return;
    }
    setState("submitting");
    setErrorCode(undefined);
    try {
      const command = await artifactCommandFromFile({
        file,
        sourceIdentity,
        retentionPolicyId: props.retentionPolicyId,
        requestOcr: form.get("requestOcr") === "on",
        requestTranscript: form.get("requestTranscript") === "on",
      });
      const next = await props.client.intake(
        props.matterId,
        crypto.randomUUID(),
        command,
      );
      setReceipt(next);
      setState("success");
      event.currentTarget.reset();
    } catch (error) {
      setErrorCode(
        error instanceof ArtifactIntakeFeatureError
          ? error.code
          : "internal_error",
      );
      setState("error");
    }
  }

  return (
    <MatterArtifactIntakePanel
      state={state}
      receipt={receipt}
      errorCode={errorCode}
      onSubmit={(event) => void submit(event)}
    />
  );
}
