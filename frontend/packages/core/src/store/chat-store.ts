import { create } from "zustand";
import type { SessionDetail, TodoItem } from "@valuz/shared";
import {
  parseTodosUpdate,
  sessionsApi,
  type SessionEventDTO,
} from "../api/sessions-api";
import {
  createSessionStreamController,
  type SessionStreamSnapshot,
  type SessionStreamState,
} from "../agent/session-stream";

export type ChatRole = "user" | "assistant";

export interface ChatToolUse {
  id: string;
  name: string;
  input: string;
  output: string | null;
  isError: boolean;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  thinking: string[];
  tools: ChatToolUse[];
  /** ``user_interrupt`` if the assistant message was cut short by Stop. */
  stopReason: string | null;
  createdAt: string;
}

interface ChatStreamCursor {
  /** Current ``message_id`` whose deltas are being accumulated. */
  messageId: string | null;
  text: string;
  thinking: string;
}

export interface ChatStoreState {
  sessionId: string | null;
  sessionStatus: SessionDetail["status"] | null;
  messages: ChatMessage[];
  todos: TodoItem[] | null;
  /** Live preview of the in-flight assistant message (text + thinking). */
  streaming: ChatStreamCursor;
  /** True between user send and the next ``session.idle`` / ``run.failed``. */
  isStreaming: boolean;
  /** Set true while an interrupt request is in flight; cleared when stream ends. */
  isInterrupting: boolean;
  /** Last seen event seq — used as resume cursor. */
  lastSeq: number;
  /** Connection lifecycle from session-stream controller. */
  connection: SessionStreamSnapshot;

  // Actions ------------------------------------------------------------
  attach: (sessionId: string) => Promise<void>;
  detach: () => void;
  send: (
    prompt: string,
    opts?: { providerId?: string | null; modelId?: string | null },
  ) => Promise<void>;
  interrupt: () => Promise<void>;
  reconnect: () => void;
  // Test/internal helper — feed an event into the reducer. Exposed so
  // the hook can pipe controller events through and so unit tests can
  // exercise reducer logic without a live SSE source.
  _ingest: (event: SessionEventDTO) => void;
}

const emptyCursor = (): ChatStreamCursor => ({
  messageId: null,
  text: "",
  thinking: "",
});

const emptyConnection = (): SessionStreamSnapshot => ({
  state: "idle",
  attempt: 0,
  lastSeq: 0,
  errorMessage: null,
  nextRetryAt: null,
});

const generateId = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

let activeController: ReturnType<typeof createSessionStreamController> | null =
  null;

// Monotonic token guarding ``attach`` against React-StrictMode (dev) double
// mount + the detach/attach race it triggers: the effect runs
// mount → cleanup(detach) → mount(attach), so two attach() calls for the SAME
// session can be in flight at once. The old ``get().sessionId !== sessionId``
// guards can't tell them apart (both see sessionId===A), so a superseded
// attach (or the interleaved detach) could leave ``messages: []`` as the final
// write → the intermittent "empty until refresh" chat. Each attach captures a
// generation at start; ``detach`` and the next ``attach`` bump it, so a stale
// attach bails before mutating and only the latest attach hydrates.
let attachGeneration = 0;

const stopActiveController = () => {
  if (activeController) {
    activeController.stop();
    activeController = null;
  }
};

