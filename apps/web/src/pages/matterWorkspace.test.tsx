/**
 * Matter workspace rendering tests (SKL-S4-02): tension rendering,
 * version history, missing source, stale snapshot, negative execution
 * states, deep links, legal terminology, and accessibility.
 */

import { describe, expect, it } from "vitest";

import { renderStatic } from "../testing/ssr";
import { syntheticWorkspace } from "../testing/workspace";
import { syntheticClaimLedger } from "../testing/claims";
import type { MatterWorkspace } from "../api/types";
import {
  MatterWorkspaceView,
  matterWorkspaceSections,
} from "./MatterWorkspace";

function render(workspace: MatterWorkspace = syntheticWorkspace): string {
  return renderStatic(
    <MatterWorkspaceView
      workspace={workspace}
      claimLedger={syntheticClaimLedger()}
    />,
  );
}

describe("matter workspace structure and accessibility", () => {
  it("renders heading hierarchy and labelled landmarks", () => {
    const html = render();
    expect(html).toContain("<h1");
    expect(html).toContain('aria-label="Matter workspace"');
    for (const section of matterWorkspaceSections) {
      expect(html).toContain(`id="${section.id}"`);
      expect(html).toContain(`aria-label="${section.label}"`);
      expect(html).toContain(`href="#${section.id}"`);
    }
  });

  it("marks sections focusable for anchor navigation", () => {
    const html = render();
    expect(html).toContain('id="overview"');
    expect(html).toContain('tabindex="-1"');
  });

  it("conveys status by label and glyph, never color alone", () => {
    const html = render();
    // Every badge carries its text label next to the glyph span.
    expect(html).toContain("sl-status-glyph");
    expect(html).toContain("Open");
    expect(html).toContain("Source asserted");
  });

  it("exposes stable deep-link anchors for sections and records", () => {
    const html = render();
    expect(html).toContain('id="tension-date_or_deadline"');
    expect(html).toContain('href="#tension-date_or_deadline"');
    expect(html).toContain('id="fact-70000000-0000-4000-8000-0000000000f1"');
    expect(html).toContain('href="#fact-70000000-0000-4000-8000-0000000000f1"');
    expect(html).toContain('id="event-60000000-0000-4000-8000-0000000000e1"');
    expect(html).toContain(
      'id="evidence-80000000-0000-4000-8000-0000000000e9"',
    );
    expect(html).toContain(
      'id="communication-90000000-0000-4000-8000-0000000000c1"',
    );
    expect(html).toContain('id="version-1"');
    expect(html).toContain('id="audit-audit-1"');
    expect(html).toContain(
      'aria-label="Deep link to tension group date_or_deadline"',
    );
  });

  it("presents the matter entirely in legal terminology", () => {
    const html = render();
    expect(html).not.toContain(">Problem<");
    expect(html).not.toContain(">Incident<");
    expect(html).not.toContain("Problems");
    expect(html).not.toContain("Incidents");
    expect(html).toContain("Matter Event");
  });
});

describe("tension rendering", () => {
  it("keeps unresolved tensions visible with review-required markers", () => {
    const html = render();
    expect(html).toContain("Tension: date_or_deadline");
    expect(html).toContain("Unresolved");
    expect(html).toContain("Review required");
    // Member assertions render inside the tension group.
    expect(html).toContain("closing_date: 2099-02-01");
    expect(html).toContain("response_due_date: 2099-02-10");
  });

  it("links tension member assertions to their fact anchors", () => {
    const html = render();
    expect(html).toContain('href="#fact-70000000-0000-4000-8000-0000000000f2"');
  });
});

describe("negative execution states", () => {
  it("renders pending approval and not-started execution states", () => {
    const html = render();
    expect(html).toContain("Approval and execution states");
    expect(html).toContain("Pending review");
    expect(html).toContain("Not started");
    expect(html).toContain("Matter Approval");
    expect(html).toContain("Matter Event Execution");
  });
});

