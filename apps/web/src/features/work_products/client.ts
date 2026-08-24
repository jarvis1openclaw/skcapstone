import {
  parseApprovalValidity,
  parseComparison,
  parseWorkProduct,
  parseWorkProducts,
  type ApprovalValidity,
  type VersionBinding,
  type WorkProductAggregate,
  type WorkProductComparison,
} from "./contracts";

export interface WorkProductFeatureClient {
  list(matterId: string, signal?: AbortSignal): Promise<WorkProductAggregate[]>;
  get(
    matterId: string,
    workProductId: string,
    signal?: AbortSignal,
  ): Promise<WorkProductAggregate>;
  mutate(
    path: string,
    body: unknown,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<WorkProductAggregate>;
  compare(
    matterId: string,
    workProductId: string,
    leftVersionId: string,
    rightVersionId: string,
    signal?: AbortSignal,
  ): Promise<WorkProductComparison>;
  approvalValidity(
    matterId: string,
    workProductId: string,
    approvalId: string,
    binding: VersionBinding,
    signal?: AbortSignal,
  ): Promise<ApprovalValidity>;
}

export function createWorkProductFeatureClient(
  apiBase: string,
  fetcher: typeof fetch = fetch,
): WorkProductFeatureClient {
  async function json(path: string, init?: RequestInit): Promise<unknown> {
    const response = await fetcher(`${apiBase}${path}`, {
      credentials: "same-origin",
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
    if (!response.ok) {
      throw new Error(`Work Product request failed with ${response.status}`);
    }
    return response.json();
  }
  return {
    async list(matterId, signal) {
      return parseWorkProducts(
        await json(`/v1/matters/${matterId}/work-products`, { signal }),
      );
    },
    async get(matterId, workProductId, signal) {
      return parseWorkProduct(
        await json(`/v1/matters/${matterId}/work-products/${workProductId}`, {
          signal,
        }),
      );
    },
    async mutate(path, body, idempotencyKey, signal) {
      return parseWorkProduct(
        await json(path, {
          method: "POST",
          signal,
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify(body),
        }),
      );
    },
    async compare(
      matterId,
      workProductId,
      leftVersionId,
      rightVersionId,
      signal,
    ) {
      const query = new URLSearchParams({ leftVersionId, rightVersionId });
      return parseComparison(
        await json(
          `/v1/matters/${matterId}/work-products/${workProductId}/compare?${query}`,
          { signal },
        ),
      );
    },
    async approvalValidity(
      matterId,
      workProductId,
      approvalId,
      binding,
      signal,
    ) {
      const query = new URLSearchParams({
        versionId: binding.workProductVersionId,
        versionNumber: String(binding.versionNumber),
        contentSha256: binding.contentSha256,
      });
      return parseApprovalValidity(
        await json(
          `/v1/matters/${matterId}/work-products/${workProductId}/approvals/${approvalId}/validity?${query}`,
          { signal },
        ),
      );
    },
  };
}
