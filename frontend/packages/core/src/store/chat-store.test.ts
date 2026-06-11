import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { reduce, type ChatStoreState } from "./chat-store";

const makeState = (overrides: Partial<ChatStoreState> = {}): ChatStoreState => {
  return {
    sessionId: "sess-1",
    sessionStatus: "running",
    messages: [],
    todos: null,
    streaming: { messageId: null, text: "", thinking: "" },
    isStreaming: false,
    isInterrupting: false,
    lastSeq: 0,
    connection: {
      state: "connected",
      attempt: 0,
      lastSeq: 0,
      errorMessage: null,
      nextRetryAt: null,
    },
    attach: async () => {},
    detach: () => {},
    send: async () => {},
    interrupt: async () => {},
    reconnect: () => {},
    _ingest: () => {},
    ...overrides,
  };
};

const frame = (
  seq: number,
  eventType: string,
  payload: Record<string, string>,
): SessionEventDTO => ({
  seq,
  event: { event_type: eventType, payload },
});

describe("chat-store reducer", () => {
  describe("text streaming", () => {
    it("should accumulate text_delta into the streaming cursor", () => {
      const s0 = makeState();
      const s1 = {
        ...s0,
        ...reduce(
          s0,
          frame(1, "message.assistant.text_delta", {
            text: "Hello",
            message_id: "m1",
          }),
        ),
      };
      const s2 = {
        ...s1,
        ...reduce(
          s1,
          frame(2, "message.assistant.text_delta", {
            text: " world",
            message_id: "m1",
          }),
        ),
      };

      expect(s2.streaming.text).toBe("Hello world");
      expect(s2.streaming.messageId).toBe("m1");
      expect(s2.isStreaming).toBe(true);
      expect(s2.lastSeq).toBe(2);
    });

    it("should commit assistant message and clear streaming on canonical delta", () => {
      const start = makeState({
        streaming: { messageId: "m1", text: "partial", thinking: "" },
        isStreaming: true,
      });
      const next = {
        ...start,
        ...reduce(
          start,
          frame(5, "message.assistant.delta", {
            text: "Hello world",
            message_id: "m1",
          }),
        ),
      };

      expect(next.messages).toHaveLength(1);
      expect(next.messages[0]!.text).toBe("Hello world");
      expect(next.messages[0]!.role).toBe("assistant");
      expect(next.streaming.text).toBe("");
    });
  });

  describe("thinking streaming", () => {
    it("should accumulate thinking_delta into the streaming cursor", () => {
      const s0 = makeState();
      const s1 = {
        ...s0,
        ...reduce(
          s0,
          frame(1, "message.assistant.thinking_delta", {
            text: "Let me",
            message_id: "m1",
          }),
        ),
      };
      const s2 = {
        ...s1,
        ...reduce(
          s1,
          frame(2, "message.assistant.thinking_delta", {
            text: " think",
            message_id: "m1",
          }),
        ),
      };

      expect(s2.streaming.thinking).toBe("Let me think");
      expect(s2.streaming.text).toBe("");
    });

    it("should flush thinking buffer into committed message on full thinking event", () => {
      const start = makeState({
        streaming: { messageId: "m1", text: "", thinking: "Let me think" },
      });
      const next = {
        ...start,
        ...reduce(
          start,
          frame(2, "message.assistant.thinking", {
            text: "Let me think hard",
            message_id: "m1",
          }),
        ),
      };

      expect(next.messages[0]!.thinking).toEqual(["Let me think hard"]);
      expect(next.streaming.thinking).toBe("");
    });
  });

  describe("user message dedup", () => {
    it("should replace optimistic user id with real message_id on echo", () => {
      const optimistic = {
        id: "pending-abc",
        role: "user" as const,
        text: "hi",
        thinking: [],
        tools: [],
        stopReason: null,
        createdAt: new Date().toISOString(),
      };
      const start = makeState({ messages: [optimistic] });
      const next = {
        ...start,
        ...reduce(
          start,
          frame(1, "message.user", { text: "hi", message_id: "u1" }),
        ),
      };

      expect(next.messages).toHaveLength(1);
      expect(next.messages[0]!.id).toBe("u1");
    });

    it("should ignore duplicate user echo with the same message_id", () => {
      const committed = {
        id: "u1",
        role: "user" as const,
        text: "hi",
        thinking: [],
        tools: [],
        stopReason: null,
        createdAt: new Date().toISOString(),
      };
      const start = makeState({ messages: [committed], lastSeq: 0 });
      const next = {
        ...start,
        ...reduce(
          start,
          frame(42, "message.user", { text: "hi", message_id: "u1" }),
        ),
      };

      expect(next.messages).toHaveLength(1);
      expect(next.messages[0]).toEqual(committed);
      expect(next.lastSeq).toBe(42);
    });
  });

  describe("turn finalization", () => {
    it("should clear streaming flags on session.idle with stop_reason", () => {
      const start = makeState({
        isStreaming: true,
        isInterrupting: true,
        messages: [
          {
            id: "a1",
            role: "assistant",
            text: "partial",
            thinking: [],
            tools: [],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
      });
      const next = {
        ...start,
        ...reduce(
          start,
          frame(9, "session.idle", { stop_reason: "user_interrupt" }),
        ),
      };

      expect(next.isStreaming).toBe(false);
      expect(next.isInterrupting).toBe(false);
      expect(next.sessionStatus).toBe("idle");
      expect(next.messages[0]!.stopReason).toBe("user_interrupt");
    });

    it("should not stamp stop_reason for end_turn (clean finish)", () => {
      const start = makeState({
        messages: [
          {
            id: "a1",
            role: "assistant",
            text: "done",
            thinking: [],
            tools: [],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
        isStreaming: true,
      });
      const next = {
        ...start,
        ...reduce(start, frame(9, "session.idle", { stop_reason: "end_turn" })),
      };

      expect(next.messages[0]!.stopReason).toBeNull();
      expect(next.isStreaming).toBe(false);
    });

    it("should stamp error reason and clear streaming on run.failed", () => {
      const start = makeState({
        isStreaming: true,
        messages: [
          {
            id: "a1",
            role: "assistant",
            text: "",
            thinking: [],
            tools: [],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
      });
      const next = {
        ...start,
        ...reduce(start, frame(9, "run.failed", { message: "boom" })),
      };

      expect(next.isStreaming).toBe(false);
      expect(next.sessionStatus).toBe("failed");
      expect(next.messages[0]!.stopReason).toBe("error");
    });
  });

  describe("tools", () => {
    it("should attach tool call to current assistant message and complete it", () => {
      const start = makeState({
        messages: [
          {
            id: "a1",
            role: "assistant",
            text: "",
            thinking: [],
            tools: [],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
      });
      const afterStart = {
        ...start,
        ...reduce(
          start,
          frame(1, "tool.call.started", {
            tool_use_id: "t1",
            name: "Read",
            input: '{"path":"/x"}',
            message_id: "a1",
          }),
        ),
      };
      expect(afterStart.messages[0]!.tools).toHaveLength(1);
      expect(afterStart.messages[0]!.tools[0]!.name).toBe("Read");

      const afterComplete = {
        ...afterStart,
        ...reduce(
          afterStart,
          frame(2, "tool.call.completed", {
            tool_use_id: "t1",
            content: "ok",
            is_error: "false",
          }),
        ),
      };
      expect(afterComplete.messages[0]!.tools[0]!.output).toBe("ok");
      expect(afterComplete.messages[0]!.tools[0]!.isError).toBe(false);
    });

    it("should stream tool input via input_delta then reconcile on started", () => {
      const start = makeState({
        messages: [
          {
            id: "a1",
            role: "assistant",
            text: "",
            thinking: [],
            tools: [],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
      });
      // First input_delta builds a provisional card before started.
      const afterDelta1 = {
        ...start,
        ...reduce(
          start,
          frame(1, "tool.call.input_delta", {
            tool_use_id: "t1",
            name: "Write",
            text: '{"file_path":"/a",',
            message_id: "a1",
          }),
        ),
      };
      expect(afterDelta1.messages[0]!.tools).toHaveLength(1);
      expect(afterDelta1.isStreaming).toBe(true);

      const afterDelta2 = {
        ...afterDelta1,
        ...reduce(
          afterDelta1,
          frame(2, "tool.call.input_delta", {
            tool_use_id: "t1",
            text: '"content":"hi"}',
            message_id: "a1",
          }),
        ),
      };
      expect(afterDelta2.messages[0]!.tools[0]!.input).toBe(
        '{"file_path":"/a","content":"hi"}',
      );

      // started reconciles the same card (no duplicate) with canonical input.
      const afterStart = {
        ...afterDelta2,
        ...reduce(
          afterDelta2,
          frame(3, "tool.call.started", {
            tool_use_id: "t1",
            name: "Write",
            input: '{"file_path":"/a.txt","content":"hi"}',
            message_id: "a1",
          }),
        ),
      };
      expect(afterStart.messages[0]!.tools).toHaveLength(1);
      expect(afterStart.messages[0]!.tools[0]!.input).toBe(
        '{"file_path":"/a.txt","content":"hi"}',
      );
    });

    it("should accumulate output_delta then let completed replace it", () => {
      const start = makeState({
        messages: [
          {
            id: "a1",
            role: "assistant",
            text: "",
            thinking: [],
            tools: [
              {
                id: "t1",
                name: "Bash",
                input: "{}",
                output: null,
                isError: false,
              },
            ],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
      });
      const afterOut1 = {
        ...start,
        ...reduce(
          start,
          frame(1, "tool.call.output_delta", { tool_use_id: "t1", text: "a" }),
        ),
      };
      const afterOut2 = {
        ...afterOut1,
        ...reduce(
          afterOut1,
          frame(2, "tool.call.output_delta", { tool_use_id: "t1", text: "b" }),
        ),
      };
      expect(afterOut2.messages[0]!.tools[0]!.output).toBe("ab");

      const afterComplete = {
        ...afterOut2,
        ...reduce(
          afterOut2,
          frame(3, "tool.call.completed", {
            tool_use_id: "t1",
            content: "final",
            is_error: "false",
          }),
        ),
      };
      expect(afterComplete.messages[0]!.tools[0]!.output).toBe("final");
    });
  });

  describe("seq tracking", () => {
    it("should advance lastSeq monotonically and not regress on out-of-order frames", () => {
      const s0 = makeState();
      const s1 = {
        ...s0,
        ...reduce(s0, frame(5, "session.update", { status: "running" })),
      };
      const s2 = {
        ...s1,
        ...reduce(s1, frame(3, "session.update", { status: "running" })),
      };
      expect(s2.lastSeq).toBe(5);
    });
  });
});
