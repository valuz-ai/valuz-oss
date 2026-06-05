import { describe, expect, it } from "vitest";
import {
  parseActionResolved,
  parseRequiresAction,
  SESSION_ACTION_RESOLVED_EVENT,
  SESSION_REQUIRES_ACTION_EVENT,
  type SessionEventDTO,
} from "./sessions-api";

function makeFrame(
  eventType: string,
  payload: Record<string, string>,
): SessionEventDTO {
  return { seq: 1, event: { event_type: eventType, payload } };
}

describe("parseRequiresAction", () => {
  it("should decode shell_command payload and decisions array", () => {
    const payload = {
      command: "ls /tmp",
      cwd: "/Users/test/workspace",
      network: false,
      reason: "List temp dir",
    };
    const frame = makeFrame(SESSION_REQUIRES_ACTION_EVENT, {
      pending_id: "pending-abc",
      subject: "shell_command",
      runtime_provider: "claude_agent",
      available_decisions: JSON.stringify(["approve", "reject"]),
      payload: JSON.stringify(payload),
      expires_at: "2026-05-12T13:14:15Z",
      message_id: "msg-7",
    });

    const parsed = parseRequiresAction(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.pending_id).toBe("pending-abc");
    expect(parsed!.subject).toBe("shell_command");
    expect(parsed!.runtime_provider).toBe("claude_agent");
    expect(parsed!.available_decisions).toEqual(["approve", "reject"]);
    expect(parsed!.payload).toEqual(payload);
    expect(parsed!.expires_at).toBe("2026-05-12T13:14:15Z");
    expect(parsed!.message_id).toBe("msg-7");
  });

  it("should decode clarifying_questions payload with the answer decision available", () => {
    const questions = [
      {
        question: "Pick a side",
        header: "Choose one",
        multiSelect: false,
        options: [
          { label: "Yes", description: "Confirm" },
          { label: "No", description: "Skip" },
        ],
      },
    ];
    const frame = makeFrame(SESSION_REQUIRES_ACTION_EVENT, {
      pending_id: "pending-q1",
      subject: "clarifying_questions",
      runtime_provider: "claude_agent",
      available_decisions: JSON.stringify(["answer", "reject"]),
      payload: JSON.stringify({ questions }),
      message_id: "msg-9",
    });

    const parsed = parseRequiresAction(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.subject).toBe("clarifying_questions");
    expect(parsed!.available_decisions).toContain("answer");
    expect(
      (parsed!.payload as { questions: unknown[] }).questions,
    ).toHaveLength(1);
  });

  it("should return null when the frame is a different event type", () => {
    const frame = makeFrame("message.assistant.delta", { text: "hi" });
    expect(parseRequiresAction(frame)).toBeNull();
  });

  it("should fall back to defaults on malformed structured payload", () => {
    // Renderer must keep rendering the card with what it can decode —
    // bare pending_id + subject — rather than dropping the whole frame.
    const frame = makeFrame(SESSION_REQUIRES_ACTION_EVENT, {
      pending_id: "pending-xyz",
      subject: "tool_input",
      runtime_provider: "deepagents",
      available_decisions: "{not json",
      payload: "{not json",
      message_id: "msg-1",
    });

    const parsed = parseRequiresAction(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.pending_id).toBe("pending-xyz");
    expect(parsed!.subject).toBe("tool_input");
    expect(parsed!.available_decisions).toEqual([]);
    expect(parsed!.payload).toEqual({});
  });
});

