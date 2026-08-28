import { describe, expect, it } from "vitest";

import { renderStatic } from "../../testing/ssr";
import { MatterArtifactIntakePanel } from "./MatterArtifactIntake";
import type { ArtifactIntakeReceipt } from "./types";

const rootId = "30000000-0000-4000-8000-000000000001";
const childId = "30000000-0000-4000-8000-000000000002";
const correctedId = "30000000-0000-4000-8000-000000000003";

const receipt: ArtifactIntakeReceipt = {
  artifact: {
    artifactId: rootId,
    tenantId: "10000000-0000-4000-8000-000000000001",
    matterId: "20000000-0000-4000-8000-000000000001",
    artifactKind: "original",
    filename: "synthetic-exhibit.txt",
    mediaType: "text/plain",
    byteCount: 32,
    contentSha256: "1".repeat(64),
    originalSha256: "1".repeat(64),
    source: {
      sourceSystem: "public_synthetic",
      sourceIdentity: "synthetic-source-001",
      sourceVersion: "v1",
      observedAt: "2099-01-02T03:04:05Z",
    },
    acquisitionMethod: "synthetic_adapter",
    storageLocator: `synthetic:sha256:${"1".repeat(64)}`,
    quarantineState: "released",
    scan: {
      scanId: "40000000-0000-4000-8000-000000000001",
      scannerName: "synthetic-scanner",
      scannerVersion: "1.0.0",
      signatureRevision: "2".repeat(64),
      state: "clean",
      scannedAt: "2099-01-02T03:04:05Z",
      detailCode: "clean",
    },
    extractionState: "complete",
    parentArtifactId: null,
    duplicateOfArtifactId: null,
    derivation: null,
    childArtifactIds: [childId],
    custody: [
      {
        custodyEventId: "50000000-0000-4000-8000-000000000001",
        action: "acquired",
        custodianPrincipalId: "60000000-0000-4000-8000-000000000001",
        occurredAt: "2099-01-02T03:04:05Z",
        sourceIdentity: "synthetic-source-001",
        idempotencyKeySha256: "3".repeat(64),
      },
    ],
    governance: {
      classification: "public",
      privilegeState: "not_privileged",
      retentionPolicyId: "70000000-0000-4000-8000-000000000001",
      legalHoldIds: ["80000000-0000-4000-8000-000000000001"],
      ethicalWallIds: [],
      policyDecisionId: "90000000-0000-4000-8000-000000000001",
      policyRevision: "4".repeat(64),
    },
    proposedLinks: [
      {
        linkId: "a0000000-0000-4000-8000-000000000001",
        targetType: "fact_assertion",
        targetId: "b0000000-0000-4000-8000-000000000001",
        rationale: "Synthetic proposed support link.",
        proposedByPrincipalId: "60000000-0000-4000-8000-000000000001",
        proposedAt: "2099-01-02T03:04:05Z",
        reviewState: "proposed",
        reviewedByPrincipalId: null,
        reviewedAt: null,
      },
    ],
    reviewState: "accepted",
    reviewedByPrincipalId: "60000000-0000-4000-8000-000000000001",
    reviewedAt: "2099-01-02T03:05:05Z",
    corrections: [],
    supersession: null,
    projectionRevision: 2,
    lineageRevision: "5".repeat(64),
    custodyRevision: "6".repeat(64),
    projectionSha256: "7".repeat(64),
    createdAt: "2099-01-02T03:04:05Z",
  },
  derivedArtifacts: [],
  lineage: {
    originalArtifactId: rootId,
    artifacts: [],
    edges: [],
    lineageRevision: "5".repeat(64),
  },
  idempotencyKey: "artifact-ui-001",
  requestSha256: "8".repeat(64),
  replayed: false,
  duplicate: false,
  auditEventId: "c0000000-0000-4000-8000-000000000001",
  outboxId: "d0000000-0000-4000-8000-000000000001",
};

