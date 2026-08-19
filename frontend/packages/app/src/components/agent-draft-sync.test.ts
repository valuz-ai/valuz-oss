import { describe, expect, it } from "vitest";

import { reconcileDraft, sameBrain } from "./agent-draft-sync";

describe("reconcileDraft", () => {
  it("adopts the server value while the draft is untouched", () => {
    // Someone else changed the agent; nothing local to protect.
    expect(reconcileDraft("A", "A", "Z")).toBe("Z");
  });

  it("keeps an edit in progress when a re-fetch lands", () => {
    // The regression: saving another tab re-fetches the agent, and the reply
    // used to revert half-written instructions to the stored version.
    expect(reconcileDraft("A half-written…", "A", "A")).toBe("A half-written…");
  });

  it("keeps the edit even when the server value also moved", () => {
    expect(reconcileDraft("mine", "A", "theirs")).toBe("mine");
  });

  it("re-seeds once the draft matches what was saved", () => {
    // After a successful save the draft equals the new server value, so the
    // NEXT unrelated change is adopted again rather than being stuck dirty.
    expect(reconcileDraft("AB", "AB", "later")).toBe("later");
  });

  it("replaces the draft outright when a different agent lands", () => {
    expect(
      reconcileDraft("mine", "A", "other agent", { agentChanged: true }),
    ).toBe("other agent");
  });

  it("handles a null draft (avatar) without treating null as dirty", () => {
    expect(reconcileDraft<string | null>(null, null, "🤖")).toBe("🤖");
    expect(reconcileDraft<string | null>("✏️", null, "🤖")).toBe("✏️");
  });

  it("uses the supplied comparator for object drafts", () => {
    const seeded = { runtime: "codex", providerId: null, model: "gpt" };
    // Same values, fresh object each re-fetch — must not read as dirty.
    const current = { runtime: "codex", providerId: null, model: "gpt" };
    const incoming = { runtime: "claude_agent", providerId: null, model: "op" };
    expect(
      reconcileDraft(current, seeded, incoming, { isEqual: sameBrain }),
    ).toBe(incoming);
    const edited = { runtime: "codex", providerId: null, model: "gpt-5" };
    expect(
      reconcileDraft(edited, seeded, incoming, { isEqual: sameBrain }),
    ).toBe(edited);
  });
});

describe("sameBrain", () => {
  it("compares by value, not identity", () => {
    expect(
      sameBrain(
        { runtime: "codex", providerId: null, model: "gpt" },
        { runtime: "codex", providerId: null, model: "gpt" },
      ),
    ).toBe(true);
    expect(
      sameBrain(
        { runtime: "codex", providerId: "p1", model: "gpt" },
        { runtime: "codex", providerId: null, model: "gpt" },
      ),
    ).toBe(false);
  });
});
