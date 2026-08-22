import { describe, expect, it } from "vitest";

import type { WorkspaceWorkProduct } from "../api/types";
import { syntheticWorkspace } from "../testing/workspace";
import { renderStatic } from "../testing/ssr";
import {
  approvalIsInvalidated,
  WorkProductDraftingSection,
} from "./WorkProductDrafting";

const syntheticProduct = syntheticWorkspace.workProducts[0]!;

function render(workProduct: WorkspaceWorkProduct = syntheticProduct): string {
  return renderStatic(
    <WorkProductDraftingSection workProducts={[workProduct]} />,
  );
}

describe("claim-grounded drafting", () => {
  it("renders every factual sentence with its claim grounding state", () => {
    const html = render();
    expect(html).toContain("Factual sentence grounding");
    expect(html).toContain('data-grounding-status="grounded"');
    expect(html).toContain("Claim ledger entry");
    expect(html).toContain(
      'href="#claim-c0000000-0000-4000-8000-000000000001"',
    );
    expect(html).toContain("The vehicle stalled during ordinary operation.");
  });

  it("warns about an ungrounded factual sentence and blocks readiness", () => {
    const html = render();
    expect(html).toContain('data-grounding-status="ungrounded"');
    expect(html).toContain("Ungrounded sentence");
    expect(html).toContain(
      "Factual sentence is not grounded in a claim ledger entry.",
    );
    expect(html).toContain("DRAFT_READY is blocked: 2 factual sentences");
    expect(html.match(/role="alert"/g)?.length).toBeGreaterThanOrEqual(2);
  });

  it("renders bracketed unknowns in place without hiding their text", () => {
    const html = render();
    expect(html).toContain('data-unknown="unresolved"');
    expect(html).toContain("[?purchase_date: unresolved tension]");
    expect(html).toContain("Unknown unresolved");
    expect(html).toContain("Grounding deferred until");
  });
});

describe("version compare", () => {
  it("renders unchanged, removed, and added sentence rows", () => {
    const html = render();
    expect(html).toContain("Version compare v2 to v3");
    expect(html).toContain('data-change="unchanged"');
    expect(html).toContain('data-change="removed"');
    expect(html).toContain('data-change="added"');
    expect(html).toContain("Notice was sent.");
    expect(html).toContain("Notice was timely.");
  });
});

describe("changed-after-approval invalidation", () => {
  it("detects and renders an exact-version approval mismatch", () => {
    expect(approvalIsInvalidated(syntheticProduct)).toBe(true);
    const html = render();
    expect(html).toContain("Approval invalidated");
    expect(html).toContain("recorded Approval names v2");
    expect(html).toContain("no longer matches v3");
    expect(html).toContain("Re-validation and a new exact-version Approval");
    expect(html).toContain("Approval hash");
    expect(html).toContain("current hash");
  });

  it("keeps an exact matching approval valid", () => {
    const current = syntheticProduct.currentVersion;
    const matching: WorkspaceWorkProduct = {
      ...syntheticProduct,
      approvalBinding: {
        versionId: current.versionId,
        versionNumber: current.versionNumber,
        contentSha256: current.contentSha256,
      },
    };
    expect(approvalIsInvalidated(matching)).toBe(false);
    const html = render(matching);
    expect(html).toContain("Approved");
    expect(html).not.toContain("Approval invalidated");
  });

  it("does not call an absent approval invalidated", () => {
    const pending: WorkspaceWorkProduct = {
      ...syntheticProduct,
      approvalBinding: null,
    };
    expect(approvalIsInvalidated(pending)).toBe(false);
    const html = render(pending);
    expect(html).toContain("Pending approval");
    expect(html).not.toContain("Approval invalidated");
  });
});

describe("empty Documents section", () => {
  it("keeps the section and its explicit no-record state", () => {
    const html = renderStatic(<WorkProductDraftingSection workProducts={[]} />);
    expect(html).toContain('id="work-products"');
    expect(html).toContain("No work products are recorded for this matter.");
  });
});
