/**
 * Synthetic claim ledger fixture for component tests (SKL-S4-03B).
 * Every value is synthetic: the statements, locators, hashes, principal
 * and model identifiers are placeholders, with no real matter content,
 * no real Authority, and no legacy identifiers.
 */

import type {
  ApplicabilityCheck,
  Challenge,
  ClaimLedger,
  ClaimLedgerEntry,
  ClaimStateTransition,
  ClaimSupportRecord,
  GateEvaluation,
} from "../api/types";
import { SYNTHETIC_CORPUS_MATTER_ID } from "./corpus";

export const SYNTHETIC_CLAIM_ID = "40000000-0000-4000-8000-0000000000c1";
export const SYNTHETIC_READY_CLAIM_ID = "40000000-0000-4000-8000-0000000000c2";
export const SYNTHETIC_CLAIM_SHA = "b".repeat(64);
export const SYNTHETIC_EXCERPT_SHA = "d".repeat(64);
export const SYNTHETIC_POLICY_REVISION = "e".repeat(64);
export const SYNTHETIC_REVIEWER_ID = "30000000-0000-4000-8000-0000000000p1";
export const SYNTHETIC_MISMATCH_DEFECT_TEXT =
  "The recorded quotation does not match the pinned source span bytes.";

function supportRecord(
  supportId: string,
  kind: "support" | "counter_support",
  locator: string,
): ClaimSupportRecord {
  return {
    supportId,
    kind,
    recordedAt: "2026-08-20T10:00:00.000Z",
    sourceSystem: "synthetic-corpus",
    sourceVersion: "1",
    sourceLocator: locator,
    contentSha256: SYNTHETIC_CLAIM_SHA,
    spanStart: 0,
    spanEnd: 12,
    excerptSha256: SYNTHETIC_EXCERPT_SHA,
    note: null,
    recordedByPrincipalId: SYNTHETIC_REVIEWER_ID,
    policyRevision: SYNTHETIC_POLICY_REVISION,
  };
}

/** The mismatch-defect challenge from the card's required test case. */
export const syntheticMismatchChallenge: Challenge = {
  challengeId: "synthetic-challenge-1",
  issuedAt: "2026-08-20T11:00:00.000Z",
  independence: "independent",
  outcome: "defect_found",
  sawChallengedConclusion: false,
  challengerProvider: "synthetic",
  challengerModelName: "challenger-model",
  challengerModelRevision: "r1",
  defects: [
    {
      defectKind: "quotation_error",
      description: SYNTHETIC_MISMATCH_DEFECT_TEXT,
    },
  ],
};

const challengedTransitions: readonly ClaimStateTransition[] = [
  {
    fromStatus: null,
    toStatus: "proposed",
    at: "2026-08-20T09:00:00.000Z",
    version: 1,
  },
  {
    fromStatus: "proposed",
    toStatus: "under_review",
    at: "2026-08-20T10:30:00.000Z",
    version: 2,
  },
  {
    fromStatus: "under_review",
    toStatus: "challenged",
    at: "2026-08-20T11:30:00.000Z",
    version: 3,
  },
];

const failedApplicability: readonly ApplicabilityCheck[] = [
  {
    checkId: "authority.quotation",
    subjectId: "synthetic-citation-1",
    outcome: "failed",
    reasons: ["quotation_mismatch"],
  },
  {
    checkId: "contrary.leads_reviewed",
    subjectId: null,
    outcome: "failed",
    reasons: ["contrary_leads_require_review"],
  },
];

const failedGate: GateEvaluation = {
  gate: "claim_ready",
  evaluatedAt: "2026-08-20T11:45:00.000Z",
  outcome: "failed",
  failedChecks: [
    {
      checkId: "challenge.no_defect",
      subjectId: SYNTHETIC_CLAIM_ID,
      reasons: ["challenge_defect_unresolved"],
    },
  ],
};

/** One claim with contrary support, a mismatch defect, and a blocked gate. */
export const syntheticChallengedClaim: ClaimLedgerEntry = {
  claimId: SYNTHETIC_CLAIM_ID,
  statement: "The synthetic vehicle qualifies under the synthetic statute.",
  status: "challenged",
  version: 3,
  policyRevision: SYNTHETIC_POLICY_REVISION,
  updatedAt: "2026-08-20T11:30:00.000Z",
  support: [
    supportRecord(
      "synthetic-support-1",
      "support",
      "fixture/synthetic-source-1",
    ),
  ],
  counterSupport: [
    supportRecord(
      "synthetic-counter-1",
      "counter_support",
      "fixture/synthetic-source-2",
    ),
  ],
  supportVerificationState: "failed",
  applicability: failedApplicability,
  challenges: [syntheticMismatchChallenge],
  reviewHistory: [
    {
      reviewId: "synthetic-review-1",
      reviewedAt: "2026-08-20T11:30:00.000Z",
      reviewerPrincipalId: SYNTHETIC_REVIEWER_ID,
      claimVersion: 3,
      decision: "challenge_recorded",
      note: "Quotation mismatch requires correction and renewed review.",
      policyRevision: SYNTHETIC_POLICY_REVISION,
    },
  ],
  gate: failedGate,
  stateTransitions: challengedTransitions,
};

/** One fully qualified claim: verified support, no defect, gate passed. */
export const syntheticReadyClaim: ClaimLedgerEntry = {
  claimId: SYNTHETIC_READY_CLAIM_ID,
  statement: "Synthetic notice was given within the statutory window.",
  status: "supported",
  version: 2,
  policyRevision: SYNTHETIC_POLICY_REVISION,
  updatedAt: "2026-08-20T12:00:00.000Z",
  support: [
    supportRecord(
      "synthetic-support-2",
      "support",
      "fixture/synthetic-source-3",
    ),
  ],
  counterSupport: [],
  supportVerificationState: "passed",
  applicability: [
    {
      checkId: "authority.jurisdiction",
      subjectId: "synthetic-citation-2",
      outcome: "passed",
      reasons: [],
    },
  ],
  challenges: [
    {
      challengeId: "synthetic-challenge-2",
      issuedAt: "2026-08-20T12:10:00.000Z",
      independence: "independent",
      outcome: "no_defect",
      sawChallengedConclusion: false,
      challengerProvider: "synthetic",
      challengerModelName: "challenger-model",
      challengerModelRevision: "r1",
      defects: [],
    },
  ],
  reviewHistory: [
    {
      reviewId: "synthetic-review-2",
      reviewedAt: "2026-08-20T12:15:00.000Z",
      reviewerPrincipalId: SYNTHETIC_REVIEWER_ID,
      claimVersion: 2,
      decision: "accepted",
      note: "Support and independent challenge checks passed.",
      policyRevision: SYNTHETIC_POLICY_REVISION,
    },
  ],
  gate: {
    gate: "claim_ready",
    evaluatedAt: "2026-08-20T12:15:00.000Z",
    outcome: "passed",
    failedChecks: [],
  },
  stateTransitions: [
    {
      fromStatus: null,
      toStatus: "proposed",
      at: "2026-08-20T09:30:00.000Z",
      version: 1,
    },
    {
      fromStatus: "proposed",
      toStatus: "supported",
      at: "2026-08-20T12:00:00.000Z",
      version: 2,
    },
  ],
};

export function syntheticClaimLedger(overrides?: {
  claims?: readonly ClaimLedgerEntry[];
}): ClaimLedger {
  return {
    matterId: SYNTHETIC_CORPUS_MATTER_ID,
    claims: overrides?.claims ?? [
      syntheticChallengedClaim,
      syntheticReadyClaim,
    ],
  };
}
