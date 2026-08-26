/**
 * ``session.todos.update`` reducer — carry-forward semantics.
 *
 * ``parseTodosUpdate`` documents that malformed payloads return ``null``
 * and are "silently dropped — the prior snapshot stays on screen". The
 * reducer used to violate that contract by writing the parse result
 * unconditionally, wiping the store's snapshot on a frame it couldn't
 * parse. These tests pin the aligned behavior (same as the conversation
 * page's SSE handler): null parse → keep the prior snapshot; a cleared
 * list arrives as ``[]`` (truthy) and still lands.
 */

import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { reduce, useChatStore, type ChatStoreState } from "./chat-store";

const todosFrame = (todos: string, seq = 7): SessionEventDTO => ({
  seq,
  event: {
    event_type: "session.todos.update",
    payload: { todos, message_id: "m1" },
  },
});

const PRIOR = [
  { content: "Plan E2E", status: "completed" },
  { content: "Run smoke", status: "in_progress", activeForm: "Running smoke" },
];

const stateWith = (todos: ChatStoreState["todos"]): ChatStoreState => ({
  ...useChatStore.getState(),
  todos,
  lastSeq: 0,
});

describe("chat-store reduce — session.todos.update", () => {
  it("replaces the snapshot on a well-formed frame", () => {
    const next = reduce(
      stateWith(null),
      todosFrame(JSON.stringify([{ content: "New", status: "pending" }])),
    );
    expect(next.todos).toEqual([{ content: "New", status: "pending" }]);
    expect(next.lastSeq).toBe(7);
  });

  it("keeps the prior snapshot when the frame is malformed", () => {
    const next = reduce(stateWith(PRIOR), todosFrame("not-json"));
    // Regression: an unconditional write nulled the panel here.
    expect(next.todos).toEqual(PRIOR);
    // The frame still advances the resume cursor.
    expect(next.lastSeq).toBe(7);
  });

  it("lets an explicitly cleared list ([]) land", () => {
    const next = reduce(stateWith(PRIOR), todosFrame("[]"));
    expect(next.todos).toEqual([]);
  });
});
