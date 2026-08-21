import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getA2UIGalleryExtensions,
  registerA2UIGalleryExtension,
  resetA2UIGalleryExtensionsForTests,
  subscribeA2UIGalleryExtensions,
} from "./registry";

const section = {
  id: "charts",
  label: "行业图表",
  description: "分发版图表",
  componentCount: 3,
  load: async () => ({ default: () => null }),
};

afterEach(() => resetA2UIGalleryExtensionsForTests());

describe("A2UI Gallery extensions", () => {
  it("registers, replaces, and disposes distribution groups", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeA2UIGalleryExtensions(listener);
    const first = { id: "finance", label: "金融组件", description: "v1", sections: [section] };
    const disposeFirst = registerA2UIGalleryExtension(first);

    expect(getA2UIGalleryExtensions()).toEqual([first]);
    expect(listener).toHaveBeenCalledTimes(1);

    const replacement = { ...first, description: "v2" };
    const disposeReplacement = registerA2UIGalleryExtension(replacement);
    disposeFirst();
    expect(getA2UIGalleryExtensions()).toEqual([replacement]);

    disposeReplacement();
    expect(getA2UIGalleryExtensions()).toEqual([]);
    unsubscribe();
  });

  it("does not invoke a section loader during registration", () => {
    const load = vi.fn(section.load);
    registerA2UIGalleryExtension({
      id: "finance",
      label: "金融组件",
      description: "",
      sections: [{ ...section, load }],
    });

    expect(load).not.toHaveBeenCalled();
  });

  it("rejects ambiguous section registrations", () => {
    expect(() => registerA2UIGalleryExtension({
      id: "finance",
      label: "金融组件",
      description: "",
      sections: [section, section],
    })).toThrow(/duplicate section/);
  });
});
