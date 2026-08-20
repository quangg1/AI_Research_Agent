import { describe, expect, it } from "vitest";

describe("kiln web smoke", () => {
  it("formats share path", () => {
    const token = "abc";
    expect(`/share/${token}`).toBe("/share/abc");
  });
});
