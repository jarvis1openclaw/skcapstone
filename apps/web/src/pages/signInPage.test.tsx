import { describe, expect, it } from "vitest";

import {
  PUBLIC_SYNTHETIC_MATTER_PATH,
  publicSyntheticPreviewEnabled,
} from "./SignInPage";

describe("public-synthetic preview bootstrap", () => {
  it.each([undefined, null, "", "0", "true", "yes", 1])(
    "fails closed for %j",
    (value) => {
      expect(publicSyntheticPreviewEnabled(value)).toBe(false);
    },
  );

  it("enables only the exact build-time value 1", () => {
    expect(publicSyntheticPreviewEnabled("1")).toBe(true);
  });

  it("targets the known public-synthetic V2 Matter route after bootstrap", () => {
    expect(PUBLIC_SYNTHETIC_MATTER_PATH).toBe(
      "/matters/10000000-0000-4000-8000-000000000301",
    );
  });
});
