import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { renderStatic } from "../../testing/ssr";
import { AgentRunsApiError, AgentRunsClient } from "./client";
import { AgentRunRequestComposer, AgentRunsPanel } from "./AgentRunsPanel";
import type {
  AgentRunRecord,
  AnalysisRequestCommand,
  BlindChallengeRecord,
  HumanDispositionRecord,
} from "./contracts";

function fixtureRun(): AgentRunRecord {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "../../tests/fixtures/mvp/fragments/agent_runs/public-synthetic-agent-run-v1.json",
      ),
      "utf8",
    ),
  ) as AgentRunRecord;
}

function analysisCommand(run: AgentRunRecord): AnalysisRequestCommand {
  const { request } = run;
  return {
    schemaVersion: "sklegal.agent-analysis-command/v1",
    analysisKind: "matter_analysis",
    purpose: request.purpose,
    classification: request.classification,
    publicSynthetic: request.publicSynthetic,
    promptTemplateId: request.promptTemplateId,
    promptTemplateSha256: request.promptTemplateSha256,
    outputSchemaId: request.outputSchemaId,
    outputSchemaSha256: request.outputSchemaSha256,
    scoringPolicyId: request.scoringPolicyId,
    scoringPolicySha256: request.scoringPolicySha256,
    retryOfRunId: request.retryOfRunId,
    snapshots: request.snapshots,
    agentSpecification: request.agentSpecification,
    requestedLogicalRouteId: request.requestedLogicalRouteId,
  };
}

function reviewAuthorization(run: AgentRunRecord, decisionId: string) {
  return {
    ...run.authorization,
    decisionId,
    capability: "claim.review",
    purpose: "claim_review",
  };
}

function challenge(run: AgentRunRecord): BlindChallengeRecord {
  const recommendation = run.recommendations[0];
  if (recommendation === undefined || run.route === null) {
    throw new Error("invalid public-synthetic fixture");
  }
  return {
    challengeId: "10000000-0000-4000-8000-000000000201",
    version: 1,
    recommendationId: recommendation.recommendationId,
    recommendationVersion: recommendation.version,
    challengerSpecId: "sklegal.public-independent-challenger",
    challengerSpecVersion: 1,
    challengerSpecSha256: "a1".repeat(32),
    challengerRoute: {
      ...run.route,
      logicalRouteId: "sklegal.public-independent-challenge",
      servedModelName: "qwen3.8-public-synthetic-challenger",
      servedModelRevision: "fixture-challenge-r1",
    },
    blindInputSha256: "b1".repeat(32),
    independentOutputSha256: "b2".repeat(32),
    authorization: reviewAuthorization(
      run,
      "10000000-0000-4000-8000-000000000204",
    ),
    sawChallengedConclusion: false,
    outcome: "no_defect",
    defects: [],
    createdAt: "2026-08-23T12:00:02Z",
  };
}

function disposition(run: AgentRunRecord): HumanDispositionRecord {
  const recommendation = run.recommendations[0];
  if (recommendation === undefined) {
    throw new Error("invalid public-synthetic fixture");
  }
  return {
    dispositionId: "10000000-0000-4000-8000-000000000202",
    version: 1,
    recommendationId: recommendation.recommendationId,
    recommendationVersion: recommendation.version,
    decision: "accept_as_proposed_task",
    reviewerPrincipalId: "20000000-0000-4000-8000-000000000001",
    rationale: "Public-synthetic human review completed.",
    policyRevision: "sklegal-authz/v1",
    capabilityDecisionId: "10000000-0000-4000-8000-000000000203",
    authorization: reviewAuthorization(
      run,
      "10000000-0000-4000-8000-000000000203",
    ),
    decidedAt: "2026-08-23T12:00:03Z",
    createsDomainRecord: false,
    externalEffect: false,
  };
}