export const useChatStore = create<ChatStoreState>((set, get) => ({
  sessionId: null,
  sessionStatus: null,
  messages: [],
  todos: null,
  streaming: emptyCursor(),
  isStreaming: false,
  isInterrupting: false,
  lastSeq: 0,
  connection: emptyConnection(),

  attach: async (sessionId: string) => {
    if (get().sessionId === sessionId) return;
    const myGen = ++attachGeneration;
    stopActiveController();
    set({
      sessionId,
      sessionStatus: null,
      messages: [],
      todos: null,
      streaming: emptyCursor(),
      isStreaming: false,
      isInterrupting: false,
      lastSeq: 0,
      connection: emptyConnection(),
    });

    // 1. Fetch detail to know the current status + locked model.
    const detail = await sessionsApi.get(sessionId);
    // Bail if a newer attach/detach superseded us (StrictMode double mount or
    // a real session switch). ``myGen`` is stricter than the old sessionId
    // check — two concurrent attaches for the SAME session both saw
    // sessionId===A, so only the generation token can distinguish them.
    if (myGen !== attachGeneration) return;
    set({
      sessionStatus: detail.status,
      todos: detail.todos ?? null,
    });

    // 2. Replay history events so the chat list is hydrated before the
    //    live subscription starts.
    const history = await sessionsApi.listEvents(sessionId, 0);
    if (myGen !== attachGeneration) return;
    for (const item of history.items) {
      get()._ingest(item);
    }

    // 3. Open live SSE subscription. The controller resumes from the
    //    seq it last saw, so a mid-flight reconnect won't double-replay
    //    the history we already ingested.
    activeController = createSessionStreamController({
      sessionId,
      startSeq: get().lastSeq,
      onEvent: (event) => get()._ingest(event),
      onStateChange: (snapshot) => {
        if (get().sessionId !== sessionId) return;
        set({ connection: snapshot });
      },
    });
    activeController.start();
  },

  detach: () => {
    // Invalidate any in-flight attach so it can't write stale state after we
    // reset (StrictMode cleanup runs detach between the two attach mounts).
    attachGeneration += 1;
    stopActiveController();
    set({
      sessionId: null,
      sessionStatus: null,
      messages: [],
      todos: null,
      streaming: emptyCursor(),
      isStreaming: false,
      isInterrupting: false,
      lastSeq: 0,
      connection: emptyConnection(),
    });
  },

  send: async (prompt, opts = {}) => {
    const { sessionId, isStreaming } = get();
    if (!sessionId) throw new Error("No session attached");
    if (isStreaming) throw new Error("A turn is already in progress");
    // Optimistic user message — the server will eventually echo a
    // ``message.user`` event we'll ignore via id de-dup.
    const optimistic: ChatMessage = {
      id: `pending-${generateId()}`,
      role: "user",
      text: prompt,
      thinking: [],
      tools: [],
      stopReason: null,
      createdAt: new Date().toISOString(),
    };
    set((s) => ({
      messages: [...s.messages, optimistic],
      isStreaming: true,
      sessionStatus: "running",
    }));
    try {
      await sessionsApi.sendMessage(
        sessionId,
        prompt,
        opts.providerId ?? null,
        opts.modelId ?? null,
      );
    } catch (err) {
      // Roll back optimistic state — the turn never started.
      set((s) => ({
        messages: s.messages.filter((m) => m.id !== optimistic.id),
        isStreaming: false,
      }));
      throw err;
    }
  },

  interrupt: async () => {
    const { sessionId, isStreaming } = get();
    if (!sessionId || !isStreaming) return;
    set({ isInterrupting: true });
    try {
      await sessionsApi.interrupt(sessionId);
    } catch (err) {
      // Server-side failure — clear the optimistic flag so the Stop
      // button doesn't lock; the user can retry.
      set({ isInterrupting: false });
      throw err;
    }
    // Don't clear isInterrupting here — wait for the stream to finalise
    // (session.idle / session.update with status=cancelled).
  },

  reconnect: () => {
    activeController?.reconnect();
  },

  _ingest: (event: SessionEventDTO) => {
    set((state) => reduce(state, event));
  },
}));

/**
 * Pure reducer: given current state and an SSE envelope, return the
 * next state. Extracted for unit tests.
 */
