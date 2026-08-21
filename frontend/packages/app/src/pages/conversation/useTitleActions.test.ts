import { describe, expect, it } from "vitest";
import { canForkSession } from "./useTitleActions";

describe("canForkSession", () => {
  it("allows a standalone session on a fork-capable runtime", () => {
    expect(canForkSession({ runtime_provider: "codex" })).toBe(true);
    expect(
      canForkSession({ task_id: null, runtime_provider: "deepagents" }),
    ).toBe(true);
  });

  it("refuses a task session even on a fork-capable runtime", () => {
    // A task's lead and members share ONE task-scoped sandbox, so the fork
    // comes up with no history. Both the header item and the per-turn
    // "Fork from here" ask this one predicate — they used to each carry
    // their own copy of the rule and only one of them got the task clause.
    expect(canForkSession({ task_id: "t-1", runtime_provider: "codex" })).toBe(
      false,
    );
  });

  it("refuses a runtime with no wired fork, and a session that has not loaded", () => {
    expect(canForkSession({ runtime_provider: "something_else" })).toBe(false);
    expect(canForkSession({})).toBe(false);
    expect(canForkSession(null)).toBe(false);
  });
});