describe("AgentRunsPanel", () => {
  it("renders the exact public-synthetic request pins before execution", () => {
    const command = analysisCommand(fixtureRun());
    const html = renderStatic(
      <AgentRunRequestComposer command={command} onStart={vi.fn()} />,
    );
    expect(html).toContain("Start governed analysis");
    expect(html).toContain(command.snapshots.matterSnapshotSha256);
    expect(html).toContain(command.snapshots.corpusSnapshotSha256);
    expect(html).toContain(command.snapshots.authoritySnapshotSha256);
    expect(html).toContain(command.snapshots.policySnapshotSha256);
    expect(html).toContain("cannot change Matter workflow state");
  });

  it("renders the no-selection state without inventing a result", () => {
    const html = renderStatic(<AgentRunsPanel run={null} />);
    expect(html).toContain("No governed analysis run is selected");
    expect(html).toContain('role="status"');
  });

  it("renders exact pins, route attribution, source lanes, scoring, and inert gates", () => {
    const run = fixtureRun();
    const html = renderStatic(<AgentRunsPanel run={run} />);
    expect(html).toContain("public synthetic only");
    expect(html).toContain(run.request.agentSpecification.specSha256);
    expect(html).toContain(run.request.promptTemplateSha256);
    expect(html).toContain("qwen3.8-public-synthetic");
    expect(html).toContain("evidentiary support");
    expect(html).toContain("course instruction");
    expect(html).toContain("current authority");
    expect(html).toContain("matter record");
    expect(html).toContain("model inference");
    expect(html).toContain("No attributable human disposition is recorded");
    expect(html).toContain("does not create a Task or Work Product");
    expect(html).not.toContain("Dispatch");
    expect(html).not.toContain("Send email");
    expect(html.match(/disabled=""/g)?.length).toBe(2);
  });

  it("enables positive review only after an independent no-defect challenge", () => {
    const initial = fixtureRun();
    const reviewed: AgentRunRecord = {
      ...initial,
      version: 3,
      challenges: [challenge(initial)],
      dispositions: [disposition(initial)],
      recommendations: initial.recommendations.map((recommendation) => ({
        ...recommendation,
        reviewState: "disposed",
      })),
    };
    const html = renderStatic(<AgentRunsPanel run={reviewed} />);
    expect(html).toContain("no defect");
    expect(html).toContain("conclusion hidden: yes");
    expect(html).toContain("accept as proposed task");
    expect(html).toContain("Public-synthetic human review completed");
    expect(html).not.toContain('disabled=""');
  });

  it("preserves a challenge defect and keeps positive controls disabled", () => {
    const initial = fixtureRun();
    const defect: BlindChallengeRecord = {
      ...challenge(initial),
      outcome: "defect_found",
      defects: [
        {
          defectKind: "unsupported_inference",
          description: "The conclusion exceeds the cited source span.",
          evidenceSha256: "e1".repeat(32),
        },
      ],
    };
    const html = renderStatic(
      <AgentRunsPanel run={{ ...initial, version: 2, challenges: [defect] }} />,
    );
    expect(html).toContain("unsupported inference");
    expect(html).toContain("The conclusion exceeds the cited source span");
    expect(html).toContain('role="alert"');
    expect(html.match(/disabled=""/g)?.length).toBe(2);
  });
});

describe("AgentRunsClient", () => {
  function build(fetchImpl: typeof fetch): AgentRunsClient {
    return new AgentRunsClient({
      baseUrl: "https://api.test/",
      tenantId: "tenant-public-synthetic",
      csrfToken: "csrf-public-synthetic",
      fetchImpl,
    });
  }

  it("encodes scope, sends idempotency and CSRF, and never adds bearer material", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify(fixtureRun()), { status: 201 }),
    ) as unknown as typeof fetch;
    const client = build(fetchImpl);
    const body = analysisCommand(fixtureRun());
    await client.start("matter/one?x", "idempotency-1", body);
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.test/v1/matters/matter%2Fone%3Fx/agent-runs");
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("idempotency-1");
    expect(headers["X-CSRF-Token"]).toBe("csrf-public-synthetic");
    expect(headers["X-SKLegal-Tenant"]).toBe("tenant-public-synthetic");
    expect(headers.Authorization).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
  });

  it("maps HTTP and transport errors to sanitized typed failures", async () => {
    const denied = build(
      (async () =>
        new Response(
          JSON.stringify({ detail: { code: "capability_denied" } }),
          {
            status: 403,
          },
        )) as typeof fetch,
    );
    const deniedError = await denied
      .list("matter")
      .catch((error: unknown) => error);
    expect(deniedError).toBeInstanceOf(AgentRunsApiError);
    expect((deniedError as AgentRunsApiError).status).toBe(403);
    expect((deniedError as AgentRunsApiError).code).toBe("capability_denied");

    const unavailable = build((async () => {
      throw new Error("socket bytes that must not leak");
    }) as typeof fetch);
    const transportError = await unavailable
      .get("matter", "run")
      .catch((error: unknown) => error);
    expect(transportError).toBeInstanceOf(AgentRunsApiError);
    expect((transportError as AgentRunsApiError).code).toBe(
      "agent_runs_unavailable",
    );
    expect((transportError as Error).message).not.toContain("socket bytes");
  });
});
