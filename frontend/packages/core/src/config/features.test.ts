import { describe, expect, it } from "vitest";
import { FEATURES } from "./features";

describe("feature flags", () => {
  it("should expose the personal feature surface", () => {
    expect(FEATURES).toEqual({
      conversation: true,
      projects: true,
      knowledge: true,
      skills: true,
      settings: true,
      onboarding: true,
    });
  });
});