const superseded = {
  ...receipt.artifact,
  artifactId: childId,
  artifactKind: "derived" as const,
  filename: "synthetic-exhibit.ocr.txt",
  contentSha256: "9".repeat(64),
  parentArtifactId: rootId,
  childArtifactIds: [correctedId],
  derivation: {
    parentArtifactId: rootId,
    childArtifactId: childId,
    kind: "ocr" as const,
    toolEvidence: {
      toolName: "synthetic-ocr",
      toolVersion: "1.0.0",
      operation: "ocr" as const,
      inputSha256: "1".repeat(64),
      outputSha256: "9".repeat(64),
      routeId: null,
      agentRunId: null,
    },
    createdAt: "2099-01-02T03:04:05Z",
  },
  reviewState: "superseded" as const,
  corrections: [
    {
      correctionId: "e0000000-0000-4000-8000-000000000001",
      targetArtifactId: childId,
      correctedArtifactId: correctedId,
      reason: "Human reviewer corrected synthetic OCR text.",
      correctedByPrincipalId: "60000000-0000-4000-8000-000000000001",
      correctedAt: "2099-01-02T03:06:05Z",
    },
  ],
  supersession: {
    supersessionId: "f0000000-0000-4000-8000-000000000001",
    supersededArtifactId: childId,
    successorArtifactId: correctedId,
    reason: "Human reviewer corrected synthetic OCR text.",
    recordedByPrincipalId: "60000000-0000-4000-8000-000000000001",
    recordedAt: "2099-01-02T03:06:05Z",
  },
};

describe("Matter artifact intake", () => {
  it("renders a usable intake form with explicit synthetic and immutable boundaries", () => {
    const html = renderStatic(
      <MatterArtifactIntakePanel state="idle" receipt={null} />,
    );
    expect(html).toContain('id="matter-artifact-intake"');
    expect(html).toContain("Add artifact to this Matter");
    expect(html).toContain('type="file"');
    expect(html).toContain('name="sourceIdentity"');
    expect(html).toContain('name="requestOcr"');
    expect(html).toContain("Public-synthetic only");
    expect(html).toContain("Original bytes are immutable");
    expect(html).toContain("No artifact intake has been recorded");
  });

  it("renders hash, scan, custody, policy, hold, link, and audit evidence", () => {
    const html = renderStatic(
      <MatterArtifactIntakePanel state="success" receipt={receipt} />,
    );
    expect(html).toContain("synthetic-exhibit.txt");
    expect(html).toContain("Clean and released");
    expect(html).toContain("Original SHA-256");
    expect(html).toContain("Custody timeline");
    expect(html).toContain("Legal holds 1");
    expect(html).toContain("Retention policy");
    expect(html).toContain("Proposed record links");
    expect(html).toContain("fact_assertion");
    expect(html).toContain("Audit event");
    expect(html).toContain('class="sl-hash"');
  });

  it("renders derived correction and supersession without rewriting lineage", () => {
    const correctedReceipt: ArtifactIntakeReceipt = {
      ...receipt,
      derivedArtifacts: [superseded],
      lineage: {
        ...receipt.lineage,
        artifacts: [receipt.artifact, superseded],
        edges: [superseded.derivation],
      },
    };
    const html = renderStatic(
      <MatterArtifactIntakePanel state="success" receipt={correctedReceipt} />,
    );
    expect(html).toContain("Derived lineage");
    expect(html).toContain("ocr");
    expect(html).toContain("Human corrections");
    expect(html).toContain("corrected synthetic OCR text");
    expect(html).toContain("Superseded by");
    expect(html).toContain(correctedId);
  });

  it("keeps bounded failure and submission states accessible", () => {
    const pending = renderStatic(
      <MatterArtifactIntakePanel state="submitting" receipt={null} />,
    );
    expect(pending).toContain('aria-busy="true"');
    expect(pending).toContain("Hashing and scanning the selected artifact");
    const failed = renderStatic(
      <MatterArtifactIntakePanel
        state="error"
        receipt={null}
        errorCode="policy_unavailable"
      />,
    );
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("Policy is temporarily unavailable");
  });
});
