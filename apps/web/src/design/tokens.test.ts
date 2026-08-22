import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  contrastRatio,
  WCAG_AA_LARGE_TEXT,
  WCAG_AA_NORMAL_TEXT,
} from "./contrast";
import {
  approvalState,
  breakpoints,
  color,
  communicationStatus,
  deadlineState,
  evidenceItemStatus,
  executionStateValue,
  factReviewStatus,
  layoutModeForWidth,
  matterEventStatus,
  matterStatus,
  minTouchTargetPx,
  snapshotFreshness,
  statusOrFallback,
  tensionStatus,
  textContrastPairs,
  verificationStatus,
} from "./tokens";

describe("contrast helpers", () => {
  it("computes the WCAG reference ratio for black on white", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
  });

  it("computes 1 for identical colors", () => {
    expect(contrastRatio("#1c1917", "#1c1917")).toBeCloseTo(1, 5);
  });
});

describe("WCAG AA token contrast", () => {
  it("declares at least the core body, link, inverse, and status pairs", () => {
    expect(textContrastPairs.length).toBeGreaterThanOrEqual(12);
  });

  it.each(textContrastPairs.map((pair) => [pair.name, pair] as const))(
    "%s meets WCAG AA",
    (_name, pair) => {
      const minimum =
        pair.largeText === true ? WCAG_AA_LARGE_TEXT : WCAG_AA_NORMAL_TEXT;
      expect(
        contrastRatio(pair.foreground, pair.background),
      ).toBeGreaterThanOrEqual(minimum);
    },
  );
});

describe("status semantics", () => {
  const groups = {
    matterStatus,
    deadlineState,
    approvalState,
    verificationStatus,
    matterEventStatus,
    factReviewStatus,
    tensionStatus,
    evidenceItemStatus,
    communicationStatus,
    executionStateValue,
    snapshotFreshness,
  };

  it.each(
    Object.entries(groups).flatMap(([group, statuses]) =>
      Object.entries(statuses).map(
        ([key, status]) => [`${group}.${key}`, status] as const,
      ),
    ),
  )(
    "%s conveys meaning by text and glyph, not color alone",
    (_name, status) => {
      expect(status.label.trim().length).toBeGreaterThan(0);
      expect(status.glyph.trim().length).toBeGreaterThan(0);
    },
  );
});

describe("statusOrFallback", () => {
  it("returns the declared presentation for known keys", () => {
    expect(statusOrFallback(tensionStatus, "unresolved").label).toBe(
      "Unresolved",
    );
  });

  it("renders unknown states as a neutral badge instead of dropping them", () => {
    const fallback = statusOrFallback(tensionStatus, "unexpected_state");
    expect(fallback.label).toBe("unexpected_state");
    expect(fallback.tone).toBe("neutral");
    expect(fallback.glyph.trim().length).toBeGreaterThan(0);
  });
});

describe("responsive breakpoints", () => {
  it("orders breakpoints by ascending min-width", () => {
    const values = [
      breakpoints.compact,
      breakpoints.medium,
      breakpoints.expanded,
      breakpoints.wide,
    ];
    const sorted = [...values].sort((a, b) => a - b);
    expect(values).toEqual(sorted);
  });

  it("requires a pointer target of at least 44px", () => {
    expect(minTouchTargetPx).toBeGreaterThanOrEqual(44);
  });
});

describe("styles.css token sync", () => {
  const css = readFileSync(
    fileURLToPath(new URL("../styles.css", import.meta.url)),
    "utf8",
  );

  it("contains every core color token", () => {
    for (const hex of Object.values(color)) {
      expect(css).toContain(hex);
    }
  });

  it("mirrors the expanded and medium breakpoints as media queries", () => {
    expect(css).toContain(`min-width: ${breakpoints.expanded}px`);
    expect(css).toContain(`min-width: ${breakpoints.medium}px`);
    expect(css).toContain(`max-width: ${breakpoints.expanded - 0.02}px`);
  });
});

describe("layoutModeForWidth", () => {
  it("is compact below the expanded breakpoint and expanded at it", () => {
    expect(layoutModeForWidth(breakpoints.expanded - 1)).toBe("compact");
    expect(layoutModeForWidth(breakpoints.expanded)).toBe("expanded");
    expect(layoutModeForWidth(breakpoints.wide)).toBe("expanded");
  });

  it("fails closed to compact for invalid input", () => {
    expect(layoutModeForWidth(Number.NaN)).toBe("compact");
    expect(layoutModeForWidth(-1)).toBe("compact");
  });
});
