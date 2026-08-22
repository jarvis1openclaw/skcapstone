/**
 * Synthetic matter workspace fixture for component tests. Every value is
 * synthetic; no real matter content or real legacy identifiers are used.
 */

import type { MatterWorkspace } from "../api/types";

export const syntheticWorkspace: MatterWorkspace = {
  matter: {
    matterId: "20000000-0000-4000-8000-0000000000a1",
    clientId: "40000000-0000-4000-8000-000000000001",
    clientDisplayName: "Synthetic Client",
    engagementId: null,
    title: "Synthetic transaction review",
    summary: "Synthetic matter summary for workspace tests.",
    status: "open",
    openedAt: "2099-01-01T00:00:00Z",
    legacyAliases: ["synthetic-legacy-container-1"],
  },
  engagementDisplayName: null,
  parties: [
    {
      partyId: "50000000-0000-4000-8000-0000000000a1",
      displayName: "Synthetic Party",
      partyKind: "company",
      roles: ["counterparty"],
      status: "proposed",
    },
  ],
  timeline: [
    {
      eventId: "60000000-0000-4000-8000-0000000000e1",
      eventType: "transaction_review",
      description: "Synthetic transaction review activity",
      occurredAt: "2099-01-01T00:00:00Z",
      observedAt: "2099-01-02T03:04:05Z",
      status: "proposed",
      sourcePath: "synthetic/legacy/activity.md",
      legacyAliases: ["synthetic-legacy-activity-1"],
    },
  ],
  facts: [
    {
      factAssertionId: "70000000-0000-4000-8000-0000000000f1",
      predicate: "closing_date",
      assertedValue: "2099-02-01",
      valueType: "string",
      reviewStatus: "source_asserted",
      sourcePath: "synthetic/legacy/matter.md",
      sourceLocator: "frontmatter/closing_date",
      sourceMissing: false,
      tensionGroupKey: "date_or_deadline",
    },
    {
      factAssertionId: "70000000-0000-4000-8000-0000000000f2",
      predicate: "response_due_date",
      assertedValue: "2099-02-10",
      valueType: "string",
      reviewStatus: "source_asserted",
      sourcePath: "synthetic/legacy/activity.md",
      sourceLocator: "frontmatter/response_due_date",
      sourceMissing: false,
      tensionGroupKey: "date_or_deadline",
    },
    {
      factAssertionId: "70000000-0000-4000-8000-0000000000f3",
      predicate: "trust_name",
      assertedValue: "Synthetic Trust",
      valueType: "string",
      reviewStatus: "ambiguous",
      sourcePath: null,
      sourceLocator: "frontmatter/trust_name",
      sourceMissing: true,
      tensionGroupKey: null,
    },
  ],
  tensions: [
    {
      tensionKey: "date_or_deadline",
      status: "unresolved",
      assertionIds: [
        "70000000-0000-4000-8000-0000000000f1",
        "70000000-0000-4000-8000-0000000000f2",
      ],
      reviewRequired: true,
    },
  ],
  evidence: [
    {
      evidenceItemId: "80000000-0000-4000-8000-0000000000e9",
      title: "Synthetic executed agreement",
      mediaType: "application/pdf",
      contentSha256: "a".repeat(64),
      status: "collected",
      sourcePath: "synthetic/evidence/agreement.pdf",
      sourceMissing: false,
    },
  ],
  communications: [
    {
      communicationId: "90000000-0000-4000-8000-0000000000c1",
      channel: "correspondence",
      summary: "OWNER-DIRECTIONS.md",
      occurredAt: "2099-01-02T03:04:05Z",
      status: "recorded",
      sourcePath: "synthetic/correspondence/OWNER-DIRECTIONS.md",
      sourceMissing: false,
    },
  ],
  workProducts: [
    {
      workProductId: "a0000000-0000-4000-8000-0000000000a1",
      title: "Synthetic demand letter",
      workProductKind: "letter",
      status: "in_review",
      previousVersionNumber: 2,
      approvalBinding: {
        versionId: "a0000000-0000-4000-8000-0000000000b2",
        versionNumber: 2,
        contentSha256: "b".repeat(64),
      },
      currentVersion: {
        versionId: "a0000000-0000-4000-8000-0000000000b3",
        versionNumber: 3,
        contentSha256: "d".repeat(64),
        status: "draft",
        content:
          "Demand letter\nOn [?purchase_date: unresolved tension] the Synthetic Client purchased the vehicle. The vehicle stalled twice. Notice was timely.",
        sentences: [
          {
            sentenceKey: "1".repeat(64),
            text: "On [?purchase_date: unresolved tension] the Synthetic Client purchased the vehicle.",
            groundingStatus: "deferred_unknown",
            claimId: null,
            claimStatement: null,
            claimStatus: null,
            warning:
              "Grounding deferred until the bracketed unknown is resolved.",
          },
          {
            sentenceKey: "2".repeat(64),
            text: "The vehicle stalled twice.",
            groundingStatus: "grounded",
            claimId: "c0000000-0000-4000-8000-000000000001",
            claimStatement: "The vehicle stalled during ordinary operation.",
            claimStatus: "supported",
            warning: null,
          },
          {
            sentenceKey: "3".repeat(64),
            text: "Notice was timely.",
            groundingStatus: "ungrounded",
            claimId: null,
            claimStatement: null,
            claimStatus: null,
            warning:
              "Factual sentence is not grounded in a claim ledger entry.",
          },
        ],
        compareRows: [
          {
            change: "unchanged",
            previousText: "The vehicle stalled twice.",
            currentText: "The vehicle stalled twice.",
          },
          {
            change: "removed",
            previousText: "Notice was sent.",
            currentText: null,
          },
          {
            change: "added",
            previousText: null,
            currentText: "Notice was timely.",
          },
        ],
      },
    },
  ],
  versionLineage: [
    {
      packetVersion: 1,
      sourcePath: "synthetic/packets/packet-v1-facts.json",
      sourceSha256: "b".repeat(64),
      historical: true,
      currentReviewBaseline: false,
    },
    {
      packetVersion: 2,
      sourcePath: "synthetic/packets/packet-v2-facts.json",
      sourceSha256: "c".repeat(64),
      historical: false,
      currentReviewBaseline: true,
    },
  ],
  executionStates: [
    {
      targetType: "matter",
      targetId: "20000000-0000-4000-8000-0000000000a1",
      stateKind: "approval",
      stateValue: "pending_review",
    },
    {
      targetType: "matter_event",
      targetId: "60000000-0000-4000-8000-0000000000e1",
      stateKind: "execution",
      stateValue: "not_started",
    },
  ],
  gaps: [
    {
      gapId: "missing-source-1",
      kind: "missing_source",
      description:
        "A fact assertion references a source that has no recorded source file.",
    },
    {
      gapId: "unrecorded-engagement",
      kind: "unrecorded_engagement",
      description: "No engagement is recorded for this matter yet.",
    },
  ],
  audit: [
    {
      auditId: "audit-1",
      action: "pilot_mapping_review.approved",
      actor: "Synthetic Human Reviewer",
      occurredAt: "2099-01-03T00:00:00Z",
      outcome: "approved",
      detail: "synthetic-review-artifact#approved",
    },
  ],
  provenance: {
    sourceSnapshot: "synthetic-snapshot-1",
    currentSourceSnapshot: "synthetic-snapshot-1",
    adapterVersion: "0.1.0",
    observedAt: "2099-01-02T03:04:05Z",
    stale: false,
    sourceFiles: [
      {
        relativePath: "synthetic/legacy/matter.md",
        contentSha256: "d".repeat(64),
        observedAt: "2099-01-02T03:04:05Z",
        byteCount: 128,
      },
    ],
  },
};
