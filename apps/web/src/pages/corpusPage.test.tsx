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
import type { CorpusSpan } from "../api/types";
import { CorpusResearchView, CorpusSpanView } from "./CorpusPage";

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
