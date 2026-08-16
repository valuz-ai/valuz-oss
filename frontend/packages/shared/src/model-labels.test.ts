import { afterEach, describe, expect, it } from "vitest";

import {
  _clearDynamicModelLabels,
  modelLabel,
  registerDynamicModelLabels,
} from "./model-labels";

describe("modelLabel resolution tiers", () => {
  afterEach(() => {
    // Process-wide overlay leaks between tests otherwise.
    _clearDynamicModelLabels();
  });

  it("should fall through to the raw id when no label is known", () => {
    expect(modelLabel("brand-new-unknown-model-7")).toBe(
      "brand-new-unknown-model-7",
    );
  });

  it("should use the known-family rule for recognised series", () => {
    expect(modelLabel("claude-opus-4-9")).toBe("Opus 4.9");
    expect(modelLabel("gpt-5.9")).toBe("GPT 5.9");
  });

  it("should prefer the runtime overlay (backend label) over the family rule", () => {
    registerDynamicModelLabels({
      "sys-reportify-pro": "Valuz Pro",
      // Backend label beats even a family-rule match.
      "claude-opus-4-9": "Custom Opus",
    });
    expect(modelLabel("sys-reportify-pro")).toBe("Valuz Pro");
    expect(modelLabel("claude-opus-4-9")).toBe("Custom Opus");
  });

  it("should skip empty / whitespace overlay labels so the rule still wins", () => {
    registerDynamicModelLabels({ "claude-opus-4-9": "  " });
    expect(modelLabel("claude-opus-4-9")).toBe("Opus 4.9");
  });

  it("should return '' for nullish ids", () => {
    expect(modelLabel(null)).toBe("");
    expect(modelLabel(undefined)).toBe("");
    expect(modelLabel("")).toBe("");
  });
});
