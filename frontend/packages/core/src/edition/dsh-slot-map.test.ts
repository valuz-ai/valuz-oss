import { describe, expect, it } from "vitest";

import catalog from "./dsh-slot-catalog.json";
import {
  DSH_REGISTRY_TARGETS,
  DSH_SLOT_MAP,
  dshPluginCompatLevel,
} from "./dsh-slot-map";
import { findSlotSpec } from "./slot-catalog";

const dshKeys = catalog.slots.map((slot) => slot.key);

describe("dsh slot mapping (upstream drift guard)", () => {
  it("classifies every slot the pinned dsh web client declares", () => {
    const unclassified = dshKeys.filter((key) => !(key in DSH_SLOT_MAP));
    // A failure here means upstream added or renamed a slot: classify it in
    // dsh-slot-map.ts (map it onto a Valuz slot, or mark it L3 with a reason).
    expect(unclassified).toEqual([]);
  });

  it("carries no mapping for a slot upstream no longer declares", () => {
    const stale = Object.keys(DSH_SLOT_MAP).filter(
      (key) => !dshKeys.includes(key),
    );
    expect(stale).toEqual([]);
  });

  it("maps only onto Valuz slots and registries that exist", () => {
    const broken = Object.entries(DSH_SLOT_MAP)
      .filter(([, target]) => target.level !== "L3")
      .map(
        ([key, target]) => [key, (target as { valuz: string }).valuz] as const,
      )
      .filter(
        ([, valuz]) =>
          !(DSH_REGISTRY_TARGETS as readonly string[]).includes(valuz) &&
          findSlotSpec(valuz.replace("{tool}", "probe")) === undefined,
      );
    expect(broken).toEqual([]);
  });

  it("is pinned to one dsh release", () => {
    expect(catalog.dsh).toMatch(/^\d+\.\d+\.\d+/);
    expect(dshKeys.length).toBeGreaterThan(0);
  });

  it("derives a plugin's compatibility level from its slots", () => {
    expect(
      dshPluginCompatLevel(["shell.overlay", "settings.general.item"]),
    ).toBe("L1");
    expect(
      dshPluginCompatLevel(["shell.overlay", "sidebar.right.pane.tab"]),
    ).toBe("L2");
    expect(
      dshPluginCompatLevel([
        "sidebar.right.pane.tab",
        "conversation.chat.node",
      ]),
    ).toBe("L3");
    expect(dshPluginCompatLevel(["a.slot.dsh.never.declared"])).toBe("L3");
  });
});