describe("parseActionResolved", () => {
  it("should decode a user-approved decision", () => {
    const frame = makeFrame(SESSION_ACTION_RESOLVED_EVENT, {
      pending_id: "pending-abc",
      decision: "approve",
      resolved_by: "user",
      message: "",
      message_id: "msg-7",
      answers: JSON.stringify({}),
    });

    const parsed = parseActionResolved(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.decision).toBe("approve");
    expect(parsed!.resolved_by).toBe("user");
    expect(parsed!.answers).toEqual({});
  });

  it("should decode answers map for an answer decision", () => {
    const answers = { "Pick a side": "Yes", Languages: ["Python", "Rust"] };
    const frame = makeFrame(SESSION_ACTION_RESOLVED_EVENT, {
      pending_id: "pending-q1",
      decision: "answer",
      resolved_by: "user",
      answers: JSON.stringify(answers),
      message_id: "msg-9",
    });

    const parsed = parseActionResolved(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.decision).toBe("answer");
    expect(parsed!.answers).toEqual(answers);
  });

  it("should decode the system-expired synthetic seal", () => {
    // Emitted by ``scan_orphan_pendings`` at host startup. The
    // renderer must move the card out of the "waiting" state even
    // though no user clicked anything.
    const frame = makeFrame(SESSION_ACTION_RESOLVED_EVENT, {
      pending_id: "stale-1",
      decision: "expired",
      resolved_by: "system",
      message_id: "msg-2",
    });

    const parsed = parseActionResolved(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.decision).toBe("expired");
    expect(parsed!.resolved_by).toBe("system");
  });

  it("should return null when the frame is a different event type", () => {
    const frame = makeFrame("session.todos.update", { todos: "[]" });
    expect(parseActionResolved(frame)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// V5+d008b53 — approval contract v2 wire shape
// ---------------------------------------------------------------------------

describe("parseRequiresAction (v2 fields)", () => {
  it("should decode session_rule_preview into a structured object", () => {
    const preview = {
      kind: "shell_command",
      display: "Bash(npm test:*)",
      runtime_kind: "claude_pattern",
      rule_data: { pattern: "Bash(npm test:*)" },
    };
    const frame = makeFrame(SESSION_REQUIRES_ACTION_EVENT, {
      pending_id: "pending-rule-1",
      subject: "shell_command",
      runtime_provider: "claude_agent",
      available_decisions: JSON.stringify([
        "approve",
        "approve_for_session",
        "reject",
      ]),
      payload: JSON.stringify({ command: "npm test", cwd: "/work" }),
      session_rule_preview: JSON.stringify(preview),
      message_id: "msg-11",
    });

    const parsed = parseRequiresAction(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.session_rule_preview).toEqual(preview);
  });

  it("should decode original_input for the Edit & Approve seed", () => {
    const original = { command: "rm -rf /tmp/cache", timeout: 30 };
    const frame = makeFrame(SESSION_REQUIRES_ACTION_EVENT, {
      pending_id: "pending-edit-1",
      subject: "shell_command",
      runtime_provider: "claude_agent",
      available_decisions: JSON.stringify([
        "approve",
        "approve_with_changes",
        "reject",
      ]),
      payload: JSON.stringify(original),
      original_input: JSON.stringify(original),
      message_id: "msg-12",
    });

    const parsed = parseRequiresAction(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.original_input).toEqual(original);
  });

  it("should emit null preview / original_input when the kernel omits them", () => {
    // Older kernel or a subject like ``clarifying_questions`` that
    // intentionally skips the rule path. The host emits empty strings
    // / empty objects; the parser must surface ``null`` so the UI
    // can branch on presence rather than parse-twice.
    const frame = makeFrame(SESSION_REQUIRES_ACTION_EVENT, {
      pending_id: "pending-clarify-1",
      subject: "clarifying_questions",
      runtime_provider: "claude_agent",
      available_decisions: JSON.stringify(["answer", "reject"]),
      payload: JSON.stringify({ questions: [] }),
      session_rule_preview: "{}",
      original_input: "{}",
      message_id: "msg-13",
    });

    const parsed = parseRequiresAction(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.session_rule_preview).toBeNull();
    expect(parsed!.original_input).toBeNull();
  });
});

describe("parseActionResolved (v2 verbs)", () => {
  it("should decode approve_for_session with rule_id", () => {
    const frame = makeFrame(SESSION_ACTION_RESOLVED_EVENT, {
      pending_id: "pending-rule-1",
      decision: "approve_for_session",
      resolved_by: "user",
      message: "",
      rule_id: "rule-uuid-aaa",
      message_id: "msg-11",
    });

    const parsed = parseActionResolved(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.decision).toBe("approve_for_session");
    expect(parsed!.rule_id).toBe("rule-uuid-aaa");
    expect(parsed!.auto_resolved_by_rule_id).toBeNull();
  });

  it("should decode auto_approved with the rule back-pointer", () => {
    // Kernel-synthesized cache-hit verb — no preceding requires_action.
    const frame = makeFrame(SESSION_ACTION_RESOLVED_EVENT, {
      pending_id: "pending-auto-2",
      decision: "auto_approved",
      resolved_by: "system",
      message: "",
      auto_resolved_by_rule_id: "rule-uuid-aaa",
      message_id: "msg-14",
    });

    const parsed = parseActionResolved(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.decision).toBe("auto_approved");
    expect(parsed!.resolved_by).toBe("system");
    expect(parsed!.auto_resolved_by_rule_id).toBe("rule-uuid-aaa");
    expect(parsed!.rule_id).toBeNull();
  });

  it("should decode approve_with_changes with no rule fields", () => {
    const frame = makeFrame(SESSION_ACTION_RESOLVED_EVENT, {
      pending_id: "pending-edit-1",
      decision: "approve_with_changes",
      resolved_by: "user",
      message: "",
      message_id: "msg-12",
    });

    const parsed = parseActionResolved(frame);

    expect(parsed).not.toBeNull();
    expect(parsed!.decision).toBe("approve_with_changes");
    expect(parsed!.rule_id).toBeNull();
    expect(parsed!.auto_resolved_by_rule_id).toBeNull();
  });
});
