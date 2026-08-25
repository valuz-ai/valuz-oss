import { describe, expect, it } from "vitest";
import type { Agent } from "@valuz/core";
import {
  compareAgentsWithValurionFirst,
  isCloudOnlyAgent,
  isCloudOnlyResource,
  isSystemAgent,
} from "./agent-list-state";

const agent = {
  id: "agent-1",
  slug: "course-builder",
  name: "Course Builder",
} as Agent;

describe("isCloudOnlyAgent", () => {
  it("identifies organization catalog rows that are not installed locally", () => {
    expect(
      isCloudOnlyAgent({
        ...agent,
        _sync: { status: "cloud_only", cloud_id: "org-agent-1" },
      } as unknown as Agent),
    ).toBe(true);
  });

  it("keeps local and synced organization agents selectable", () => {
    expect(isCloudOnlyAgent(agent)).toBe(false);
    expect(
      isCloudOnlyAgent({
        ...agent,
        _sync: { status: "synced", cloud_id: "org-agent-1" },
      } as unknown as Agent),
    ).toBe(false);
  });
});

describe("isCloudOnlyResource", () => {
  it("supports raw Skill rows and installed Connector entries", () => {
    expect(
      isCloudOnlyResource({
        id: "org-skill",
        _sync: { status: "cloud_only" },
      }),
    ).toBe(true);
    expect(
      isCloudOnlyResource({
        kind: "installed",
        item: {
          id: "org-connector",
          _sync: { status: "cloud_only" },
        },
      }),
    ).toBe(true);
  });

  it("recognizes organization sync metadata used by duplicated local rows", () => {
    expect(
      isCloudOnlyResource({
        id: "organization-agent",
        _org_sync: { status: "cloud_only" },
      }),
    ).toBe(true);
  });
});

describe("isSystemAgent", () => {
  it("uses the backend kind instead of a display slug or source heuristic", () => {
    expect(isSystemAgent({ ...agent, kind: "system" } as Agent)).toBe(true);
    expect(
      isSystemAgent({
        ...agent,
        slug: "valurion",
        kind: "standard",
      } as Agent),
    ).toBe(false);
  });
});

describe("compareAgentsWithValurionFirst", () => {
  it("pins Valurion ahead of agents that would otherwise sort before it", () => {
    const valurion = {
      ...agent,
      slug: "valurion",
      name: "Valurion",
      kind: "system",
    } as Agent;
    const alpha = { ...agent, slug: "alpha", name: "Alpha" } as Agent;

    expect([alpha, valurion].sort(compareAgentsWithValurionFirst)).toEqual([
      valurion,
      alpha,
    ]);
  });
});
