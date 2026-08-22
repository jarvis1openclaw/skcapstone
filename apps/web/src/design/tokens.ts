/**
 * SKLegal design tokens.
 *
 * Source of truth for the design language approved in
 * docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md section 16:
 * warm near-black base, parchment-neutral surfaces, a restrained copper
 * accent, and clear status colors. Values here are mirrored into
 * src/styles.css as CSS custom properties; tokens.test.ts asserts the two
 * stay in sync and that every text pair meets WCAG AA contrast.
 */

export const color = {
  /** Warm near-black base ink and dark surfaces. */
  ink: "#1c1917",
  inkSoft: "#44403c",
  /** Parchment-neutral surfaces. */
  parchment: "#faf6ef",
  surface: "#f3ecdf",
  surfaceSunken: "#eae0cd",
  border: "#d8cbb6",
  /** Restrained copper accent, dark enough for AA text on parchment. */
  copper: "#9a3412",
  copperStrong: "#7c2d12",
  /** Focus ring, always visible against light and dark surfaces. */
  focusRing: "#1d4ed8",
  /** Inverse text for dark surfaces. */
  inkInverse: "#faf6ef",
} as const;

export type StatusTone =
  "positive" | "caution" | "critical" | "info" | "neutral";

export interface StatusPresentation {
  /** Human readable label; status is never conveyed by color alone. */
  label: string;
  /** Non-color text glyph rendered before the label. */
  glyph: string;
  tone: StatusTone;
}

export const toneColors: Record<
  StatusTone,
  { foreground: string; background: string; border: string }
> = {
  positive: { foreground: "#166534", background: "#dcfce7", border: "#166534" },
  caution: { foreground: "#92400e", background: "#fef3c7", border: "#92400e" },
  critical: { foreground: "#991b1b", background: "#fee2e2", border: "#991b1b" },
  info: { foreground: "#1e40af", background: "#dbeafe", border: "#1e40af" },
  neutral: { foreground: "#44403c", background: "#e7e5e4", border: "#78716c" },
};

