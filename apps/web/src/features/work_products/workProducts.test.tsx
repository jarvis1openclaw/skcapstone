import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { renderStatic } from "../../testing/ssr";
import { createWorkProductFeatureClient } from "./client";
import {
  parseApprovalValidity,
  parseComparison,
  parseWorkProduct,
  parseWorkProducts,
  type WorkProductAggregate,
} from "./contracts";
import { WorkProductsPanel } from "./WorkProductsPanel";

function fixture(): WorkProductAggregate {
  return parseWorkProduct(
    JSON.parse(
      readFileSync(
        resolve(
          process.cwd(),
          "../../tests/fixtures/mvp/fragments/work_products/public-synthetic-work-product-v1.json",
        ),
        "utf8",
      ),
    ),
  );
}

describe("Work Product contract", () => {
  it("parses the public synthetic feature fragment", () => {
    const product = fixture();
    expect(product.schemaVersion).toBe("sklegal.work-product-aggregate/v1");
    expect(product.status).toBe("approved");
    expect(product.versions).toHaveLength(1);
    expect(product.groundings).toHaveLength(1);
    expect(product.validations[0]?.outcome).toBe("passed");
    expect(product.approvals[0]?.status).toBe("approved");
  });

  it("fails closed on schema drift, invalid hashes, and current-version drift", () => {
    const product = fixture();
    expect(() => parseWorkProduct({ ...product, schemaVersion: "v2" })).toThrow(
      "unsupported",
    );
    expect(() =>
      parseWorkProduct({
        ...product,
        versions: [{ ...product.versions[0], contentSha256: "not-a-digest" }],
      }),
    ).toThrow("SHA-256");
    expect(() =>
      parseWorkProduct({ ...product, currentVersionId: "missing" }),
    ).toThrow("exactly one active");
  });

  it("parses lists, comparisons, and exact Approval validity", () => {
    const product = fixture();
    expect(parseWorkProducts([product])).toHaveLength(1);
    const comparison = parseComparison({
      workProductId: product.workProductId,
      left: product.approvals[0]?.binding,
      right: product.approvals[0]?.binding,
      unifiedDiff: [],
      changed: false,
    });
    expect(comparison.changed).toBe(false);
    const validity = parseApprovalValidity({
      approvalId: product.approvals[0]?.approvalId,
      binding: product.approvals[0]?.binding,
      valid: true,
      reasonCode: "approval_valid",
    });
    expect(validity.valid).toBe(true);
  });
});

describe("WorkProductsPanel", () => {
  it("renders content, grounding, validation, and exact Approval state", () => {
    const product = fixture();
    const approval = product.approvals[0];
    if (approval === undefined) throw new Error("fixture Approval missing");
    const html = renderStatic(
      <WorkProductsPanel
        products={[product]}
        validity={{
          [approval.approvalId]: {
            approvalId: approval.approvalId,
            binding: approval.binding,
            valid: true,
            reasonCode: "approval_valid",
          },
        }}
      />,
    );
    expect(html).toContain("Public synthetic memorandum");
    expect(html).toContain("The public synthetic fact is grounded");
    expect(html).toContain("passed: Exact public synthetic checks completed");
    expect(html).toContain("Approval approved");
    expect(html).toContain("approval_valid");
    expect(html).toContain("Revoke Approval");
    expect(html).not.toContain("Dispatch");
  });

  it("keeps approval request disabled without passing validation", () => {
    const product = fixture();
    const pending: WorkProductAggregate = {
      ...product,
      status: "in_review",
      validations: [],
      approvals: [],
    };
    const html = renderStatic(<WorkProductsPanel products={[pending]} />);
    expect(html).toContain("Not validated");
    expect(html).toContain("Request Approval");
    expect(html).toContain('disabled=""');
  });

  it("renders empty and sanitized error states", () => {
    expect(renderStatic(<WorkProductsPanel products={[]} />)).toContain(
      "No Work Products are available",
    );
    expect(
      renderStatic(
        <WorkProductsPanel products={[]} error="Work Product unavailable" />,
      ),
    ).toContain('role="alert"');
  });
});

describe("Work Product feature client", () => {
  it("uses same-origin cookies and exact idempotency without bearer material", async () => {
    const fetcher = vi.fn(
      async () => new Response(JSON.stringify(fixture()), { status: 201 }),
    ) as unknown as typeof fetch;
    const client = createWorkProductFeatureClient("https://api.test", fetcher);
    await client.mutate(
      "/v1/matters/matter/work-products",
      { schemaVersion: "sklegal.work-product-create-command/v1" },
      "idempotency-public-synthetic",
    );
    const [, init] = (fetcher as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("same-origin");
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("idempotency-public-synthetic");
    expect(JSON.stringify(headers)).not.toContain("Authorization");
  });

  it("encodes comparison and validity query parameters", async () => {
    const product = fixture();
    const approval = product.approvals[0];
    if (approval === undefined) throw new Error("fixture Approval missing");
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      if (String(url).includes("/compare?")) {
        return new Response(
          JSON.stringify({
            workProductId: product.workProductId,
            left: approval.binding,
            right: approval.binding,
            unifiedDiff: [],
            changed: false,
          }),
        );
      }
      return new Response(
        JSON.stringify({
          approvalId: approval.approvalId,
          binding: approval.binding,
          valid: true,
          reasonCode: "approval_valid",
        }),
      );
    }) as unknown as typeof fetch;
    const client = createWorkProductFeatureClient("", fetcher);
    await client.compare(
      product.matterId,
      product.workProductId,
      approval.binding.workProductVersionId,
      approval.binding.workProductVersionId,
    );
    await client.approvalValidity(
      product.matterId,
      product.workProductId,
      approval.approvalId,
      approval.binding,
    );
    const urls = (
      fetcher as unknown as ReturnType<typeof vi.fn>
    ).mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toContain("leftVersionId=");
    expect(urls[1]).toContain("contentSha256=");
  });

  it("fails closed on non-success responses", async () => {
    const fetcher = vi.fn(
      async () => new Response("unavailable", { status: 503 }),
    ) as unknown as typeof fetch;
    const client = createWorkProductFeatureClient("", fetcher);
    await expect(client.list("matter")).rejects.toThrow("503");
  });
});
