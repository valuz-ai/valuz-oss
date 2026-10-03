import { describe, expect, it, vi } from "vitest";

import { createContributions } from "./contributions";

describe("createContributions", () => {
  it("returns the registered value and forgets it when disposed", () => {
    const registry = createContributions<string>();
    const dispose = registry.register("a", "one");

    expect(registry.get("a")).toBe("one");
    expect(registry.get("missing")).toBeUndefined();

    dispose();
    expect(registry.get("a")).toBeUndefined();
    expect(registry.entries()).toEqual([]);
  });

  it("lets the latest registration of a key win and restores the previous on dispose", () => {
    const registry = createContributions<string>();
    registry.register("page", "oss");
    const disposeOverlay = registry.register("page", "overlay");

    expect(registry.get("page")).toBe("overlay");
    expect(registry.entries()).toEqual([{ key: "page", value: "overlay" }]);

    disposeOverlay();
    expect(registry.get("page")).toBe("oss");
  });

  it("orders entries by order, then by registration", () => {
    const registry = createContributions<string>();
    registry.register("late", "c", { order: 10 });
    registry.register("first", "a");
    registry.register("second", "b");

    expect(registry.entries().map((entry) => entry.key)).toEqual([
      "first",
      "second",
      "late",
    ]);
  });

  it("is idempotent to dispose twice and keeps a stable snapshot until a change", () => {
    const registry = createContributions<string>();
    const dispose = registry.register("a", "one");
    const before = registry.entries();

    expect(registry.entries()).toBe(before);
    dispose();
    const after = registry.entries();
    dispose();

    expect(after).not.toBe(before);
    expect(registry.entries()).toBe(after);
  });

  it("notifies subscribers on register and dispose, until they unsubscribe", () => {
    const registry = createContributions<string>();
    const listener = vi.fn();
    const unsubscribe = registry.subscribe(listener);

    const dispose = registry.register("a", "one");
    dispose();
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    registry.register("b", "two");
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
