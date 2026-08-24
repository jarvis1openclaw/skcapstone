import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { renderStatic } from "../../testing/ssr";
import {
  GovernedCorpusFeatureClient,
  GovernedCorpusFeatureError,
} from "./client";
import { GovernedCorpusPanel } from "./GovernedCorpusPanel";
import type {
  CorpusSource,
  GovernedCorpusResult,
  ProjectionState,
} from "./types";

interface Fixture {
  projection: ProjectionState;
  sources: CorpusSource[];
}

function fixture(): Fixture {
  const data = JSON.parse(
    readFileSync(
      fileURLToPath(
        new URL(
          "../../../../../tests/fixtures/mvp/fragments/governed_corpus/public-synthetic-governed-corpus-v1.json",
          import.meta.url,
        ),
      ),
      "utf8",
    ),
  ) as Fixture;
  return {
    ...data,
    sources: data.sources.map((source) => ({
      ...source,
      chunkSha256: createHash("sha256").update(source.exactSpan).digest("hex"),
    })),
  };
}

function result(): GovernedCorpusResult {
  const data = fixture();
  const source = data.sources[0];
  if (!source) throw new Error("public-synthetic source is missing");
  return {
    tenantId: source.tenantId,
    matterId: source.matterId,
    querySha256: "c".repeat(64),
    authorizationDecisionId: "51000000-0000-4000-8000-000000000001",
    policyDecisionId: "50000000-0000-4000-8000-000000000001",
    policyRevision: "2".repeat(64),
    rightsRevision: "1".repeat(64),
    classificationCeiling: 0,
    projection: data.projection,
    mode: "hybrid_rrf",
    noAnswer: false,
    continuationCursor: null,
    hits: [
      {
        source,
        supersessionStatus: "current",
        rank: {
          rank: 1,
          score: 0.032,
          fullTextRank: 0.7,
          vectorDistance: 0.01,
          rankPath: ["full_text", "pgvector_exact", "hybrid_rrf"],
        },
      },
    ],
  };
}

describe("GovernedCorpusPanel", () => {
  it("renders rank, release, watermark, lag, exact locator, and hashes", () => {
    const corpus = result();
    const html = renderStatic(
      <GovernedCorpusPanel
        result={corpus}
        span={{ source: corpus.hits[0]!.source, projection: corpus.projection }}
      />,
    );
    expect(html).toContain("synthetic-release-1");
    expect(html).toContain("42 of 42");
    expect(html).toContain("full_text then pgvector_exact then hybrid_rrf");
    expect(html).toContain(corpus.hits[0]!.source.sourceSha256);
    expect(html).toContain(corpus.hits[0]!.source.chunkSha256);
    expect(html).toContain("character:0:64");
    expect(html).toContain("Exact source span");
    expect(html).toContain("metadata compatibility only");
    expect(html).not.toContain(">Problem<");
    expect(html).not.toContain(">Incident<");
  });

  it("renders bounded empty, no-answer, busy, and error states", () => {
    const empty = renderStatic(
      <GovernedCorpusPanel result={null} busy error="Corpus unavailable." />,
    );
    expect(empty).toContain('aria-busy="true"');
    expect(empty).toContain('role="alert"');
    expect(empty).toContain("No governed corpus query has run");
    const noAnswer = result();
    noAnswer.hits = [];
    noAnswer.noAnswer = true;
    expect(renderStatic(<GovernedCorpusPanel result={noAnswer} />)).toContain(
      "No governed answer is available in the authorized Matter scope.",
    );
  });
});

describe("GovernedCorpusFeatureClient", () => {
  it("uses same-origin cookies, CSRF, and no bearer material", async () => {
    const corpus = result();
    const fetcher = vi.fn(
      async () => new Response(JSON.stringify({ result: corpus })),
    ) as unknown as typeof fetch;
    const client = new GovernedCorpusFeatureClient({
      baseUrl: "https://api.test/",
      tenantId: () => corpus.tenantId,
      csrfToken: () => "public-synthetic-csrf",
      fetchImpl: fetcher,
    });
    await client.search(corpus.matterId, {
      query: "public synthetic",
      expectedReleaseId: "synthetic-release-1",
      expectedProjectionGeneration: 3,
      requiredCoreWatermark: 42,
      continuationCursor: "opaque.cursor",
    });
    const [url, init] = (fetcher as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    expect(url).toContain(`/v1/matters/${corpus.matterId}/corpus/search`);
    expect(init.credentials).toBe("same-origin");
    expect(String(init.body)).toContain('"continuationCursor":"opaque.cursor"');
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("public-synthetic-csrf");
    expect(JSON.stringify(headers)).not.toContain("Authorization");
  });

  it("keeps untrusted outage details outside the error", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "database host leaked" }), {
          status: 503,
        }),
    ) as unknown as typeof fetch;
    const client = new GovernedCorpusFeatureClient({
      baseUrl: "",
      tenantId: () => "10000000-0000-4000-8000-000000000001",
      csrfToken: () => "public-synthetic-csrf",
      fetchImpl: fetcher,
    });
    await expect(
      client.search("matter", {
        query: "public synthetic",
        expectedReleaseId: "synthetic-release-1",
        expectedProjectionGeneration: 3,
        requiredCoreWatermark: 42,
      }),
    ).rejects.toEqual(
      new GovernedCorpusFeatureError("resource_unavailable", 503),
    );
  });
});

describe("compact evidence style", () => {
  it("keeps identifiers selectable, wrapped, and keyboard reachable", () => {
    const css = readFileSync(
      fileURLToPath(new URL("./governedCorpus.css", import.meta.url)),
      "utf8",
    );
    expect(css).toContain("overflow-wrap: anywhere");
    expect(css).toContain("user-select: text");
    expect(css).toContain("grid-template-columns: minmax(0, 1fr)");
    const html = renderStatic(<GovernedCorpusPanel result={result()} />);
    expect(html).toContain('tabindex="0"');
  });
});
