import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MatterActivity } from "./MatterActivity";
import type { MatterActivityPage } from "./types";

const digest = "a".repeat(64);
const page: MatterActivityPage = {
  version: "sklegal-matter-activity/v1",
  tenantId: "10000000-0000-4000-8000-000000000001",
  matterId: "20000000-0000-4000-8000-000000000001",
  nextCursor: "synthetic-cursor",
  snapshotSequence: 11,
  snapshotSha256: digest,
  watermark: {
    projection: "matter_activity.v1",
    tenantHeadSequence: 13,
    tenantHeadSha256: "b".repeat(64),
    projectedSequence: 11,
    projectedEventSha256: digest,
    lagEvents: 2,
    verifiedAt: "2026-08-23T12:12:00Z",
  },
  items: [
    {
      activityId: "52000000-0000-4000-8000-000000000010",
      tenantId: "10000000-0000-4000-8000-000000000001",
      matterId: "20000000-0000-4000-8000-000000000001",
      eventSequence: 10,
      eventSha256: digest,
      previousEventSha256: "c".repeat(64),
      chainStatus: "verified",
      action: "matter_activity.synthetic.correction",
      boundary: "human",
      outcome: "success",
      reasonCode: "synthetic_fixture",
      occurredAt: "2026-08-23T12:10:00Z",
      recordedAt: "2026-08-23T12:10:01Z",
      actorPrincipalId: "30000000-0000-4000-8000-000000000001",
      authorizationDecisionId: "53000000-0000-4000-8000-000000000010",
      policyDecisionId: "54000000-0000-4000-8000-000000000010",
      trace: {
        correlationId: "55000000-0000-4000-8000-000000000001",
        causationId: "52000000-0000-4000-8000-000000000009",
        runId: "56000000-0000-4000-8000-000000000001",
        workflowReferenceId: null,
        agentRunId: null,
        toolCallId: null,
      },
      source: {
        kind: "correction",
        sourceId: "51000000-0000-4000-8000-000000000010",
        sourceVersion: 1,
        sourceSha256: "d".repeat(64),
        status: "recorded",
        recordedAt: "2026-08-23T12:10:00Z",
        correctsSourceId: "51000000-0000-4000-8000-000000000007",
        supersededBySourceId: null,
        supersededBySourceVersion: null,
      },
    },
  ],
};

describe("MatterActivity", () => {
  it("renders verified chronology, provenance, correction, and truthful lag", () => {
    const html = renderToStaticMarkup(
      <MatterActivity
        state={{ status: "success", page }}
        onLoadMore={() => undefined}
      />,
    );
    expect(html).toContain("Matter activity and provenance log");
    expect(html).toContain("Verified Tenant chain 11 of 13");
    expect(html).toContain("Projection lag: 2 events");
    expect(html).toContain('data-source-kind="correction"');
    expect(html).toContain("Corrects source");
    expect(html).toContain("Audit and provenance");
    expect(html).toContain("never rewrite prior history");
  });

  it("shows a hashed proposal without Approval or dispatch authority", () => {
    const html = renderToStaticMarkup(
      <MatterActivity
        state={{ status: "success", page }}
        onProposeExport={() => undefined}
        exportProposal={{
          proposalId: "62000000-0000-4000-8000-000000000001",
          status: "proposed",
          title: "Synthetic Matter activity export",
          itemCount: 1,
          selectionSha256: "e".repeat(64),
          contentSha256: "f".repeat(64),
          approvalId: null,
          dispatchState: "not_requested",
        }}
      />,
    );
    expect(html).toContain("Work Product proposal only");
    expect(html).toContain("no Approval");
    expect(html).toContain("not_requested");
    expect(html).toContain("No send, file, service, email, calendar");
  });

  it("renders empty and sanitized unavailable states with inert actions", () => {
    const empty = renderToStaticMarkup(
      <MatterActivity
        state={{ status: "success", page: { ...page, items: [] } }}
      />,
    );
    expect(empty).toContain("no Matter activity entries");
    expect(empty.match(/disabled=""/g)?.length).toBe(2);

    const unavailable = renderToStaticMarkup(
      <MatterActivity
        state={{
          status: "unavailable",
          correlationId: "70000000-0000-4000-8000-000000000001",
        }}
      />,
    );
    expect(unavailable).toContain('role="alert"');
    expect(unavailable).toContain("temporarily unavailable");
    expect(unavailable).not.toContain("database");
    expect(unavailable).not.toContain("token");
    expect(unavailable).toContain("disabled");
  });
});
