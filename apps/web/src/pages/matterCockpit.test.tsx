import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { syntheticClaimLedger } from "../testing/claims";
import { renderStatic } from "../testing/ssr";
import { syntheticWorkspace } from "../testing/workspace";
import { MatterCockpit, v2CockpitSections } from "./MatterCockpit";

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
  });
});
