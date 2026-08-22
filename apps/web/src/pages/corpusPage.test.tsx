/**
 * Corpus research rendering tests (SKL-S4-03A): governed search bar with
 * explicit scope chips, the full retrieval trace, citation navigation to
 * the exact source-span viewer, the inaccessible-source denial state,
 * stale projection generation display, legal terminology, and
 * accessibility.
 */

import type { AnchorHTMLAttributes, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: (
    props: AnchorHTMLAttributes<HTMLAnchorElement> & {
      to: string;
      activeProps?: AnchorHTMLAttributes<HTMLAnchorElement>;
      activeOptions?: { exact?: boolean };
      children?: ReactNode;
    },
  ) => {
    const { to, children, ...rest } = props;
    delete (rest as Record<string, unknown>).activeProps;
    delete (rest as Record<string, unknown>).activeOptions;
    return (
      <a href={to} {...rest}>
        {children}
      </a>
    );
  },
}));

import { renderStatic } from "../testing/ssr";
import {
  SYNTHETIC_DENIED_SOURCE_ID,
  SYNTHETIC_QUERY,
  SYNTHETIC_SPAN_TEXT,
  SYNTHETIC_SOURCE_ID,
  SYNTHETIC_SOURCE_SHA,
  SYNTHETIC_TRACE_SHA,
  syntheticAvailableSpan,
  syntheticDeniedSpan,
  syntheticSearchResponse,
  syntheticTrace,
} from "../testing/corpus";
import {
  SYNTHETIC_CLAIM_ID,
  SYNTHETIC_MISMATCH_DEFECT_TEXT,
  SYNTHETIC_REVIEWER_ID,
  syntheticChallengedClaim,
  syntheticClaimLedger,
} from "../testing/claims";
import type { CorpusSpan } from "../api/types";
import {
  contrastRatio,
  WCAG_AA_LARGE_TEXT,
  WCAG_AA_NORMAL_TEXT,
} from "../design/contrast";
import { color } from "../design/tokens";
import {
  claimRecordGaps,
  ClaimLedgerView,
  CorpusResearchView,
  CorpusSpanView,
  reviewStepIndexForKey,
} from "./CorpusPage";

function render(
  span: CorpusSpan | null = syntheticAvailableSpan,
  trace = syntheticTrace(),
): string {
  return renderStatic(
    <CorpusResearchView
      query={SYNTHETIC_QUERY}
      response={syntheticSearchResponse({ trace })}
      span={span}
    />,
  );
}

describe("corpus research structure and accessibility", () => {
  it("renders the heading, search landmark, and labelled input", () => {
    const html = render();
    expect(html).toContain("<h1");
    expect(html).toContain('role="search"');
    expect(html).toContain('aria-label="Corpus search"');
    expect(html).toContain('for="corpus-search-input"');
    expect(html).toContain('id="corpus-search-input"');
    expect(html).toContain("Search the matter corpus");
  });

  it("keeps every scope chip explicit instead of silently dropping gated scopes", () => {
    const html = render();
    expect(html).toContain("This matter");
    expect(html).toContain("Active");
    expect(html).toContain("tenant_corpus");
    expect(html).toContain("official_sources");
    expect(html).toContain("Unavailable");
    expect(html).toContain(
      "Tenant corpus scope is pending retrieval policy approval.",
    );
    expect(html).toContain(
      "Official-source verification is a separate approved lane.",
    );
  });

  it("presents the surface entirely in legal terminology", () => {
    const html = render();
    expect(html).not.toContain(">Problem<");
    expect(html).not.toContain(">Incident<");
    expect(html).not.toContain("Problems");
    expect(html).not.toContain("Incidents");
  });

  it("conveys status by label and glyph, never color alone", () => {
    const html = render();
    expect(html).toContain("sl-status-glyph");
    expect(html).toContain("Current");
    expect(html).toContain("Superseded");
    expect(html).toContain("Source accessible");
  });
});

describe("retrieval trace rendering", () => {
  it("renders the full trace fields behind the results", () => {
    const html = render();
    expect(html).toContain("synthetic-release-1");
    expect(html).toContain("lexical.search.v1");
    expect(html).toContain(SYNTHETIC_TRACE_SHA);
    expect(html).toContain("lexical_rank then scope_aggregate");
    expect(html).toContain(SYNTHETIC_SOURCE_SHA);
    expect(html).toContain("Backend watermark");
    expect(html).toContain("Rank path");
  });

  it("labels corpus rows as unverified research proposals", () => {
    const html = render();
    expect(html).toContain(
      "Research proposal, not verified Authority (unverified_research_proposal).",
    );
  });
});

