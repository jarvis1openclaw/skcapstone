import { describe, expect, it } from "vitest";

import { foundationLabel, PRODUCT_NAME } from "./foundation";

describe("foundation workspace", () => {
  it("uses the approved product name", () => {
    expect(PRODUCT_NAME).toBe("SKLegal");
    expect(foundationLabel("development")).toBe("SKLegal development");
  });
});
