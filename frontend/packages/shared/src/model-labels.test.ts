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

  it("should use the static MODEL_LABELS table for known ids", () => {
    expect(modelLabel("reportify-pro")).toBe("Reportify Pro");
  });

  it("should use the known-family rule when MODEL_LABELS misses", () => {
    expect(modelLabel("claude-opus-4-9")).toBe("Opus 4.9");
  });

  it("should prefer the runtime overlay over every static tier", () => {
    registerDynamicModelLabels({
      "sys-reportify-pro": "Valuz Pro",
      // Confirm overlay beats even the static MODEL_LABELS table.
      "reportify-pro": "Valuz Pro Mini",
    });
    expect(modelLabel("sys-reportify-pro")).toBe("Valuz Pro");
    expect(modelLabel("reportify-pro")).toBe("Valuz Pro Mini");
  });

  it("should skip empty / whitespace labels so the static tier still wins", () => {
    registerDynamicModelLabels({ "reportify-pro": "  " });
    // Whitespace overlay was rejected — static tier still resolves.
    expect(modelLabel("reportify-pro")).toBe("Reportify Pro");
  });

  it("should return '' for nullish ids", () => {
    expect(modelLabel(null)).toBe("");
    expect(modelLabel(undefined)).toBe("");
    expect(modelLabel("")).toBe("");
  });
});