describe("citation navigation", () => {
  it("anchors each citation to its exact source-span viewer target", () => {
    const html = render();
    expect(html).toContain(`id="result-${SYNTHETIC_SOURCE_ID}"`);
    expect(html).toContain(`href="#span-${SYNTHETIC_SOURCE_ID}"`);
    expect(html).toContain(
      `aria-label="View exact source span for SYN 100 ILCS 1/2"`,
    );
    expect(html).toContain(`id="span-${SYNTHETIC_SOURCE_ID}"`);
    expect(html).toContain('tabindex="-1"');
  });
});

describe("exact source-span viewer", () => {
  it("renders the exact span text, locator, hash, and supersession status", () => {
    const html = render();
    expect(html).toContain(SYNTHETIC_SPAN_TEXT);
    expect(html).toContain("fixture/synthetic-source-1");
    expect(html).toContain(SYNTHETIC_SOURCE_SHA);
    expect(html).toContain("SYN 100 ILCS 1/2");
    expect(html).toContain("Synthetic Jurisdiction");
    expect(html).toContain("<mark>");
  });

  it("prompts for a selection when no span is loaded", () => {
    const html = renderStatic(
      <CorpusResearchView
        query={SYNTHETIC_QUERY}
        response={syntheticSearchResponse()}
        span={null}
      />,
    );
    expect(html).toContain("Select a result citation above");
  });
});

describe("inaccessible source denial state", () => {
  it("renders the denial without any span content leak", () => {
    const html = render(syntheticDeniedSpan);
    expect(html).toContain("Source not accessible");
    expect(html).toContain("source_not_accessible");
    expect(html).toContain("No span content is available.");
    // The exact span text never reaches the page at all.
    expect(html).not.toContain(SYNTHETIC_SPAN_TEXT);
  });

  it("carries no locator, hash, or citation in the denial view itself", () => {
    const html = renderStatic(<CorpusSpanView span={syntheticDeniedSpan} />);
    expect(html).toContain(`id="span-${SYNTHETIC_DENIED_SOURCE_ID}"`);
    // Result rows may carry trace locators by design, but the denial
    // view itself must not render locator, hashes, or the citation.
    expect(html).not.toContain("fixture/synthetic-source-1");
    expect(html).not.toContain(SYNTHETIC_SOURCE_SHA);
    expect(html).not.toContain("Exact locator");
    expect(html).not.toContain("SYN 100 ILCS 1/2");
  });
});

describe("stale projection generation display", () => {
  it("shows current generation without an alert when fresh", () => {
    const html = render();
    expect(html).toContain("Projection generation");
    expect(html).not.toContain('role="alert"');
  });

  it("raises a visible alert with both generations when stale", () => {
    const html = render(
      syntheticAvailableSpan,
      syntheticTrace({
        projectionGeneration: 3,
        currentProjectionGeneration: 4,
      }),
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("Stale projection generation");
    expect(html).toContain("Results were read from projection generation 3");
    expect(html).toContain("the current generation is 4");
  });
});

describe("claim ledger", () => {
  it("exposes support, qualification, challenge, review, and state history", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    expect(html).toContain(`id="claim-${SYNTHETIC_CLAIM_ID}"`);
    expect(html).toContain("Support");
    expect(html).toContain("Counter-support");
    expect(html).toContain("Authority applicability factors");
    expect(html).toContain("Blind challenge workflow");
    expect(html).toContain("Reviewer history");
    expect(html).toContain("Claim state transitions");
    expect(html).toContain("Support failed verification");
    expect(html).toContain("Blocked");
  });

  it("preserves contrary support beside support", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    expect(html).toContain("fixture/synthetic-source-1");
    expect(html).toContain("fixture/synthetic-source-2");
    expect(html).toContain("contrary_leads_require_review");
  });

  it("shows the mismatch defect and failed gate reason without reduction", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    expect(html).toContain(SYNTHETIC_MISMATCH_DEFECT_TEXT);
    expect(html).toContain("quotation_error");
    expect(html).toContain("quotation_mismatch");
    expect(html).toContain("challenge_defect_unresolved");
  });

  it("keeps human reviewer decisions separate from model challenges", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    expect(html).toContain("Accepted by reviewer");
    expect(html).toContain("Challenge recorded");
    expect(html).toContain(`Reviewer principal ${SYNTHETIC_REVIEWER_ID}`);
    expect(html).toContain("Quotation mismatch requires correction");
  });

  it("renders every state transition in order", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    const proposed = html.indexOf("Initially proposed");
    const underReview = html.indexOf("proposed to under_review");
    const challenged = html.indexOf("under_review to challenged");
    expect(proposed).toBeGreaterThan(-1);
    expect(underReview).toBeGreaterThan(proposed);
    expect(challenged).toBeGreaterThan(underReview);
  });

  it("renders the recorded no-answer state explicitly", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger({ claims: [] })} />,
    );
    expect(html).toContain('role="status"');
    expect(html).toContain("There is no claim answer to review.");
    expect(html).not.toContain(`id="claim-${SYNTHETIC_CLAIM_ID}"`);
  });
});

