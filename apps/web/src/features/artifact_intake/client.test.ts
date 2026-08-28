import { describe, expect, it, vi } from "vitest";

import {
  artifactCommandFromFile,
  ArtifactIntakeFeatureClient,
  ArtifactIntakeFeatureError,
} from "./client";

describe("artifact feature client", () => {
  it("builds exact byte count, digest, and base64 only in request memory", async () => {
    const file = new File(["Public synthetic artifact."], "exhibit.txt", {
      type: "text/plain",
    });
    const command = await artifactCommandFromFile({
      file,
      sourceIdentity: "synthetic-source-ui",
      retentionPolicyId: "70000000-0000-4000-8000-000000000001",
      requestOcr: true,
      requestTranscript: false,
    });
    expect(command.original.filename).toBe("exhibit.txt");
    expect(command.original.byteCount).toBe(file.size);
    expect(command.original.contentSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(command.original.contentBase64).toBe(
      btoa("Public synthetic artifact."),
    );
    expect(command.requestedDerivations.map((item) => item.kind)).toEqual([
      "text_extraction",
      "ocr",
    ]);
  });

  it("posts to the frozen route with session, CSRF, tenant, and idempotency", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ artifact: { artifactId: "artifact-1" } }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ArtifactIntakeFeatureClient({
      baseUrl: "https://sklegal.internal/",
      tenantId: () => "tenant-synthetic",
      csrfToken: () => "csrf-synthetic",
      fetchImpl,
    });
    const body = { source: { sourceSystem: "public_synthetic" } };
    await client.intake("matter/with spaces", "artifact-key-001", body);

    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe(
      "https://sklegal.internal/v1/matters/matter%2Fwith%20spaces/artifacts",
    );
    expect(init?.credentials).toBe("same-origin");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": "artifact-key-001",
      "X-CSRF-Token": "csrf-synthetic",
      "X-SKLegal-Tenant": "tenant-synthetic",
    });
    expect(init?.body).toBe(JSON.stringify(body));
  });

  it("uses only the closed error vocabulary and does not echo server detail", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "policy_unavailable",
            rawValue: "must-not-cross-client-boundary",
          },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = new ArtifactIntakeFeatureClient({
      baseUrl: "",
      tenantId: () => "tenant-synthetic",
      csrfToken: () => "csrf-synthetic",
      fetchImpl,
    });
    await expect(
      client.intake("matter-1", "artifact-key-002", {}),
    ).rejects.toEqual(
      new ArtifactIntakeFeatureError("policy_unavailable", 503),
    );
  });
});
