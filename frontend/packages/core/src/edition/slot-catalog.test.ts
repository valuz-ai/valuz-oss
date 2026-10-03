import { describe, expect, it } from "vitest";

import { declareSlots, findSlotSpec, listSlotSpecs } from "./slot-catalog";

describe("slot catalog", () => {
  it("resolves exact names before {param} patterns", () => {
    const undeclare = declareSlots([
      {
        name: "test.{kind}.actions",
        kind: "list",
        region: "resource",
        description: "pattern",
      },
      {
        name: "test.special.actions",
        kind: "single",
        region: "resource",
        description: "exact",
      },
    ]);
    expect(findSlotSpec("test.agent.actions")?.description).toBe("pattern");
    expect(findSlotSpec("test.special.actions")?.description).toBe("exact");
    // A parameter is one segment: it does not swallow dots.
    expect(findSlotSpec("test.a.b.actions")).toBeUndefined();
    undeclare();
    expect(findSlotSpec("test.agent.actions")).toBeUndefined();
  });

  it("undeclares only the specs it declared", () => {
    const first = {
      name: "test.shared",
      kind: "list",
      region: "shell",
      description: "first",
    } as const;
    const undeclareFirst = declareSlots([first]);
    declareSlots([{ ...first, description: "second" }]);
    undeclareFirst();
    expect(findSlotSpec("test.shared")?.description).toBe("second");
    expect(listSlotSpecs().filter((s) => s.name === "test.shared")).toHaveLength(
      1,
    );
  });
});

describe("host slot catalog", () => {
  it("declares every host slot once", async () => {
    const { HOST_SLOTS } = await import("./host-slots");
    const names = HOST_SLOTS.map((s) => s.name);
    expect(new Set(names).size).toBe(names.length);
    for (const name of names) {
      expect(findSlotSpec(name)?.name).toBe(name);
    }
  });

  it("resolves concrete names of the pattern slots in use today", () => {
    for (const name of [
      "resource.agent.actions",
      "resource.kb.list.actions",
      "resource.skill.cloud-detail",
      "conversation.tool-card.site_deploy",
      "notification.card.thesis_alert",
    ]) {
      expect(findSlotSpec(name), name).toBeDefined();
    }
    // The fixed remote-agent names win over the resource.{type} patterns.
    expect(findSlotSpec("resource.agent.remote-detail.actions")?.name).toBe(
      "resource.agent.remote-detail.actions",
    );
  });
});
