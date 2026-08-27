import { describe, expect, it } from "vitest";
import type { SkillView } from "@valuz/core";

import {
  libraryEnabledSkillItems,
  resolveAgentSkillItems,
} from "./agent-skill-items";

const skill = (over: Partial<SkillView> & { slug: string }): SkillView =>
  ({
    id: over.slug,
    name: over.slug,
    description: "",
    scope: "user",
    source: "user",
    path: `/skills/${over.slug}`,
    enabled: true,
    tags: [],
    deletable: true,
    ...over,
  }) as SkillView;

describe("libraryEnabledSkillItems", () => {
  it("offers the skills whose library switch is on", () => {
    const items = libraryEnabledSkillItems([
      skill({ slug: "stock-analysis", library_enabled: true }),
      skill({ slug: "dcf", library_enabled: false }),
    ]);
    expect(items.map((i) => i.slug)).toEqual(["stock-analysis"]);
  });

  it("treats an absent switch as on (a backend that never sends the field)", () => {
    const items = libraryEnabledSkillItems([skill({ slug: "comps" })]);
    expect(items.map((i) => i.slug)).toEqual(["comps"]);
  });

  it("drops what the agent could not actually run", () => {
    // Same three predicates as the backend's EffectiveResourceResolver —
    // offering one of these would dead-end the user at run time.
    const items = libraryEnabledSkillItems([
      skill({ slug: "entitled", status: "available" }),
      skill({ slug: "locked", is_locked: true }),
      skill({ slug: "unmaterialized", status: "unavailable" }),
    ]);
    expect(items.map((i) => i.slug)).toEqual(["entitled"]);
  });

  it("lists a slug once when the catalog carries it twice", () => {
    // A user copy shadowing the official package is one entry in the picker,
    // not two identical-looking rows.
    const items = libraryEnabledSkillItems([
      skill({ slug: "browser", id: "official-browser", source: "official" }),
      skill({ slug: "browser", id: "user-browser", source: "user" }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]!.id).toBe("official-browser");
  });

  it("carries the description the picker searches on", () => {
    const items = libraryEnabledSkillItems([
      skill({ slug: "dcf", name: "DCF", description: "discounted cash flow" }),
    ]);
    expect(items[0]).toMatchObject({
      id: "dcf",
      name: "DCF",
      slug: "dcf",
      description: "discounted cash flow",
    });
  });
});

describe("resolveAgentSkillItems", () => {
  it("resolves a bound slug to its catalog name", () => {
    const items = resolveAgentSkillItems(
      ["dcf"],
      [[skill({ slug: "dcf", name: "DCF", description: "valuation" })]],
    );
    expect(items[0]).toMatchObject({ slug: "dcf", name: "DCF" });
  });

  it("keeps a bound skill the catalog has never heard of", () => {
    const items = resolveAgentSkillItems(["ghost"], [[]]);
    expect(items).toEqual([{ id: "ghost", name: "ghost", slug: "ghost" }]);
  });

  it("takes the directory basename of an absolute path", () => {
    const items = resolveAgentSkillItems(
      ["/Users/me/skills/weather-query-v2"],
      [[]],
    );
    expect(items[0]!.slug).toBe("weather-query-v2");
  });

  it("does NOT fall back to the library when an agent binds nothing", () => {
    // Explicit bindings stay explicit: an empty array means "no skills", and
    // only an ``all_available`` policy (handled by the caller) means "all of
    // them". Conflating the two is the bug this pair of helpers separates.
    expect(resolveAgentSkillItems([], [[skill({ slug: "dcf" })]])).toEqual([]);
  });
});
