import type { ResourceCategory } from "@valuz/shared";
import { beforeEach, describe, expect, it } from "vitest";

import { useCategoryRegistry } from "./use-resource-categories";

const cat = (id: string, order = 0): ResourceCategory<unknown> => ({
  id,
  label: id,
  order,
  filter: () => true,
});

const ids = (type: string) =>
  (useCategoryRegistry.getState().injected[type] ?? []).map((c) => c.id);

describe("category registry contributions", () => {
  beforeEach(() => {
    useCategoryRegistry.setState({ injected: {}, contributions: {} });
  });

  it("merges contributions from different contributors for the same type", () => {
    const { contribute } = useCategoryRegistry.getState();
    contribute("agent", "org", [cat("team")]);
    contribute("agent", "shared-agents", [cat("shared")]);
    // ``inject`` replaced a type's whole list, so two independent features
    // contributing to one library silently dropped each other's groups.
    expect(ids("agent")).toEqual(["team", "shared"]);
  });

  it("withdraws only the disposed contributor's categories", () => {
    const { contribute } = useCategoryRegistry.getState();
    contribute("agent", "org", [cat("team")]);
    const dispose = contribute("agent", "shared-agents", [cat("shared")]);
    dispose();
    expect(ids("agent")).toEqual(["team"]);
  });

  it("lets a later contributor override a category by id", () => {
    const { contribute } = useCategoryRegistry.getState();
    contribute("agent", "a", [cat("shared", 1)]);
    contribute("agent", "b", [cat("shared", 9)]);
    expect(useCategoryRegistry.getState().injected.agent).toHaveLength(1);
    expect(useCategoryRegistry.getState().injected.agent?.[0]?.order).toBe(9);
  });

  it("keeps inject() as a single-caller replace for compatibility", () => {
    const { inject, contribute } = useCategoryRegistry.getState();
    contribute("skill", "org", [cat("team")]);
    inject("skill", [cat("legacy-a")]);
    inject("skill", [cat("legacy-b")]);
    expect(ids("skill")).toEqual(["team", "legacy-b"]);
  });

  it("remove() clears every contribution for a type", () => {
    const { contribute, remove } = useCategoryRegistry.getState();
    contribute("kb", "org", [cat("team")]);
    remove("kb");
    expect(useCategoryRegistry.getState().injected.kb).toBeUndefined();
  });
});
