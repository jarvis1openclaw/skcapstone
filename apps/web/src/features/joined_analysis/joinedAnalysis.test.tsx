import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { JoinedAnalysisPanel } from "./JoinedAnalysisPanel";
import { JoinedAnalysisError, loadJoinedAnalysis } from "./client";
import { MatterAnalysisRead, parseMatterAnalysisRead } from "./contract";

function camel(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(camel);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, nested]) => [
      key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase()),
      camel(nested),
    ]),
  );
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nested]) => `${JSON.stringify(key)}:${canonical(nested)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256(value: unknown): string {
  return createHash("sha256").update(canonical(value)).digest("hex");
}

function fixture(): MatterAnalysisRead {
  const path = fileURLToPath(
    new URL(
      "../../../../../tests/fixtures/mvp/fragments/joined_analysis/public-synthetic-v1.json",
      import.meta.url,
    ),
  );
  const template = JSON.parse(readFileSync(path, "utf8")) as Record<
    string,
    unknown
  >;
  const snapshot = template.snapshot as Record<string, unknown>;
  snapshot.matter_snapshot_sha256 = sha256({
    classification: template.classification,
    forum: template.forum,
    proceedings: template.proceedings,
    issues: template.issues,
    fact_assertions: template.fact_assertions,
    evidence_items: template.evidence_items,
  });
  snapshot.claim_projection_revision = sha256({
    theories: template.theories,
    elements: template.elements,
  });
  snapshot.authority_snapshot = sha256(template.authorities);
  snapshot.projection_revision = createHash("sha256")
    .update(template.schema_revision as string)
    .digest("hex");
  snapshot.projection_sha256 = sha256(template);
  const projection = camel(template) as Record<string, unknown>;
  return parseMatterAnalysisRead({
    ...projection,
    policyIdentity: {
      authorizationDecisionId: "91000000-0000-4000-8000-000000000090",
      principalId: "91000000-0000-4000-8000-000000000004",
      capability: "claim.review",
      purpose: "claim_review",
      verifierPolicyVersion: "sklegal-authz/v1",
      principalPolicyRevisions: ["8".repeat(64)],
      trustedIssuerPolicyRevision: "9".repeat(64),
      revocationRevision: "a".repeat(64),
    },
    page: { limit: 50, returned: 2, hasMore: false, nextCursor: null },
  });
}

describe("joined Matter analysis feature contract", () => {
  it("accepts the exact public fixture and rejects Claim split truth", () => {
    const analysis = fixture();
    expect(analysis.theories.map((item) => item.theoryKind)).toEqual([
      "claim",
      "defense",
    ]);
    const mutated = structuredClone(analysis);
    mutated.theories[0]!.ledgerProjection!.projectedClaimVersion += 1;
    expect(() => parseMatterAnalysisRead(mutated)).toThrow(
      "joined analysis response is invalid",
    );
    const claim = analysis.theories[0]!;
    expect(claim.status).toBe("accepted");
    expect(claim.ledgerProjection!.projectedClaimStatus).toBe("accepted");
    expect(claim.ledgerProjection!.legacyMigration).toEqual({
      legacyLedgerClaimId: "91000000-0000-4000-8000-000000000033",
      legacyStatus: "supported",
      mappedClaimStatus: "accepted",
      mappingRevision: "ledger-claim-to-claim-v1",
    });
  });

  it.each(["accepted", "rejected"] as const)(
    "rejects canonical status %s as a legacy ledger status",
    (legacyStatus) => {
      const mutated = structuredClone(fixture());
      const migration = mutated.theories[0]!.ledgerProjection!
        .legacyMigration as unknown as Record<string, unknown>;
      migration.legacyStatus = legacyStatus;
      expect(() => parseMatterAnalysisRead(mutated)).toThrow();
    },
  );

  it("requires the exact supported to accepted legacy mapping", () => {
    const legacy = structuredClone(fixture());
    legacy.theories[0]!.ledgerProjection!.legacyMigration!.legacyStatus =
      "under_review";
    expect(() => parseMatterAnalysisRead(legacy)).toThrow();

    const canonical = structuredClone(fixture());
    canonical.theories[0]!.status = "under_review";
    canonical.theories[0]!.ledgerProjection!.projectedClaimStatus =
      "under_review";
    canonical.theories[0]!.ledgerProjection!.legacyMigration = null;
    expect(parseMatterAnalysisRead(canonical).theories[0]!.status).toBe(
      "under_review",
    );
  });

  it.each(["accepted", "rejected"] as const)(
    "rejects terminal canonical status %s without a legacy mapping",
    (claimStatus) => {
      const mutated = structuredClone(fixture());
      mutated.theories[0]!.status = claimStatus;
      mutated.theories[0]!.ledgerProjection!.projectedClaimStatus = claimStatus;
      mutated.theories[0]!.ledgerProjection!.legacyMigration = null;
      expect(() => parseMatterAnalysisRead(mutated)).toThrow();
    },
  );

  it("rejects nonreciprocal Issue and Element edges", () => {
    const issue = structuredClone(fixture());
    issue.issues.push({
      issueId: "91000000-0000-4000-8000-000000000034",
      question: "A second synthetic Issue must not claim the same Claim.",
      status: "identified",
      version: 1,
      theoryIds: ["91000000-0000-4000-8000-000000000031"],
    });
    expect(() => parseMatterAnalysisRead(issue)).toThrow();

    const element = structuredClone(fixture());
    element.theories[1]!.elementIds.push(
      "91000000-0000-4000-8000-000000000040",
    );
    expect(() => parseMatterAnalysisRead(element)).toThrow();
  });

  it("rejects an unknown Fact Assertion subject", () => {
    const mutated = structuredClone(fixture());
    mutated.factAssertions[0]!.subjectRef =
      "91000000-0000-4000-8000-000000000099";
    expect(() => parseMatterAnalysisRead(mutated)).toThrow();
  });

  it("rejects provenance, Authority, graph, and page drift", () => {
    const provenance = structuredClone(fixture());
    provenance.theories[0]!.evidence[0]!.source.sourceLocator =
      "fixture://mutated";
    expect(() => parseMatterAnalysisRead(provenance)).toThrow();

    const authority = structuredClone(fixture());
    authority.authorities[0]!.source.origin = "course_instruction";
    expect(() => parseMatterAnalysisRead(authority)).toThrow();

    const element = structuredClone(fixture());
    element.elements[0]!.theoryId = "91000000-0000-4000-8000-000000000099";
    expect(() => parseMatterAnalysisRead(element)).toThrow();

    const page = structuredClone(fixture());
    page.page.returned = 1;
    expect(() => parseMatterAnalysisRead(page)).toThrow();
  });

  it("loads with cookie custody, exact Tenant scope, and no bearer material", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify(fixture()), { status: 200 }),
    ) as unknown as typeof fetch;
    const response = await loadJoinedAnalysis({
      baseUrl: "https://api.test",
      tenantId: fixture().tenantId,
      matterId: "matter/with?reserved",
      limit: 1,
      cursor: "cursor/value",
      fetchImpl,
    });
    expect(response.schemaRevision).toBe("sklegal-matter-analysis/v1");
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [URL, RequestInit];
    expect(url.pathname).toBe("/v1/matters/matter%2Fwith%3Freserved/analysis");
    expect(url.searchParams.get("limit")).toBe("1");
    expect(url.searchParams.get("cursor")).toBe("cursor/value");
    expect(init.credentials).toBe("same-origin");
    expect(
      (init.headers as Record<string, string>).Authorization,
    ).toBeUndefined();
    expect((init.headers as Record<string, string>)["X-SKLegal-Tenant"]).toBe(
      fixture().tenantId,
    );
  });

  it.each([
    [401, "authentication_required"],
    [403, "access_denied"],
    [404, "resource_unavailable"],
    [503, "dependency_unavailable"],
  ] as const)(
    "maps HTTP %i to a sanitized unavailable state",
    async (status, kind) => {
      const fetchImpl = (async () =>
        new Response("private backend detail", { status })) as typeof fetch;
      const caught = await loadJoinedAnalysis({
        baseUrl: "https://api.test",
        tenantId: fixture().tenantId,
        matterId: fixture().matterId,
        fetchImpl,
      }).catch((error: unknown) => error);
      expect(caught).toBeInstanceOf(JoinedAnalysisError);
      expect((caught as JoinedAnalysisError).kind).toBe(kind);
      expect((caught as Error).message).not.toContain("private backend detail");
    },
  );

  it("renders support, counter-support, contrary Authority, gaps, and inert outage", () => {
    const ready = renderToStaticMarkup(
      <JoinedAnalysisPanel state={{ kind: "ready", analysis: fixture() }} />,
    );
    expect(ready).toContain("Issues, Claims, Defenses, and Authority");
    expect(ready).toContain("Synthetic performance Claim");
    expect(ready).toContain("Counter-support</dt><dd>1");
    expect(ready).toContain("Contrary Authority</dt><dd>1");
    expect(ready).toContain("Blocking gaps</dt><dd>1");
    expect(ready).toContain("public_synthetic");
    const unavailable = renderToStaticMarkup(
      <JoinedAnalysisPanel
        state={{
          kind: "unavailable",
          message: "Policy evidence is unavailable.",
        }}
      />,
    );
    expect(unavailable).toContain("Joined analysis unavailable");
    expect(unavailable).toContain("disabled");
    expect(unavailable).not.toContain("Authorization");
  });
});
