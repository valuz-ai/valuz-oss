/**
 * The PRD's reminder matrix (question-attention) as table-driven tests —
 * these three rows ARE the product spec for interruption behavior.
 */
import { describe, expect, it } from "vitest";

import type { DecisionEntry } from "../api/decisions-api";
import {
  attentionContextLabel,
  attentionRoute,
  decideAttentionChannel,
} from "./attention-dispatch";

describe("decideAttentionChannel", () => {
  it.each([
    // [watched, focused, expected]
    [true, true, "silent"], // inline card on screen, user looking at it
    [true, false, "system"], // watched but window hidden → nobody sees the card
    [false, true, "toast"], // in-app, elsewhere
    [false, false, "system"], // window in background
  ] as const)(
    "watched=%s focused=%s → %s",
    (watched, focused, expected) => {
      expect(decideAttentionChannel(watched, focused)).toBe(expected);
    },
  );
});

function entry(over: Partial<DecisionEntry>): DecisionEntry {
  return {
    pending_id: "p1",
    session_id: "s1",
    source_kind: "chat",
    task_id: null,
    project_id: null,
    subtask_key: null,
    agent_slug: "a",
    project_title: null,
    project_emoji: null,
    task_title: null,
    subtask_label: null,
    session_title: null,
    question_payload: {},
    raised_at: 1,
    ...over,
  };
}

describe("attentionRoute", () => {
  it("task entries land on the task page", () => {
    expect(
      attentionRoute(entry({ source_kind: "task", task_id: "t9" })),
    ).toBe("/tasks/t9");
  });
  it("conversations open the session", () => {
    expect(attentionRoute(entry({ session_id: "s7" }))).toBe(
      "/conversation/s7",
    );
  });
  it("a task entry missing its task_id degrades to the session", () => {
    expect(attentionRoute(entry({ source_kind: "task" }))).toBe(
      "/conversation/s1",
    );
  });
});

describe("attentionContextLabel", () => {
  it("task: task chain", () => {
    expect(
      attentionContextLabel(
        entry({
          source_kind: "task",
          task_title: "打豆豆小游戏",
          subtask_label: "架构设计",
        }),
      ),
    ).toBe("打豆豆小游戏 · 架构设计");
  });
  it("chat: session title", () => {
    expect(
      attentionContextLabel(entry({ session_title: "季度综述" })),
    ).toBe("季度综述");
  });
  it("untitled chat: falls back to the question text", () => {
    expect(
      attentionContextLabel(
        entry({ question_payload: { questions: [{ question: "中文还是英文？" }] } }),
      ),
    ).toBe("中文还是英文？");
  });
});
