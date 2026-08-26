import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { buildTurns } from "./conversation-utils";

/**
 * Mid-turn reconnect: the kernel re-sends each open stream as ONE frame
 * carrying its absolute state, marked ``live_snapshot``. The deltas that
 * preceded the reconnect are never persisted, so this frame is the only
 * way the recovered prefix can arrive at all.
 *
 * Everything here turns on replace-vs-append. Appending a snapshot
 * duplicates whatever the client already had; failing to recognise a
 * STALE snapshot (one the server took before a canonical event
 * superseded it) resurrects text the turn has already moved past.
 */

const evt = (
  seq: number,
  eventType: string,
  payload: Record<string, string>,
): SessionEventDTO => ({
  seq,
  event: { event_type: eventType, payload },
});

const snapshot = (seq: number, payload: Record<string, string>) =>
  evt(seq, "message.assistant.text_delta", {
    ...payload,
    live_snapshot: "true",
  });

describe("buildTurns — live snapshot frames", () => {
  it("recovers the streamed prefix for a client that connected mid-turn", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      snapshot(0, { text: "Once upon a time", message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "assistant",
        text: "Once upon a time",
        messageId: "a1",
        sealed: false,
      },
    ]);
  });

  it("keeps streaming after the snapshot rather than sealing the block", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      snapshot(0, { text: "Once upon ", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", {
        text: "a time",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "assistant",
        text: "Once upon a time",
        messageId: "a1",
        sealed: false,
      },
    ]);
  });

  it("replaces rather than appends, so redelivery is harmless", () => {
    // Two reconnects in a row deliver the same absolute state twice.
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      snapshot(0, { text: "Hello world", message_id: "a1" }),
      snapshot(0, { text: "Hello world", message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello world", messageId: "a1", sealed: false },
    ]);
  });

  it("does not append the snapshot onto text the client already had", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "Hel", message_id: "a1" }),
      // Reconnect: the server's absolute state INCLUDES the "Hel" the
      // client already rendered. Appending would produce "HelHello".
      snapshot(0, { text: "Hello", message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello", messageId: "a1", sealed: false },
    ]);
  });

  it("drops a snapshot the canonical event already superseded", () => {
    // The server takes the snapshot when the tap attaches, which happens
    // BEFORE it reads history — so a canonical event landing in that
    // window reaches the client first and the snapshot arrives stale.
    // Its text is a prefix of the sealed canonical: nothing to add.
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.delta", {
        text: "Hello world",
        message_id: "a1",
      }),
      snapshot(0, { text: "Hello", message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello world", messageId: "a1", sealed: true },
    ]);
  });

  it("starts a new block for a snapshot of the NEXT segment", () => {
    // Runtimes that seal per segment keep streaming under one message_id.
    // Segment 2's snapshot is not contained in segment 1's sealed text,
    // so it is a continuation — not a stale redelivery.
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.delta", {
        text: "segment one",
        message_id: "a1",
      }),
      snapshot(0, { text: "segment two", message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "segment one", messageId: "a1", sealed: true },
      { kind: "assistant", text: "segment two", messageId: "a1", sealed: false },
    ]);
  });

  it("keeps a subagent's recovered text out of the lead's block", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      snapshot(0, { text: "lead says", message_id: "a1" }),
      snapshot(0, {
        text: "sub says",
        message_id: "a1",
        parent_tool_use_id: "tool-9",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "lead says", messageId: "a1", sealed: false },
      {
        kind: "assistant",
        text: "sub says",
        messageId: "a1",
        sealed: false,
        parentToolUseId: "tool-9",
      },
    ]);
  });

  it("recovers thinking the same way", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(0, "message.assistant.thinking_delta", {
        text: "pon",
        message_id: "a1",
        live_snapshot: "true",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "thinking", text: "pon", messageId: "a1", sealed: false },
    ]);
  });

  it("leaves unmarked deltas appending, so nothing else changes", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "Hel", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", { text: "lo", message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello", messageId: "a1", sealed: false },
    ]);
  });
});
