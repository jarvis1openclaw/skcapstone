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

/** Matter lifecycle status semantics. */
export const matterStatus = {
  active: { label: "Active", glyph: "\u25CF", tone: "positive" },
  on_hold: { label: "On hold", glyph: "\u275A\u275A", tone: "caution" },
  closed: { label: "Closed", glyph: "\u25A0", tone: "neutral" },
} as const satisfies Record<string, StatusPresentation>;

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
