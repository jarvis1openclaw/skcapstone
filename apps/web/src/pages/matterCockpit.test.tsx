import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { syntheticClaimLedger } from "../testing/claims";
import { renderStatic } from "../testing/ssr";
import { syntheticWorkspace } from "../testing/workspace";
import {
  MatterCockpit,
  v2CockpitSections,
  workProductApprovalState,
} from "./MatterCockpit";

function render(): string {
  return renderStatic(
    <MatterCockpit
      workspace={syntheticWorkspace}
      claimLedger={syntheticClaimLedger()}
    />,
  );
}

describe("V2 AI-first Matter cockpit", () => {
  it("renders every reviewed V2 surface as a labelled focus target", () => {
    const html = render();
    for (const section of v2CockpitSections) {
      expect(html).toContain(`id="${section.id}"`);
    }
    expect(html.match(/class="sl-v2-surface" aria-label=/g)?.length).toBe(
      v2CockpitSections.length,
    );
    expect(html).toContain("AI Matter cockpit");
    expect(html).toContain("AI operating model");
    expect(html).toContain("Private corpus strategy map");
    expect(html).toContain("AI Work Product assembly");
    expect(html).toContain("Tasks, Deadlines, and external-action handoff");
    expect(html).toContain("Matter activity and provenance log");
    expect(v2CockpitSections).toHaveLength(18);
    expect(html.indexOf('id="decision"')).toBeLessThan(
      html.indexOf('id="ai-operating-model"'),
    );
    expect(html.indexOf('id="ai-operating-model"')).toBeLessThan(
      html.indexOf('id="corpus-map"'),
    );
    expect(html.indexOf('id="corpus-map"')).toBeLessThan(
      html.indexOf('id="ai-cockpit"'),
    );
  });

  it("uses authorized Matter, Claim, Evidence Item, audit, and provenance data", () => {
    const html = render();
    expect(html).toContain(syntheticWorkspace.matter.title);
    expect(html).toContain(syntheticWorkspace.matter.summary);
    expect(html).toContain(syntheticWorkspace.provenance.sourceSnapshot);
    expect(html).toContain("Synthetic executed agreement");
    expect(html).toContain(
      "The synthetic vehicle qualifies under the synthetic statute.",
    );
    expect(html).toContain("pilot_mapping_review.approved");
    expect(html).toContain("Synthetic demand letter");
  });

  it("keeps every missing contract inert and explicitly unavailable", () => {
    const html = render();
    expect(html).toContain("safely-unavailable");
    expect(html).toContain("Recommendation schema is not mounted");
    expect(html).toContain("No model request is sent");
    expect(html).toContain("not running");
    expect(html).toContain("disabled");
    expect(html).toContain("Ranked recommendation queue unavailable");
    expect(html).toContain("Blind challenge and human disposition");
    expect(html).toContain("Course instruction: not evaluated");
    expect(html).toContain("Current Authority: not evaluated");
    expect(html).not.toContain("Top ranked proposals");
    expect(html).not.toContain("Proof coverage %");
    expect(html).not.toContain("provider.example");
    expect(html).not.toContain("Authorization: Bearer");
  });

  it("keeps source classes separate and states the proposal equation", () => {
    const html = render();
    expect(html).toContain("Course instruction");
    expect(html).toContain("Current Authority");
    expect(html).toContain("Matter record");
    expect(html).toContain("model inference");
    expect(html).toContain("ranked typed proposal");
    expect(html).toContain("No lane silently");
  });

  it("preserves legal-domain vocabulary and simulation-only effects", () => {
    const html = render();
    expect(html).toContain("Fact Assertions");
    expect(html).toContain("Evidence Items");
    expect(html).toContain("Work Product");
    expect(html).toContain("human decision required");
    expect(html).toContain("external effects");
    expect(html).not.toContain(">Problem<");
    expect(html).not.toContain(">Incident<");
  });

  it("defines coherent expanded and compact responsive layouts", () => {
    const stylesPath = fileURLToPath(new URL("../styles.css", import.meta.url));
    const styles = readFileSync(stylesPath, "utf8");
    expect(styles).toContain(".sl-v2-layout");
    expect(styles).toContain("grid-template-columns: 13rem minmax(0, 1fr)");
    expect(styles).toContain("@media (max-width: 959.98px)");
    expect(styles).toContain("@media (max-width: 639.98px)");
    expect(styles).toContain(
      "grid-template-columns: repeat(2, minmax(0, 1fr))",
    );
    expect(styles).toContain(
      '.sl-v2-layout > .sl-matter-nav[data-expanded="true"] ul',
    );
  });

  it("wraps and keyboard-selects exact identifiers for compact copying", () => {
    const html = render();
    const stylesPath = fileURLToPath(new URL("../styles.css", import.meta.url));
    const styles = readFileSync(stylesPath, "utf8");
    const workProduct = syntheticWorkspace.workProducts[0]!;
    const currentHash = workProduct.currentVersion.contentSha256;

    expect(currentHash).toHaveLength(64);
    expect(html).toContain(`aria-label="${workProduct.title} content hash"`);
    expect(html).toMatch(
      new RegExp(`class="sl-hash" tabindex="0"[^>]*>${currentHash}</code>`),
    );
    expect(html.match(/<code(?![^>]*class="sl-hash")/g)).toBeNull();
    expect(styles).toMatch(
      /\.sl-hash\s*\{[^}]*word-break:\s*break-all;[^}]*user-select:\s*all;/s,
    );
  });

  it("renders explicit empty Evidence Item and activity states", () => {
    const emptyWorkspace = {
      ...syntheticWorkspace,
      evidence: [],
      audit: [],
    };
    const html = renderStatic(
      <MatterCockpit
        workspace={emptyWorkspace}
        claimLedger={syntheticClaimLedger()}
      />,
    );
    expect(html).toContain(
      "The authorized API returned no Evidence Items for this Matter.",
    );
    expect(html).toContain(
      "The authorized API returned no Matter activity entries.",
    );
  });

  it("treats Approval as current only for an exact version and hash binding", () => {
    const stale = syntheticWorkspace.workProducts[0]!;
    expect(workProductApprovalState(stale)).toBe("invalidated");
    expect(workProductApprovalState({ ...stale, approvalBinding: null })).toBe(
      "unbound",
    );
    expect(
      workProductApprovalState({
        ...stale,
        approvalBinding: {
          versionId: stale.currentVersion.versionId,
          versionNumber: stale.currentVersion.versionNumber,
          contentSha256: stale.currentVersion.contentSha256,
        },
      }),
    ).toBe("current");
    expect(
      workProductApprovalState({
        ...stale,
        approvalBinding: {
          versionId: stale.currentVersion.versionId,
          versionNumber: stale.currentVersion.versionNumber,
          contentSha256: "0".repeat(64),
        },
      }),
    ).toBe("invalidated");
  });
});
