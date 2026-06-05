import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { buildTurns } from "./conversation-utils";

const evt = (
  seq: number,
  eventType: string,
  payload: Record<string, string>,
  timestamp?: number,
): SessionEventDTO => ({
  seq,
  event: { event_type: eventType, payload },
  timestamp,
});

describe("buildTurns — streaming deltas", () => {
  it("should accumulate text_delta into a live assistant block during streaming", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "Hel",
        message_id: "a1",
      }),
      evt(3, "message.assistant.text_delta", {
        text: "lo",
        message_id: "a1",
      }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello", messageId: "a1", sealed: false },
    ]);
  });

  it("should replace the live block's text on canonical message.assistant.delta and seal it", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "Hel",
        message_id: "a1",
      }),
      evt(3, "message.assistant.text_delta", {
        text: "lo",
        message_id: "a1",
      }),
      evt(4, "message.assistant.delta", {
        text: "Hello world",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello world", messageId: "a1", sealed: true },
    ]);
  });

  it("should accumulate thinking_delta separately from text_delta", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking_delta", {
        text: "Let me",
        message_id: "a1",
      }),
      evt(3, "message.assistant.thinking_delta", {
        text: " think",
        message_id: "a1",
      }),
      evt(4, "message.assistant.text_delta", {
        text: "Done",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "thinking",
        text: "Let me think",
        messageId: "a1",
        sealed: false,
      },
      { kind: "assistant", text: "Done", messageId: "a1", sealed: false },
    ]);
  });

  it("should attach thinking elapsed time from user to canonical thinking event timestamps", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "message.assistant.thinking",
        {
          text: "Let me think",
          message_id: "a1",
        },
        Date.parse("2026-05-07T10:00:02.350Z"),
      ),
    ]);

    expect(turns[0]!.blocks[0]).toEqual({
      kind: "thinking",
      text: "Let me think",
      messageId: "a1",
      sealed: true,
      elapsedMs: 2350,
    });
  });

  it("should attach tool elapsed time from user to tool.call.completed timestamp", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "tool.call.started",
        { name: "Read", tool_use_id: "t1", input: "{}" },
        Date.parse("2026-05-07T10:00:01.000Z"),
      ),
      evt(
        3,
        "tool.call.completed",
        { tool_use_id: "t1", content: "ok" },
        Date.parse("2026-05-07T10:00:04.500Z"),
      ),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock?.kind).toBe("tool");
    expect((toolBlock as { elapsedMs?: number }).elapsedMs).toBe(4500);
  });

  it("should attach tool elapsed time from user to tool.call.started timestamp when the tool is still running", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "tool.call.started",
        { name: "Read", tool_use_id: "t1", input: "{}" },
        Date.parse("2026-05-07T10:00:01.500Z"),
      ),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock?.kind).toBe("tool");
    expect((toolBlock as { elapsedMs?: number }).elapsedMs).toBe(1500);
  });

  it("should attach elapsed time to meta tools flushed at end of stream", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "message.assistant.delta",
        { text: "done", message_id: "a1" },
        Date.parse("2026-05-07T10:00:01.000Z"),
      ),
      evt(
        3,
        "runtime.engine.cost",
        { engine: "claude", input_tokens: "10" },
        Date.parse("2026-05-07T10:00:05.000Z"),
      ),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock?.kind).toBe("tool");
    expect((toolBlock as { elapsedMs?: number }).elapsedMs).toBe(5000);
  });

  it("should keep two AssistantMessages from the same turn as separate blocks", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "First",
        message_id: "a1",
      }),
      evt(3, "message.assistant.delta", {
        text: "First message.",
        message_id: "a1",
      }),
      evt(4, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(5, "tool.call.completed", {
        tool_use_id: "t1",
        content: "ok",
      }),
      evt(6, "message.assistant.text_delta", {
        text: "Second",
        message_id: "a2",
      }),
      evt(7, "message.assistant.delta", {
        text: "Second message.",
        message_id: "a2",
      }),
    ]);

    const blocks = turns[0]!.blocks;
    const textBlocks = blocks.filter((b) => b.kind === "assistant");
    expect(textBlocks.map((b) => (b as { text: string }).text)).toEqual([
      "First message.",
      "Second message.",
    ]);
  });

  it("should append to the existing thinking block when text_delta interleaves between thinking_deltas", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking_delta", {
        text: "The user is asking me to ",
        message_id: "a1",
      }),
      evt(3, "message.assistant.text_delta", {
        text: "代码评审是",
        message_id: "a1",
      }),
      evt(4, "message.assistant.thinking_delta", {
        text: "continue writing.",
        message_id: "a1",
      }),
      evt(5, "message.assistant.text_delta", {
        text: "团队知识传递。",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "thinking",
        text: "The user is asking me to continue writing.",
        messageId: "a1",
        sealed: false,
      },
      {
        kind: "assistant",
        text: "代码评审是团队知识传递。",
        messageId: "a1",
        sealed: false,
      },
    ]);
  });

  it("should dedup re-delivered thinking_delta / text_delta so a phantom block doesn't appear after the canonical sealed", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking_delta", {
        text: "Let me think.",
        message_id: "a1",
      }),
      evt(3, "message.assistant.thinking", {
        text: "Let me think.",
        message_id: "a1",
      }),
      evt(4, "message.assistant.text_delta", {
        text: "Done.",
        message_id: "a1",
      }),
      evt(5, "message.assistant.delta", {
        text: "Done.",
        message_id: "a1",
      }),
      evt(6, "message.assistant.thinking_delta", {
        text: "Let me think.",
        message_id: "a1",
      }),
      evt(7, "message.assistant.text_delta", {
        text: "Done.",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "thinking",
        text: "Let me think.",
        messageId: "a1",
        sealed: true,
      },
      { kind: "assistant", text: "Done.", messageId: "a1", sealed: true },
    ]);
  });

  it("should fall back to legacy concatenation when message_id is absent (history replay shape)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi" }),
      evt(2, "message.assistant.delta", { text: "Part one. " }),
      evt(3, "message.assistant.delta", { text: "Part two." }),
    ]);

    const text = turns[0]!.blocks
      .filter((b) => b.kind === "assistant")
      .map((b) => (b as { text: string }).text)
      .join("|");
    expect(text).toBe("Part one. Part two.");
  });

  it("should drop a duplicate message.user event that the SSE adapter re-delivers via DB poll fallback", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.user", { text: "hi", message_id: "u1" }),
      evt(3, "message.assistant.delta", { text: "hello", message_id: "a1" }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.userText).toBe("hi");
  });

  it("should dedup SSE double-delivery of thinking, tool, and assistant canonical events", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking", {
        text: "let me think",
        message_id: "a1",
      }),
      evt(3, "message.assistant.thinking", {
        text: "let me think",
        message_id: "a1",
      }),
      evt(4, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(5, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(6, "tool.call.completed", { tool_use_id: "t1", content: "ok" }),
      evt(7, "message.assistant.delta", {
        text: "done",
        message_id: "a1",
      }),
      evt(8, "message.assistant.delta", {
        text: "done",
        message_id: "a1",
      }),
    ]);

    expect(turns).toHaveLength(1);
    const blocks = turns[0]!.blocks;
    expect(blocks.map((b) => b.kind)).toEqual([
      "thinking",
      "tool",
      "assistant",
    ]);
    const tools = blocks.filter((b) => b.kind === "tool");
    expect(tools).toHaveLength(1);
    const thinkingBlocks = blocks.filter((b) => b.kind === "thinking");
    expect(thinkingBlocks).toHaveLength(1);
  });

  it("should keep multi-block AssistantMessage segments interleaved with tools (history replay shape)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.delta", {
        text: "First message.",
        message_id: "a1",
      }),
      evt(3, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(4, "tool.call.completed", {
        tool_use_id: "t1",
        content: "ok",
      }),
      evt(5, "message.assistant.delta", {
        text: "Second message.",
        message_id: "a1",
      }),
    ]);

    expect(turns).toHaveLength(1);
    const blocks = turns[0]!.blocks;
    expect(blocks.map((b) => b.kind)).toEqual([
      "assistant",
      "tool",
      "assistant",
    ]);
    const textBlocks = blocks.filter((b) => b.kind === "assistant");
    expect(textBlocks.map((b) => (b as { text: string }).text)).toEqual([
      "First message.",
      "Second message.",
    ]);
  });

  it("should still open a new turn for a genuine subsequent user message in the same session", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.delta", { text: "hello", message_id: "a1" }),
      evt(3, "message.user", { text: "hi", message_id: "u2" }),
      evt(4, "message.assistant.delta", { text: "again", message_id: "a2" }),
    ]);

    expect(turns).toHaveLength(2);
    expect(turns[0]!.userText).toBe("hi");
    expect(turns[1]!.userText).toBe("hi");
  });
});