export const reduce = (
  state: ChatStoreState,
  envelope: SessionEventDTO,
): Partial<ChatStoreState> => {
  const seq = envelope.seq;
  const { event_type, payload } = envelope.event;
  const messageId = payload.message_id ?? null;

  // Always advance the resume cursor.
  const nextLastSeq = Math.max(state.lastSeq, seq);

  switch (event_type) {
    case "message.user": {
      // De-dup: if the trailing message is an optimistic user message
      // with matching text, replace its id rather than appending.
      const text = payload.text ?? "";
      if (
        messageId &&
        state.messages.some((m) => m.role === "user" && m.id === messageId)
      ) {
        return { lastSeq: nextLastSeq };
      }
      const trailing = state.messages[state.messages.length - 1];
      if (
        trailing &&
        trailing.role === "user" &&
        trailing.id.startsWith("pending-") &&
        trailing.text === text
      ) {
        const updated: ChatMessage = {
          ...trailing,
          id: messageId ?? trailing.id,
        };
        return {
          messages: [...state.messages.slice(0, -1), updated],
          lastSeq: nextLastSeq,
        };
      }
      return {
        messages: [
          ...state.messages,
          {
            id: messageId ?? `user-${generateId()}`,
            role: "user",
            text,
            thinking: [],
            tools: [],
            stopReason: null,
            createdAt: new Date().toISOString(),
          },
        ],
        lastSeq: nextLastSeq,
      };
    }

    case "message.assistant.text_delta": {
      const text = payload.text ?? "";
      return {
        streaming: {
          messageId: messageId ?? state.streaming.messageId,
          text: state.streaming.text + text,
          thinking: state.streaming.thinking,
        },
        isStreaming: true,
        lastSeq: nextLastSeq,
      };
    }

    case "message.assistant.thinking_delta": {
      const text = payload.text ?? "";
      return {
        streaming: {
          messageId: messageId ?? state.streaming.messageId,
          text: state.streaming.text,
          thinking: state.streaming.thinking + text,
        },
        isStreaming: true,
        lastSeq: nextLastSeq,
      };
    }

    case "message.assistant.thinking": {
      // Full thinking block — flush the streaming preview into the
      // committed assistant message. The renderer shows thinking[]
      // dimmed/italic above the assistant turn body.
      const text = payload.text ?? state.streaming.thinking;
      const target = ensureAssistantMessage(state.messages, messageId);
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        thinking: [...target.thinking, text],
      });
      return {
        messages: updatedMessages,
        streaming: {
          messageId: state.streaming.messageId,
          text: state.streaming.text,
          thinking: "",
        },
        lastSeq: nextLastSeq,
      };
    }

    case "message.assistant.delta": {
      // Canonical end-of-message text — flush streamingText into the
      // committed assistant message and clear the cursor.
      const text = payload.text ?? state.streaming.text;
      const target = ensureAssistantMessage(state.messages, messageId);
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        text,
      });
      return {
        messages: updatedMessages,
        streaming: {
          messageId: null,
          text: "",
          thinking: state.streaming.thinking,
        },
        lastSeq: nextLastSeq,
      };
    }

    case "tool.call.started": {
      const target = ensureAssistantMessage(state.messages, messageId);
      const tool: ChatToolUse = {
        id: payload.tool_use_id ?? payload.id ?? `tool-${generateId()}`,
        name: payload.name ?? "tool",
        input: payload.input ?? "",
        output: null,
        isError: false,
      };
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        tools: [...target.tools, tool],
      });
      return { messages: updatedMessages, lastSeq: nextLastSeq };
    }

    case "tool.call.completed": {
      const toolId = payload.tool_use_id ?? payload.id ?? "";
      const isError = payload.is_error === "true";
      const updatedMessages = state.messages.map((msg) => {
        if (!msg.tools.some((t) => t.id === toolId)) return msg;
        return {
          ...msg,
          tools: msg.tools.map((t) =>
            t.id === toolId
              ? { ...t, output: payload.content ?? "", isError }
              : t,
          ),
        };
      });
      return { messages: updatedMessages, lastSeq: nextLastSeq };
    }

    case "session.todos.update": {
      const todos = parseTodosUpdate(envelope);
      return { todos, lastSeq: nextLastSeq };
    }

    case "session.idle": {
      const stopReason = payload.stop_reason ?? null;
      // Stamp stop_reason on the last assistant message if the turn
      // was interrupted, so the UI can show "(stopped)" indicator.
      let messages = state.messages;
      if (stopReason && stopReason !== "end_turn") {
        const last = messages[messages.length - 1];
        if (last?.role === "assistant") {
          messages = [...messages.slice(0, -1), { ...last, stopReason }];
        }
      }
      return {
        messages,
        sessionStatus: "idle",
        isStreaming: false,
        isInterrupting: false,
        streaming: emptyCursor(),
        lastSeq: nextLastSeq,
      };
    }

    case "session.update": {
      const status = payload.status as SessionDetail["status"] | undefined;
      if (!status) return { lastSeq: nextLastSeq };
      const isTerminal =
        status === "idle" ||
        status === "failed" ||
        status === "cancelled" ||
        status === "archived";
      return {
        sessionStatus: status,
        isStreaming: isTerminal ? false : state.isStreaming,
        isInterrupting: isTerminal ? false : state.isInterrupting,
        streaming: isTerminal ? emptyCursor() : state.streaming,
        lastSeq: nextLastSeq,
      };
    }

    case "run.failed": {
      const message = payload.message ?? "Run failed";
      const last = state.messages[state.messages.length - 1];
      let messages = state.messages;
      if (last?.role === "assistant") {
        messages = [
          ...messages.slice(0, -1),
          {
            ...last,
            text: last.text || `[${message}]`,
            stopReason: "error",
          },
        ];
      } else {
        messages = [
          ...messages,
          {
            id: messageId ?? `error-${generateId()}`,
            role: "assistant",
            text: `[${message}]`,
            thinking: [],
            tools: [],
            stopReason: "error",
            createdAt: new Date().toISOString(),
          },
        ];
      }
      return {
        messages,
        sessionStatus: "failed",
        isStreaming: false,
        isInterrupting: false,
        streaming: emptyCursor(),
        lastSeq: nextLastSeq,
      };
    }

    default:
      return { lastSeq: nextLastSeq };
  }
};

const ensureAssistantMessage = (
  messages: ChatMessage[],
  messageId: string | null,
): ChatMessage => {
  const existing = messageId
    ? messages.find((m) => m.id === messageId && m.role === "assistant")
    : messages
        .slice()
        .reverse()
        .find((m) => m.role === "assistant" && !m.stopReason);
  if (existing) return existing;
  return {
    id: messageId ?? `assistant-${generateId()}`,
    role: "assistant",
    text: "",
    thinking: [],
    tools: [],
    stopReason: null,
    createdAt: new Date().toISOString(),
  };
};

const upsertAssistantMessage = (
  messages: ChatMessage[],
  target: ChatMessage,
  patch: Partial<ChatMessage>,
): ChatMessage[] => {
  const idx = messages.findIndex((m) => m.id === target.id);
  if (idx === -1) {
    return [...messages, { ...target, ...patch }];
  }
  return messages.map((m, i) => (i === idx ? { ...m, ...patch } : m));
};

/** Selector helpers — preferred over reading the whole store object. */
export const selectConnectionLabel = (state: SessionStreamState): string => {
  switch (state) {
    case "connecting":
      return "Connecting";
    case "connected":
      return "Live";
    case "reconnecting":
      return "Reconnecting";
    case "disconnected":
      return "Idle";
    case "error":
      return "Disconnected";
    case "idle":
      return "Not connected";
  }
};
