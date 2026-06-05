import { describe, expect, it } from "vitest";
import { PERSONAL_EDITION, isPersonalEdition, type Edition } from "./editions";

describe("edition constants", () => {
  it("exposes PERSONAL_EDITION as 'personal'", () => {
    expect(PERSONAL_EDITION).toBe("personal");
  });

  it("isPersonalEdition true only for the personal id", () => {
    expect(isPersonalEdition(PERSONAL_EDITION)).toBe(true);
    expect(isPersonalEdition("personal")).toBe(true);
    // 公共骨架不知道任何 enterprise edition id；任意非 personal 字符串都不算 personal
    expect(isPersonalEdition("anything-else" as Edition)).toBe(false);
    expect(isPersonalEdition("" as Edition)).toBe(false);
  });
});