describe("blind challenge review workflow", () => {
  it("renders each challenge as a keyboard-operable disclosure with blindness checks", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    expect(html).toContain('<details open=""');
    expect(html).toContain("<summary>Challenge 1: defect_found</summary>");
    expect(html).toContain("Independence check");
    expect(html).toContain("Conclusion exposure");
    expect(html).toContain("Challenger did not see the challenged conclusion");
    expect(html).toContain("Challenge records are read-only");
  });

  it("preserves a compromised challenge as an explicit alert", () => {
    const compromised = {
      ...syntheticChallengedClaim,
      challenges: [
        {
          ...syntheticChallengedClaim.challenges[0]!,
          independence: "not_blind" as const,
          sawChallengedConclusion: true,
        },
      ],
    };
    const html = renderStatic(
      <ClaimLedgerView
        ledger={syntheticClaimLedger({ claims: [compromised] })}
      />,
    );
    expect(html).toContain("Challenger saw the challenged conclusion");
    expect(html).toContain(
      "This record does not satisfy a blind, independent challenge.",
    );
    expect(html).toContain('role="alert"');
  });
});

describe("record gaps and traceability", () => {
  const incompleteClaim = {
    ...syntheticChallengedClaim,
    support: [],
    counterSupport: [],
    applicability: [],
    challenges: [],
    reviewHistory: [],
    gate: null,
    stateTransitions: [],
  };

  it("derives every missing review record without changing the claim", () => {
    expect(claimRecordGaps(incompleteClaim).map((gap) => gap.key)).toEqual([
      "support_missing",
      "applicability_missing",
      "blind_challenge_missing",
      "human_review_missing",
      "claim_gate_missing",
      "state_history_missing",
    ]);
  });

  it("surfaces record gaps and withholds an untraceable summary-only answer", () => {
    const html = renderStatic(
      <ClaimLedgerView
        ledger={syntheticClaimLedger({ claims: [incompleteClaim] })}
      />,
    );
    expect(html).toContain("Untraceable claim statement withheld");
    expect(html).toContain(
      "this interface will not render a summary-only claim answer",
    );
    expect(html).not.toContain(syntheticChallengedClaim.statement);
    expect(html).toContain("support_missing");
    expect(html).toContain("blind_challenge_missing");
    expect(html).toContain("6 record gaps");
  });
});

describe("full keyboard review path", () => {
  it("links all ordered review steps to labelled focus targets", () => {
    const html = renderStatic(
      <ClaimLedgerView ledger={syntheticClaimLedger()} />,
    );
    const steps = [
      "summary",
      "support",
      "counter-support",
      "applicability",
      "challenges",
      "record-gaps",
      "reviewer-history",
      "state-transitions",
    ];
    expect(html).toContain('aria-label="Claim keyboard review path"');
    expect(html).toContain("Use Tab or the arrow keys");
    for (const step of steps) {
      const id = `claim-${SYNTHETIC_CLAIM_ID}-${step}`;
      expect(html).toContain(`href="#${id}"`);
      expect(html).toContain(`id="${id}"`);
    }
    expect(html.match(/data-claim-review-step="true"/g)).toHaveLength(16);
    expect(html.match(/tabindex="-1"/g)?.length).toBeGreaterThanOrEqual(16);
  });

  it("supports arrows, Home, End, wrapping, and ignores unrelated keys", () => {
    expect(reviewStepIndexForKey("ArrowRight", 2, 8)).toBe(3);
    expect(reviewStepIndexForKey("ArrowDown", 7, 8)).toBe(0);
    expect(reviewStepIndexForKey("ArrowLeft", 0, 8)).toBe(7);
    expect(reviewStepIndexForKey("ArrowUp", 3, 8)).toBe(2);
    expect(reviewStepIndexForKey("Home", 5, 8)).toBe(0);
    expect(reviewStepIndexForKey("End", 1, 8)).toBe(7);
    expect(reviewStepIndexForKey("Enter", 1, 8)).toBeNull();
    expect(reviewStepIndexForKey("ArrowRight", 0, 0)).toBeNull();
  });
});

describe("research review WCAG AA checks", () => {
  it("keeps review text, links, and focus indication above WCAG thresholds", () => {
    expect(contrastRatio(color.ink, color.surface)).toBeGreaterThanOrEqual(
      WCAG_AA_NORMAL_TEXT,
    );
    expect(contrastRatio(color.copper, color.parchment)).toBeGreaterThanOrEqual(
      WCAG_AA_NORMAL_TEXT,
    );
    expect(
      contrastRatio(color.focusRing, color.parchment),
    ).toBeGreaterThanOrEqual(WCAG_AA_LARGE_TEXT);
  });
});
