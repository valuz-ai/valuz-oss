import { describe, expect, it } from "vitest";
import type {
  ConversationTurn,
  PrototypeToolCall,
} from "@valuz/shared";
import { computePlanAnchors, extractToolOutputJson } from "./conversation-plan-anchors";

function toolBlock(tool: Partial<PrototypeToolCall> & { id: string; title: string }) {
  return {
    kind: "tool" as const,
    tool: { kind: "fetch", status: "success", ...tool } as PrototypeToolCall,
  };
}

function turn(id: string, blocks: ConversationTurn["blocks"]): ConversationTurn {
  return {
    id,
    userMessageSeq: 0,
    userText: "",
    blocks,
    failedMessage: null,
  };
}

// Kernel wraps tool output as a Python-repr content block.
const repr = (json: string) => `[{'type': 'text', 'text': '${json}'}]`;

describe("extractToolOutputJson", () => {
  it("parses raw JSON", () => {
    expect(extractToolOutputJson('{"task_id":"abc"}')).toEqual({ task_id: "abc" });
  });

  it("pulls the inner JSON object out of a Python-repr content block", () => {
    expect(extractToolOutputJson(repr('{"task_id": "abc", "n": 1}'))).toEqual({
      task_id: "abc",
      n: 1,
    });
  });

  it("pulls the inner JSON object out of a JSON content block", () => {
    // The shape the kernel actually produces now. It differs from the repr
    // above in one way that matters: it is VALID JSON, so `JSON.parse`
    // succeeds and returns the envelope array. Reading fields off that array
    // gives `undefined` for every one of them — a delivered inject rendered
    // as "not delivered (unknown)" because `delivered` and `reason` were
    // missing rather than false.
    const envelope = JSON.stringify([
      { type: "text", text: '{"delivered": true, "lead_session_id": "s1", "reason": null}' },
    ]);
    expect(extractToolOutputJson(envelope)).toEqual({
      delivered: true,
      lead_session_id: "s1",
      reason: null,
    });
  });

  it("keeps a payload that is genuinely an array", () => {
    // Unwrapping unconditionally would be a different kind of wrong: some
    // tools return a list, and handing back its first element would silently
    // drop the rest.
    expect(extractToolOutputJson('[{"a":1},{"b":2}]')).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it("keeps the envelope when its text block is prose, not a payload", () => {
    const envelope = JSON.stringify([{ type: "text", text: "no JSON here" }]);
    expect(extractToolOutputJson(envelope)).toEqual([{ type: "text", text: "no JSON here" }]);
  });

  it("returns null when there is no embedded object", () => {
    expect(extractToolOutputJson("ERROR: nope")).toBeNull();
  });
});

describe("computePlanAnchors", () => {
  it("uses the lead session task id, not the hallucinated tool arg (regression)", () => {
    // Mirrors the real SpaceX lead session: a failed modify_plan passes
    // task_id "1" (hallucinated, 404s) and the successful one omits it.
    const turns = [
      turn("t1", [
        toolBlock({ id: "u-plan", title: "mcp__harness__plan_task" }),
        toolBlock({
          id: "u-bad",
          title: "mcp__harness__modify_plan",
          input: '{"task_id":"1","update":[]}',
          output: repr('ERROR: plan tool: task \\u00271\\u0027 not found'),
        }),
        toolBlock({
          id: "u-good",
          title: "mcp__harness__modify_plan",
          input: '{"update":[]}',
        }),
      ]),
    ];
    const real = "722612beef824bc0820558d1b9a8cc83";

    const { taskByRichTool } = computePlanAnchors(turns, real);

    // Every plan write resolves to the real task id — never "1".
    expect([...taskByRichTool.values()]).not.toContain("1");
    // Last write wins: the rich card anchors at the most recent plan write.
    expect(taskByRichTool.get("u-good")).toBe(real);
    expect(taskByRichTool.has("u-bad")).toBe(false);
    expect(taskByRichTool.has("u-plan")).toBe(false);
  });

  it("falls back to the tool arg when the session is not task-bound", () => {
    const turns = [
      turn("t1", [
        toolBlock({
          id: "u1",
          title: "modify_plan",
          input: '{"task_id":"from-arg"}',
        }),
      ]),
    ];

    const { taskByRichTool } = computePlanAnchors(turns, null);

    expect(taskByRichTool.get("u1")).toBe("from-arg");
  });

  it("ignores non-plan tools", () => {
    const turns = [
      turn("t1", [
        toolBlock({ id: "u1", title: "mcp__harness__dispatch" }),
        toolBlock({ id: "u2", title: "mcp__harness__get_plan" }),
      ]),
    ];

    const { taskByRichTool } = computePlanAnchors(turns, "task-x");

    expect(taskByRichTool.size).toBe(0);
  });

  it("produces no anchor when neither session nor tool args supply an id", () => {
    const turns = [
      turn("t1", [toolBlock({ id: "u1", title: "modify_plan" })]),
    ];

    const { taskByRichTool } = computePlanAnchors(turns, null);

    expect(taskByRichTool.size).toBe(0);
  });
});