describe("version history", () => {
  it("marks the current review baseline and historical versions", () => {
    const html = render();
    expect(html).toContain("Packet version 1");
    expect(html).toContain("Packet version 2");
    expect(html).toContain("Historical");
    expect(html).toContain("Current review baseline");
  });
});

describe("missing source handling", () => {
  it("flags assertions whose source is not recorded", () => {
    const html = render();
    expect(html).toContain("Source missing");
    expect(html).toContain("trust_name");
  });

  it("renders the record gap for the missing source", () => {
    const html = render();
    expect(html).toContain("Record gaps");
    expect(html).toContain(
      "A fact assertion references a source that has no recorded source file.",
    );
  });
});

describe("stale snapshot handling", () => {
  it("shows current freshness when snapshots match", () => {
    const html = render();
    expect(html).toContain("Current snapshot");
    expect(html).not.toContain("The recorded source snapshot is stale");
  });

  it("raises a visible alert when the snapshot is stale", () => {
    const stale: MatterWorkspace = {
      ...syntheticWorkspace,
      provenance: {
        ...syntheticWorkspace.provenance,
        currentSourceSnapshot: "synthetic-snapshot-2",
        stale: true,
      },
    };
    const html = render(stale);
    expect(html).toContain("Stale snapshot");
    expect(html).toContain('role="alert"');
    expect(html).toContain("synthetic-snapshot-2");
  });
});

describe("workspace sections", () => {
  it("renders parties with verification status", () => {
    const html = render();
    expect(html).toContain("Synthetic Party");
    expect(html).toContain("counterparty");
  });

  it("renders timeline events with occurrence and observation times", () => {
    const html = render();
    expect(html).toContain("Synthetic transaction review activity");
    expect(html).toContain("occurred 2099-01-01T00:00:00Z");
    expect(html).toContain("observed 2099-01-02T03:04:05Z");
    expect(html).toContain("Legacy reference: synthetic-legacy-activity-1");
  });

  it("renders evidence with its content hash", () => {
    const html = render();
    expect(html).toContain("Synthetic executed agreement");
    expect(html).toContain("a".repeat(64));
  });

  it("renders communications and audit entries", () => {
    const html = render();
    expect(html).toContain("OWNER-DIRECTIONS.md");
    expect(html).toContain("pilot_mapping_review.approved");
    expect(html).toContain("Synthetic Human Reviewer");
  });

  it("states explicitly when a section has no records", () => {
    const empty: MatterWorkspace = {
      ...syntheticWorkspace,
      parties: [],
      timeline: [],
      evidence: [],
      communications: [],
    };
    const html = render(empty);
    expect(html).toContain("No verified parties are recorded for this matter.");
    expect(html).toContain("No matter events are recorded for this matter.");
    expect(html).toContain("No evidence items are recorded for this matter.");
    expect(html).toContain("No communications are recorded for this matter.");
  });

  it("renders the integrated claims, Authority support, and Documents editor", () => {
    const html = render();
    expect(html).toContain('id="issues-and-claims"');
    expect(html).toContain("The vehicle stalled during ordinary operation.");
    expect(html).toContain("Counter-support");
    expect(html).toContain("research record");
    expect(html).toContain('id="work-products"');
    expect(html).toContain("Claim-grounded editor");
    expect(html).toContain("Version compare v2 to v3");
    expect(html).toContain("MVP feature matrix");
    expect(html).toContain("safely-unavailable");
    expect(html).toContain("post-mvp");
  });

  it("does not invent Claims when the ledger is unavailable", () => {
    const html = renderStatic(
      <MatterWorkspaceView workspace={syntheticWorkspace} />,
    );
    expect(html).toContain(
      "Safely unavailable: the claim ledger did not return an authorized answer.",
    );
    expect(html).not.toContain(
      "The synthetic vehicle qualifies under the synthetic statute.",
    );
  });
});
