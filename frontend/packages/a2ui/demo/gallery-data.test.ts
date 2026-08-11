import { describe, expect, it } from "vitest";

import { valuzBaseComponentNames } from "../src";
import { GALLERY_CATEGORIES, GALLERY_COMPONENT_NAMES } from "./gallery-data";

describe("A2UI component Gallery", () => {
  it("contains the five canonical base component categories", () => {
    expect(GALLERY_CATEGORIES.map((category) => category.id)).toEqual([
      "layout",
      "content",
      "actions",
      "forms",
      "charts",
    ]);
    expect(GALLERY_CATEGORIES.map((category) => category.specimens.length)).toEqual([
      9,
      13,
      3,
      10,
      16,
    ]);
  });

  it("shows every registered base component exactly once", () => {
    expect(new Set(GALLERY_COMPONENT_NAMES).size).toBe(GALLERY_COMPONENT_NAMES.length);
    expect([...GALLERY_COMPONENT_NAMES].sort()).toEqual([...valuzBaseComponentNames].sort());
  });

  it("renders every specimen from a real A2UI surface", () => {
    for (const category of GALLERY_CATEGORIES) {
      for (const item of category.specimens) {
        expect(item.componentNames).toContain(item.name);
        expect(item.surface).toBeDefined();
      }
    }
  });
});
