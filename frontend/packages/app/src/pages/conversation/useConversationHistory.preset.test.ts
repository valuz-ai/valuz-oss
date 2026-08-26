import { describe, expect, it } from "vitest";
import type { ProjectListItem } from "@valuz/core";

import { resolvePresetProject } from "./useConversationHistory";

const project = (id: string, kind: "project" | "chat"): ProjectListItem =>
  ({
    id,
    name: id,
    kind,
    root_path: null,
    icon: null,
    cwd: null,
  }) as ProjectListItem;

const PROJECTS = [project("ws-1", "project"), project("chat-default", "chat")];

describe("resolvePresetProject", () => {
  it("prefers the route query over the host default", () => {
    expect(resolvePresetProject("ws-1", "other", PROJECTS)).toBe("ws-1");
  });

  it("falls back to the embedding host's createDefaults project", () => {
    expect(resolvePresetProject(null, "ws-1", PROJECTS)).toBe("ws-1");
  });

  it("ignores the chat-default sentinel and unknown ids", () => {
    expect(resolvePresetProject(null, "chat-default", PROJECTS)).toBeNull();
    expect(resolvePresetProject(null, "gone", PROJECTS)).toBeNull();
    expect(resolvePresetProject("gone", null, PROJECTS)).toBeNull();
    expect(resolvePresetProject(null, null, PROJECTS)).toBeNull();
  });
});