/** Matter lifecycle status semantics (domain MatterStatus values). */
export const matterStatus = {
  proposed: { label: "Proposed", glyph: "\u25CB", tone: "info" },
  open: { label: "Open", glyph: "\u25CF", tone: "positive" },
  on_hold: { label: "On hold", glyph: "\u275A\u275A", tone: "caution" },
  closed: { label: "Closed", glyph: "\u25A0", tone: "neutral" },
  archived: { label: "Archived", glyph: "\u25A1", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Party and record verification semantics (domain VerificationStatus). */
export const verificationStatus = {
  proposed: { label: "Proposed", glyph: "\u25CB", tone: "info" },
  verified: { label: "Verified", glyph: "\u2713", tone: "positive" },
  disputed: { label: "Disputed", glyph: "!", tone: "caution" },
  superseded: { label: "Superseded", glyph: "\u2190", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Matter event status semantics (domain MatterEventStatus values). */
export const matterEventStatus = {
  proposed: { label: "Proposed", glyph: "\u25CB", tone: "info" },
  recorded: { label: "Recorded", glyph: "\u2022", tone: "info" },
  verified: { label: "Verified", glyph: "\u2713", tone: "positive" },
  superseded: { label: "Superseded", glyph: "\u2190", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Fact review semantics (domain FactReviewStatus values). */
export const factReviewStatus = {
  source_asserted: { label: "Source asserted", glyph: "\u2022", tone: "info" },
  ambiguous: { label: "Ambiguous", glyph: "?", tone: "caution" },
  verified: { label: "Verified", glyph: "\u2713", tone: "positive" },
  superseded: { label: "Superseded", glyph: "\u2190", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/**
 * Tension semantics (domain TensionStatus values). Unresolved tensions
 * use the critical tone and are always rendered, never hidden.
 */
export const tensionStatus = {
  unresolved: { label: "Unresolved", glyph: "!", tone: "critical" },
  under_review: { label: "Under review", glyph: "\u25CB", tone: "caution" },
  resolved: { label: "Resolved", glyph: "\u2713", tone: "positive" },
  dismissed: { label: "Dismissed", glyph: "\u2190", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Evidence status semantics (domain EvidenceStatus values). */
export const evidenceItemStatus = {
  proposed: { label: "Proposed", glyph: "\u25CB", tone: "info" },
  collected: { label: "Collected", glyph: "\u2022", tone: "info" },
  verified: { label: "Verified", glyph: "\u2713", tone: "positive" },
  challenged: { label: "Challenged", glyph: "!", tone: "caution" },
  excluded: { label: "Excluded", glyph: "\u2715", tone: "critical" },
  superseded: { label: "Superseded", glyph: "\u2190", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Communication status semantics (domain CommunicationStatus values). */
export const communicationStatus = {
  recorded: { label: "Recorded", glyph: "\u2022", tone: "info" },
  draft: { label: "Draft", glyph: "\u25CB", tone: "neutral" },
  validated: { label: "Validated", glyph: "\u2022", tone: "info" },
  approved: { label: "Approved", glyph: "\u2713", tone: "positive" },
  queued: { label: "Queued", glyph: "\u2022", tone: "info" },
  dispatched: { label: "Dispatched", glyph: "\u25CF", tone: "info" },
  receipt_verified: {
    label: "Receipt verified",
    glyph: "\u2713",
    tone: "positive",
  },
  failed: { label: "Failed", glyph: "\u2715", tone: "critical" },
  cancelled: { label: "Cancelled", glyph: "\u2190", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/**
 * Approval and execution state values imported as negative states. They
 * stay visible until a human advances them through the governed flow.
 */
export const executionStateValue = {
  pending_review: { label: "Pending review", glyph: "\u2022", tone: "caution" },
  not_started: { label: "Not started", glyph: "\u25CB", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Provenance freshness semantics for the workspace source snapshot. */
export const snapshotFreshness = {
  current: { label: "Current snapshot", glyph: "\u2713", tone: "positive" },
  stale: { label: "Stale snapshot", glyph: "!", tone: "caution" },
} as const satisfies Record<string, StatusPresentation>;

/** Marker shown when a record references a source with no recorded file. */
export const missingSource: StatusPresentation = {
  label: "Source missing",
  glyph: "!",
  tone: "critical",
};

/**
 * Look up a status presentation, falling back to a neutral badge that
 * shows the raw value. Unknown states are rendered, never dropped.
 */
export function statusOrFallback(
  group: Record<string, StatusPresentation>,
  key: string,
): StatusPresentation {
  return group[key] ?? { label: key, glyph: "\u2022", tone: "neutral" };
}

/** Deadline state semantics. */
export const deadlineState = {
  met: { label: "Met", glyph: "\u2713", tone: "positive" },
  upcoming: { label: "Upcoming", glyph: "\u25CB", tone: "info" },
  due_soon: { label: "Due soon", glyph: "!", tone: "caution" },
  overdue: { label: "Overdue", glyph: "\u2715", tone: "critical" },
} as const satisfies Record<string, StatusPresentation>;

/** Approval state semantics, shown prominently per the approved design. */
export const approvalState = {
  approved: { label: "Approved", glyph: "\u2713", tone: "positive" },
  pending: { label: "Pending approval", glyph: "\u2022", tone: "caution" },
  changes_requested: {
    label: "Changes requested",
    glyph: "\u2190",
    tone: "caution",
  },
  rejected: { label: "Rejected", glyph: "\u2715", tone: "critical" },
} as const satisfies Record<string, StatusPresentation>;

/** Corpus supersession semantics for the source-span viewer. */
export const supersessionStatus = {
  current: { label: "Current", glyph: "\u2713", tone: "positive" },
  superseded: { label: "Superseded", glyph: "\u2190", tone: "caution" },
} as const satisfies Record<string, StatusPresentation>;

/** Corpus span accessibility semantics for the source-span viewer. */
export const spanAccessibility = {
  available: { label: "Source accessible", glyph: "\u2713", tone: "positive" },
  denied: { label: "Source not accessible", glyph: "\u2715", tone: "critical" },
} as const satisfies Record<string, StatusPresentation>;

/**
 * Claim ledger state semantics (domain LedgerClaimStatus values). A
 * withdrawn claim stays visible with its full history; it never vanishes.
 */
export const claimStatus = {
  proposed: { label: "Proposed", glyph: "\u25CB", tone: "info" },
  under_review: { label: "Under review", glyph: "\u2022", tone: "caution" },
  supported: { label: "Supported", glyph: "\u2713", tone: "positive" },
  challenged: { label: "Challenged", glyph: "!", tone: "critical" },
  withdrawn: { label: "Withdrawn", glyph: "-", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

/** Authority support verification semantics for a ledger claim. */
export const supportVerificationState = {
  passed: { label: "Support verified", glyph: "\u2713", tone: "positive" },
  failed: {
    label: "Support failed verification",
    glyph: "\u2715",
    tone: "critical",
  },
  missing: {
    label: "No verification recorded",
    glyph: "\u2022",
    tone: "caution",
  },
} as const satisfies Record<string, StatusPresentation>;

/** Claim gate semantics: a failed gate is blocked, never waivable. */
export const claimGateOutcome = {
  passed: { label: "CLAIM_READY", glyph: "\u2713", tone: "positive" },
  failed: { label: "Blocked", glyph: "\u2715", tone: "critical" },
} as const satisfies Record<string, StatusPresentation>;

/** Blind challenge independence semantics. */
export const challengeIndependence = {
  independent: { label: "Independent", glyph: "\u2713", tone: "positive" },
  same_model: {
    label: "Same-model challenge",
    glyph: "\u2190",
    tone: "caution",
  },
  not_blind: { label: "Not blind", glyph: "\u2190", tone: "caution" },
} as const satisfies Record<string, StatusPresentation>;

/** Blind challenge outcome semantics, defects preserved either way. */
export const challengeOutcome = {
  no_defect: { label: "No defect found", glyph: "\u2713", tone: "positive" },
  defect_found: { label: "Unresolved defect", glyph: "!", tone: "caution" },
} as const satisfies Record<string, StatusPresentation>;

/** Human claim-review decisions remain distinct from model challenges. */
export const claimReviewDecision = {
  accepted: {
    label: "Accepted by reviewer",
    glyph: "\u2713",
    tone: "positive",
  },
  changes_requested: {
    label: "Changes requested",
    glyph: "!",
    tone: "caution",
  },
  challenge_recorded: {
    label: "Challenge recorded",
    glyph: "!",
    tone: "critical",
  },
  withdrawal_confirmed: {
    label: "Withdrawal confirmed",
    glyph: "-",
    tone: "neutral",
  },
} as const satisfies Record<string, StatusPresentation>;

export const typography = {
  fontFamilyBase:
    'ui-sans-serif, system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  fontFamilyMono: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace',
  /** Modular scale in rem on a 16px root. */
  size: {
    xs: "0.75rem",
    sm: "0.875rem",
    base: "1rem",
    lg: "1.125rem",
    xl: "1.375rem",
    "2xl": "1.75rem",
  },
  weight: { regular: 400, medium: 500, semibold: 600, bold: 700 },
  lineHeight: { tight: 1.25, base: 1.5, relaxed: 1.65 },
} as const;

/** Spacing scale in rem, 4px base unit. */
export const spacing = {
  "0": "0",
  "1": "0.25rem",
  "2": "0.5rem",
  "3": "0.75rem",
  "4": "1rem",
  "6": "1.5rem",
  "8": "2rem",
  "12": "3rem",
} as const;

/**
 * Responsive breakpoints as min-width in px. Layout mode changes are
 * decided by layoutModeForWidth so the behavior is testable without a
 * browser; styles.css mirrors the same thresholds in media queries.
 */
export const breakpoints = {
  compact: 0,
  medium: 640,
  expanded: 960,
  wide: 1280,
} as const;

/** WCAG 2.5.5 target size guidance; 44px minimum pointer target. */
export const minTouchTargetPx = 44;

export type LayoutMode = "compact" | "expanded";

/**
 * Below the expanded breakpoint the primary navigation collapses behind a
 * disclosure control and content stacks in a single column.
 */
export function layoutModeForWidth(widthPx: number): LayoutMode {
  if (!Number.isFinite(widthPx) || widthPx < 0) {
    return "compact";
  }
  return widthPx >= breakpoints.expanded ? "expanded" : "compact";
}

/**
 * Every text-on-surface pair shipped by the design system. tokens.test.ts
 * proves each pair meets WCAG AA.
 */
export const textContrastPairs: ReadonlyArray<{
  name: string;
  foreground: string;
  background: string;
  largeText?: boolean;
}> = [
  {
    name: "body on parchment",
    foreground: color.ink,
    background: color.parchment,
  },
  { name: "body on surface", foreground: color.ink, background: color.surface },
  {
    name: "soft ink on parchment",
    foreground: color.inkSoft,
    background: color.parchment,
  },
  {
    name: "soft ink on surface",
    foreground: color.inkSoft,
    background: color.surface,
  },
  {
    name: "copper link on parchment",
    foreground: color.copper,
    background: color.parchment,
  },
  {
    name: "copper link on surface",
    foreground: color.copper,
    background: color.surface,
  },
  {
    name: "inverse on ink",
    foreground: color.inkInverse,
    background: color.ink,
  },
  {
    name: "inverse on copper strong",
    foreground: color.inkInverse,
    background: color.copperStrong,
  },
  ...(
    Object.entries(toneColors) as Array<
      [StatusTone, { foreground: string; background: string }]
    >
  ).map(([tone, pair]) => ({
    name: `status tone ${tone}`,
    foreground: pair.foreground,
    background: pair.background,
  })),
];
