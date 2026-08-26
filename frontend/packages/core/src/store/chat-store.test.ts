import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { reduce, useChatStore, type ChatStoreState } from "./chat-store";

const makeState = (overrides: Partial<ChatStoreState> = {}): ChatStoreState => {
  return {
    sessionId: "sess-1",
    sessionStatus: "running",
    messages: [],
    todos: null,
    streaming: { messageId: null, assistantId: null, text: "", thinking: "" },
    isStreaming: false,
    isInterrupting: false,
    queue: [],
    queuePaused: false,
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
    enqueue: async () => {},
    editQueued: async () => {},
    deleteQueued: async () => {},
    resumeQueue: async () => {},
    steerQueued: async () => {},
    refreshQueue: async () => {},
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
        streaming: {
          messageId: "m1",
          assistantId: null,
          text: "partial",
          thinking: "",
        },
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
        streaming: {
          messageId: "m1",
          assistantId: null,
          text: "",
          thinking: "Let me think",
        },
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

  describe("kernel turn-id reuse (assistant events carry the user echo's message_id)", () => {
    // Real kernel streams scope ``message_id`` to the TURN: the user echo
    // and every assistant event of the turn share one id (repro:
    // kernel.db events 8–24 — user_message, thinking, tool_use,
    // assistant_message ×2, session_idle, all on the same message_id).
    const mid = "turn-1";
    const apply = (
      state: ChatStoreState,
      ...frames: SessionEventDTO[]
    ): ChatStoreState =>
      frames.reduce<ChatStoreState>(
        (s, f) => ({ ...s, ...reduce(s, f) }),
        state,
      );

    it("should keep assistant output out of the user bubble when message_id is reused", () => {
      const s = apply(
        makeState(),
        frame(1, "message.user", { text: "check NVDA", message_id: mid }),
        frame(2, "message.assistant.thinking", {
          text: "let me look",
          message_id: mid,
        }),
        frame(3, "tool.call.started", {
          tool_use_id: "t1",
          name: "stock_quote",
          input: "{}",
          message_id: mid,
        }),
        frame(4, "tool.call.completed", {
          tool_use_id: "t1",
          content: "211.94",
          is_error: "false",
        }),
        frame(5, "message.assistant.delta", {
          text: "NVDA closed at $211.94",
          message_id: mid,
        }),
      );

      expect(s.messages).toHaveLength(2);
      const [user, assistant] = s.messages;
      expect(user!.role).toBe("user");
      expect(user!.text).toBe("check NVDA");
      expect(user!.thinking).toEqual([]);
      expect(user!.tools).toEqual([]);
      expect(assistant!.role).toBe("assistant");
      expect(assistant!.id).not.toBe(user!.id);
      expect(assistant!.thinking).toEqual(["let me look"]);
      expect(assistant!.tools).toHaveLength(1);
      expect(assistant!.tools[0]!.output).toBe("211.94");
      expect(assistant!.text).toBe("NVDA closed at $211.94");
    });

    it("should route streamed tool input to the assistant entry, not the user echo", () => {
      const s = apply(
        makeState(),
        frame(1, "message.user", { text: "write it", message_id: mid }),
        frame(2, "tool.call.input_delta", {
          tool_use_id: "t1",
          name: "Write",
          text: '{"content":',
          message_id: mid,
        }),
        frame(3, "tool.call.input_delta", {
          tool_use_id: "t1",
          text: '"hi"}',
          message_id: mid,
        }),
      );

      expect(s.messages).toHaveLength(2);
      expect(s.messages[0]!.role).toBe("user");
      expect(s.messages[0]!.tools).toEqual([]);
      expect(s.messages[1]!.role).toBe("assistant");
      expect(s.messages[1]!.tools[0]!.input).toBe('{"content":"hi"}');
    });

    it("should open a fresh entry after a canonical delta instead of overwriting the committed text", () => {
      // One turn, two assistant messages separated by a tool block —
      // the second commit must not clobber the first answer.
      const s = apply(
        makeState(),
        frame(1, "message.user", { text: "check NVDA", message_id: mid }),
        frame(2, "message.assistant.delta", {
          text: "first answer",
          message_id: mid,
        }),
        frame(3, "message.assistant.thinking", {
          text: "double-checking",
          message_id: mid,
        }),
        frame(4, "tool.call.started", {
          tool_use_id: "t2",
          name: "stock_quote",
          input: "{}",
          message_id: mid,
        }),
        frame(5, "tool.call.completed", {
          tool_use_id: "t2",
          content: "ok",
          is_error: "false",
        }),
        frame(6, "message.assistant.delta", {
          text: "nothing to add",
          message_id: mid,
        }),
      );

      expect(s.messages).toHaveLength(3);
      expect(s.messages[1]!.role).toBe("assistant");
      expect(s.messages[1]!.text).toBe("first answer");
      expect(s.messages[2]!.role).toBe("assistant");
      expect(s.messages[2]!.thinking).toEqual(["double-checking"]);
      expect(s.messages[2]!.tools).toHaveLength(1);
      expect(s.messages[2]!.text).toBe("nothing to add");
      // Ids stay unique — they key React rendering.
      expect(new Set(s.messages.map((m) => m.id)).size).toBe(3);
    });

    it("should not reuse the user echo's id for the run.failed placeholder", () => {
      const s = apply(
        makeState(),
        frame(1, "message.user", { text: "boom pls", message_id: mid }),
        frame(2, "run.failed", { message: "boom", message_id: mid }),
      );

      expect(s.messages).toHaveLength(2);
      expect(s.messages[0]!.role).toBe("user");
      expect(s.messages[0]!.text).toBe("boom pls");
      expect(s.messages[1]!.role).toBe("assistant");
      expect(s.messages[1]!.id).not.toBe(s.messages[0]!.id);
      expect(s.messages[1]!.text).toBe("[boom]");
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

    it("should advance lastSeq from history-source envelopes (durable seq space)", () => {
      const s0 = makeState({ lastSeq: 10 });
      const next = {
        ...s0,
        ...reduce(s0, frame(900, "session.update", { status: "running" }), {
          source: "history",
        }),
      };
      expect(next.lastSeq).toBe(900);
    });

    it("should NOT advance lastSeq from live-source envelopes (kernel-local seq space)", () => {
      // The history cursor is durable-store space; a live frame's seq is the
      // kernel's independent local counter. Folding it in would corrupt the
      // ``after_seq`` handed to the server on reconnect.
      const s0 = makeState({ lastSeq: 10 });
      const next = {
        ...s0,
        ...reduce(s0, frame(9999, "session.update", { status: "running" }), {
          source: "live",
        }),
      };
      expect(next.lastSeq).toBe(10);
    });

    it("should still apply a live envelope's content while leaving the cursor alone", () => {
      const s0 = makeState({ lastSeq: 10 });
      const next = {
        ...s0,
        ...reduce(
          s0,
          {
            seq: 7,
            event: {
              event_type: "message.assistant.text_delta",
              payload: { text: "Hi", message_id: "m1" },
            },
            event_uid: null,
          },
          { source: "live" },
        ),
      };
      expect(next.streaming.text).toBe("Hi");
      expect(next.lastSeq).toBe(10);
    });
  });
});

describe("chat-store _ingest — event_uid dedup across history/live", () => {
  const uidFrame = (
    seq: number,
    uid: string | null,
    eventType: string,
    payload: Record<string, string>,
  ): SessionEventDTO => ({
    seq,
    event: { event_type: eventType, payload },
    event_uid: uid,
  });

  it("should collapse the same persisted event delivered via history replay and live stream", () => {
    const store = useChatStore.getState();
    store.detach(); // reset messages + the seen-uid set
    // ``message.assistant.thinking`` has NO reducer-level dedup — every
    // application appends to ``thinking[]`` — so a collapsed duplicate can
    // only come from the uid gate in ``_ingest``.
    // History replay: durable seq 900.
    useChatStore.getState()._ingest(
      uidFrame(900, "uid-x", "message.assistant.thinking", {
        text: "pondering",
        message_id: "a1",
      }),
      { source: "history" },
    );
    // Live redelivery of the SAME event: kernel-local seq 5, same uid.
    useChatStore.getState()._ingest(
      uidFrame(5, "uid-x", "message.assistant.thinking", {
        text: "pondering",
        message_id: "a1",
      }),
      { source: "live" },
    );
    const state = useChatStore.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]!.thinking).toEqual(["pondering"]);
    // Cursor holds the history seq; the live duplicate neither re-applied
    // nor advanced anything.
    expect(state.lastSeq).toBe(900);
    useChatStore.getState().detach();
  });

  it("should NOT drop a different live event whose kernel seq collides with a history seq", () => {
    const store = useChatStore.getState();
    store.detach();
    useChatStore
      .getState()
      ._ingest(
        uidFrame(2, "uid-a", "message.user", { text: "one", message_id: "u1" }),
        { source: "history" },
      );
    // Different event (different uid), numerically identical seq from the
    // OTHER space — must flow through.
    useChatStore
      .getState()
      ._ingest(
        uidFrame(2, "uid-b", "message.user", { text: "two", message_id: "u2" }),
        { source: "live" },
      );
    expect(useChatStore.getState().messages).toHaveLength(2);
    useChatStore.getState().detach();
  });

  it("should keep uid-less delta frames flowing (never uid-deduped)", () => {
    const store = useChatStore.getState();
    store.detach();
    useChatStore.getState()._ingest(
      uidFrame(0, null, "message.assistant.text_delta", {
        text: "He",
        message_id: "m1",
      }),
      { source: "live" },
    );
    useChatStore.getState()._ingest(
      uidFrame(0, null, "message.assistant.text_delta", {
        text: "y",
        message_id: "m1",
      }),
      { source: "live" },
    );
    const state = useChatStore.getState();
    expect(state.streaming.text).toBe("Hey");
    expect(state.lastSeq).toBe(0);
    useChatStore.getState().detach();
  });
});
