import { create } from "zustand";
import type { SessionDetail, TodoItem } from "@valuz/shared";
import {
  parseTodosUpdate,
  sessionsApi,
  type SessionEventDTO,
  type SessionMessageHostRef,
} from "../api/sessions-api";
import { queueApi, type QueuedInput } from "../api/queue-api";
import {
  createSessionStreamController,
  type SessionStreamSnapshot,
  type SessionStreamState,
} from "../agent/session-stream";
import { isLiveSnapshot } from "../conversation/conversation-utils";

export type ChatRole = "user" | "assistant";

export interface ChatToolUse {
  id: string;
  name: string;
  input: string;
  output: string | null;
  isError: boolean;
  /** Tool-scoped reasoning stream (``tool.call.thinking_delta``, live-only).
   * Kept apart from ``output`` — output is the tool's result stream. */
  thinking?: string;
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
  /**
   * Store id of the assistant entry currently receiving committed output
   * (thinking flushes / tool cards / canonical text). Kernel streams reuse
   * the TURN's ``message_id`` — the same id the ``message.user`` echo
   * carries — for every assistant event of the turn, so the entry cannot
   * be keyed by ``message_id`` alone: the cursor pins the open entry and a
   * canonical ``message.assistant.delta`` closes it, letting a later
   * thinking/tool block in the same turn open a fresh entry instead of
   * overwriting this one.
   */
  assistantId: string | null;
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
  /** Follow-up inputs queued while a turn is running (drain FIFO after it). */
  queue: QueuedInput[];
  /** True when an interrupt soft-paused the queue; awaits explicit resume. */
  queuePaused: boolean;
  /**
   * HISTORY cursor (durable-store seq) — used as the SSE resume
   * ``after_seq``. Advanced ONLY by history/replay envelopes (the REST
   * ``listEvents`` results ingested at attach); live SSE frames carry the
   * kernel-LOCAL seq — an independent space — and must never advance it.
   * (The stream controller additionally advances its own copy from
   * heartbeat frames, which are guaranteed history-space.)
   */
  lastSeq: number;
  /** Connection lifecycle from session-stream controller. */
  connection: SessionStreamSnapshot;

  // Actions ------------------------------------------------------------
  attach: (sessionId: string) => Promise<void>;
  detach: () => void;
  send: (
    prompt: string,
    opts?: {
      providerId?: string | null;
      modelId?: string | null;
      hostRef?: SessionMessageHostRef | null;
    },
  ) => Promise<void>;
  interrupt: () => Promise<void>;
  /** Append a follow-up input to the queue (drains after the active turn). */
  enqueue: (
    prompt: string,
    opts?: { providerId?: string | null; modelId?: string | null },
  ) => Promise<void>;
  editQueued: (queueId: string, prompt: string) => Promise<void>;
  deleteQueued: (queueId: string) => Promise<void>;
  resumeQueue: () => Promise<void>;
  /** Steer — send a queued item now, silently interrupting the active turn. */
  steerQueued: (queueId: string) => Promise<void>;
  refreshQueue: () => Promise<void>;
  reconnect: () => void;
  // Test/internal helper — feed an event into the reducer. Exposed so
  // the hook can pipe controller events through and so unit tests can
  // exercise reducer logic without a live SSE source. ``source`` tells the
  // reducer which seq space the envelope's ``seq`` belongs to (defaults to
  // ``"history"`` — REST replay); live SSE frames must pass ``"live"`` so
  // their kernel-local seq never advances the history cursor. Duplicate
  // deliveries across the two paths are collapsed by ``event_uid``.
  _ingest: (event: SessionEventDTO, opts?: { source?: IngestSource }) => void;
}

/**
 * Which seq space an envelope's ``seq`` belongs to. ``history`` = the
 * durable store (REST listEvents replay); ``live`` = the kernel's local
 * store (SSE frames). The two are independent counters — only history
 * envelopes may advance the resume cursor.
 */
export type IngestSource = "history" | "live";

const emptyCursor = (): ChatStreamCursor => ({
  messageId: null,
  assistantId: null,
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

// Bounded remember-set of ``event_uid``s already ingested for the attached
// session. Persisted events can arrive through BOTH paths — the attach-time
// REST history replay and the live SSE stream (whose reconnect backfill
// re-reads history) — with per-store seqs that cannot be compared, so the
// store-independent uid is the only valid dedup key. Reset on attach/detach.
// FIFO-trimmed so an arbitrarily long session can't grow it unbounded.
const SEEN_UIDS_MAX = 8192;
const seenEventUids = new Set<string>();
const seenEventUidOrder: string[] = [];

const resetSeenUids = () => {
  seenEventUids.clear();
  seenEventUidOrder.length = 0;
};

/** Returns true when the uid is NEW (process the event); false = duplicate. */
const rememberUid = (uid: string): boolean => {
  if (seenEventUids.has(uid)) return false;
  seenEventUids.add(uid);
  seenEventUidOrder.push(uid);
  while (seenEventUidOrder.length > SEEN_UIDS_MAX) {
    const evicted = seenEventUidOrder.shift();
    if (evicted !== undefined) seenEventUids.delete(evicted);
  }
  return true;
};

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
  queue: [],
  queuePaused: false,
  lastSeq: 0,
  connection: emptyConnection(),

  attach: async (sessionId: string) => {
    if (get().sessionId === sessionId) return;
    const myGen = ++attachGeneration;
    stopActiveController();
    resetSeenUids();
    set({
      sessionId,
      sessionStatus: null,
      messages: [],
      todos: null,
      streaming: emptyCursor(),
      isStreaming: false,
      isInterrupting: false,
      queue: [],
      queuePaused: false,
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

    // Hydrate any persisted input queue (survives reload / reconnect).
    void get().refreshQueue();

    // 2. Replay history events so the chat list is hydrated before the
    //    live subscription starts. These are REST reads — durable-store
    //    seq space — so they (and only they) advance the history cursor.
    const history = await sessionsApi.listEvents(sessionId, 0);
    if (myGen !== attachGeneration) return;
    for (const item of history.items) {
      get()._ingest(item, { source: "history" });
    }

    // 3. Open live SSE subscription. ``startSeq`` is the HISTORY cursor
    //    hydrated by the replay above, so the server-side backfill starts
    //    right after what we already ingested. Live frames are kernel-seq
    //    space: they never advance the cursor (the controller advances its
    //    own copy from heartbeats only) and dedup against the replay via
    //    ``event_uid``.
    activeController = createSessionStreamController({
      sessionId,
      startSeq: get().lastSeq,
      onEvent: (event) => get()._ingest(event, { source: "live" }),
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
    resetSeenUids();
    set({
      sessionId: null,
      sessionStatus: null,
      messages: [],
      todos: null,
      streaming: emptyCursor(),
      isStreaming: false,
      isInterrupting: false,
      queue: [],
      queuePaused: false,
      lastSeq: 0,
      connection: emptyConnection(),
    });
  },

  send: async (prompt, opts = {}) => {
    const { sessionId, isStreaming } = get();
    if (!sessionId) throw new Error("No session attached");
    if (isStreaming) {
      // A turn is in flight — queue this as a follow-up instead of failing.
      // It drains FIFO after the active turn (docs/design/session-input-queue).
      await get().enqueue(prompt, opts);
      return;
    }
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
        opts.hostRef ?? null,
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

  enqueue: async (prompt, opts = {}) => {
    const { sessionId } = get();
    if (!sessionId) throw new Error("No session attached");
    const list = await queueApi.enqueue(sessionId, prompt, {
      providerId: opts.providerId ?? null,
      modelId: opts.modelId ?? null,
    });
    if (get().sessionId !== sessionId) return;
    set({ queue: list.items, queuePaused: list.paused });
  },

  editQueued: async (queueId, prompt) => {
    const { sessionId } = get();
    if (!sessionId) return;
    const list = await queueApi.edit(sessionId, queueId, prompt);
    if (get().sessionId !== sessionId) return;
    set({ queue: list.items, queuePaused: list.paused });
  },

  deleteQueued: async (queueId) => {
    const { sessionId } = get();
    if (!sessionId) return;
    const list = await queueApi.remove(sessionId, queueId);
    if (get().sessionId !== sessionId) return;
    set({ queue: list.items, queuePaused: list.paused });
  },

  resumeQueue: async () => {
    const { sessionId } = get();
    if (!sessionId) return;
    const list = await queueApi.resume(sessionId);
    if (get().sessionId !== sessionId) return;
    set({ queue: list.items, queuePaused: list.paused });
  },

  steerQueued: async (queueId) => {
    const { sessionId } = get();
    if (!sessionId) return;
    // Send-now: the backend interrupts the active turn silently and dispatches
    // this item. The cut turn finalises as a clean idle; the drain-follower /
    // _ingest turn-boundary resync then refreshes the queue as it runs.
    const list = await queueApi.steer(sessionId, queueId);
    if (get().sessionId !== sessionId) return;
    set({ queue: list.items, queuePaused: list.paused });
  },

  refreshQueue: async () => {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const list = await queueApi.list(sessionId);
      if (get().sessionId !== sessionId) return;
      set({ queue: list.items, queuePaused: list.paused });
    } catch {
      // Best-effort — a transient queue fetch failure must not break chat.
    }
  },

  reconnect: () => {
    activeController?.reconnect();
  },

  _ingest: (event: SessionEventDTO, opts?: { source?: IngestSource }) => {
    // Cross-path dedup: a persisted event can be delivered by BOTH the
    // attach-time REST replay and the live SSE stream (reconnect backfill
    // included). Their seqs live in different spaces, so the uid is the
    // only valid identity; uid-less envelopes (live-only deltas, legacy
    // rows) flow through untouched — exactly today's behavior for them.
    if (event.event_uid && !rememberUid(event.event_uid)) return;
    const before = get().isStreaming;
    set((state) => reduce(state, event, opts));
    // Turn boundary (streaming true → false): a drained queue item just
    // dispatched / the queue settled. Resync the queue view so bubbles drop
    // as they run and ``paused`` / ``blocked`` states surface. See §8.4.
    if (before && !get().isStreaming) {
      void get().refreshQueue();
    }
  },
}));

/**
 * Pure reducer: given current state and an SSE envelope, return the
 * next state. Extracted for unit tests.
 *
 * ``opts.source`` declares the envelope's seq space: ``"history"``
 * (default — REST listEvents replay, durable-store seq) advances the
 * resume cursor; ``"live"`` (SSE frames, kernel-LOCAL seq) must NOT —
 * the two counters are independent and comparing/merging them corrupts
 * the ``after_seq`` handed back to the server on reconnect.
 */
export const reduce = (
  state: ChatStoreState,
  envelope: SessionEventDTO,
  opts?: { source?: IngestSource },
): Partial<ChatStoreState> => {
  const seq = envelope.seq;
  const { event_type, payload } = envelope.event;
  const messageId = payload.message_id ?? null;

  // Advance the resume cursor from HISTORY envelopes only (see above).
  const nextLastSeq =
    (opts?.source ?? "history") === "history"
      ? Math.max(state.lastSeq, seq)
      : state.lastSeq;

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

    // A ``live_snapshot`` frame carries the stream's absolute state rather
    // than the next increment — the kernel sends one per open stream when
    // a client joins mid-turn, because deltas emitted before it connected
    // are never persisted. Replace instead of append, or a reconnect
    // renders the recovered prefix twice. See
    // ``kernel/src/core/live_partial.py``.
    case "message.assistant.text_delta": {
      const text = payload.text ?? "";
      return {
        streaming: {
          ...state.streaming,
          messageId: messageId ?? state.streaming.messageId,
          text: isLiveSnapshot(payload) ? text : state.streaming.text + text,
        },
        isStreaming: true,
        lastSeq: nextLastSeq,
      };
    }

    case "message.assistant.thinking_delta": {
      const text = payload.text ?? "";
      return {
        streaming: {
          ...state.streaming,
          messageId: messageId ?? state.streaming.messageId,
          thinking: isLiveSnapshot(payload)
            ? text
            : state.streaming.thinking + text,
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
      const target = ensureAssistantMessage(state, messageId);
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        thinking: [...target.thinking, text],
      });
      return {
        messages: updatedMessages,
        streaming: {
          ...state.streaming,
          assistantId: target.id,
          thinking: "",
        },
        lastSeq: nextLastSeq,
      };
    }

    case "message.assistant.delta": {
      // Canonical end-of-message text — flush streamingText into the
      // committed assistant message and clear the cursor. Clearing
      // ``assistantId`` closes the entry: the kernel can emit several
      // assistant messages per turn (text → tools → text), and the next
      // thinking/tool event must open a fresh entry rather than overwrite
      // this one's text.
      const text = payload.text ?? state.streaming.text;
      const target = ensureAssistantMessage(state, messageId);
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        text,
      });
      return {
        messages: updatedMessages,
        streaming: {
          messageId: null,
          assistantId: null,
          text: "",
          thinking: state.streaming.thinking,
        },
        lastSeq: nextLastSeq,
      };
    }

    case "tool.call.started": {
      const target = ensureAssistantMessage(state, messageId);
      const toolId =
        payload.tool_use_id ?? payload.id ?? `tool-${generateId()}`;
      // A preceding tool.call.input_delta may already have built a
      // provisional card for this id (streaming the partial input).
      const streamed = target.tools.find((t) => t.id === toolId);
      const tool: ChatToolUse = {
        id: toolId,
        name: payload.name ?? streamed?.name ?? "tool",
        // Canonical full input replaces the partial-JSON preview.
        input: payload.input ?? streamed?.input ?? "",
        output: streamed?.output ?? null,
        isError: false,
      };
      const tools = streamed
        ? target.tools.map((t) => (t.id === toolId ? tool : t))
        : [...target.tools, tool];
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        tools,
      });
      return {
        messages: updatedMessages,
        streaming: { ...state.streaming, assistantId: target.id },
        lastSeq: nextLastSeq,
      };
    }

    case "tool.call.input_delta": {
      // Live partial tool-call input (non-persisted). First chunk builds a
      // provisional running card so large file writes show immediate
      // progress; later chunks accumulate. started reconciles to the
      // canonical full input.
      const toolId = payload.tool_use_id ?? "";
      if (!toolId) return { lastSeq: nextLastSeq };
      const text = payload.text ?? "";
      const target = ensureAssistantMessage(state, messageId);
      const existing = target.tools.find((t) => t.id === toolId);
      const nextTool: ChatToolUse = existing
        ? { ...existing, input: existing.input + text }
        : {
            id: toolId,
            name: payload.name ?? "tool",
            input: text,
            output: null,
            isError: false,
          };
      const tools = existing
        ? target.tools.map((t) => (t.id === toolId ? nextTool : t))
        : [...target.tools, nextTool];
      const updatedMessages = upsertAssistantMessage(state.messages, target, {
        tools,
      });
      return {
        messages: updatedMessages,
        streaming: { ...state.streaming, assistantId: target.id },
        isStreaming: true,
        lastSeq: nextLastSeq,
      };
    }

    case "tool.call.output_delta": {
      // Live streamed tool output (non-persisted) between started and
      // completed; accumulate onto the matching card. completed later
      // replaces it with the canonical aggregated output.
      const toolId = payload.tool_use_id ?? "";
      if (!toolId) return { lastSeq: nextLastSeq };
      const text = payload.text ?? "";
      const updatedMessages = state.messages.map((msg) => {
        if (!msg.tools.some((t) => t.id === toolId)) return msg;
        return {
          ...msg,
          tools: msg.tools.map((t) =>
            t.id === toolId ? { ...t, output: (t.output ?? "") + text } : t,
          ),
        };
      });
      return {
        messages: updatedMessages,
        isStreaming: true,
        lastSeq: nextLastSeq,
      };
    }

    case "tool.call.thinking_delta": {
      // Tool-scoped reasoning stream (live-only) — same accumulation shape as
      // output_delta but onto ``thinking``, never ``output`` (the result
      // stream, e.g. the A2UI JSONL generate_ui renders progressively).
      const toolId = payload.tool_use_id ?? "";
      if (!toolId) return { lastSeq: nextLastSeq };
      const text = payload.text ?? "";
      const updatedMessages = state.messages.map((msg) => {
        if (!msg.tools.some((t) => t.id === toolId)) return msg;
        return {
          ...msg,
          tools: msg.tools.map((t) =>
            t.id === toolId ? { ...t, thinking: (t.thinking ?? "") + text } : t,
          ),
        };
      });
      return {
        messages: updatedMessages,
        isStreaming: true,
        lastSeq: nextLastSeq,
      };
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
      // Carry-forward on a malformed frame (parser returns null) — same
      // semantics as the conversation page's SSE handler: a frame that
      // can't be parsed must not wipe the snapshot the panel already has.
      // A cleared todo list arrives as ``[]`` (truthy) and still lands.
      const todos = parseTodosUpdate(envelope);
      return { todos: todos ?? state.todos, lastSeq: nextLastSeq };
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
            id: assistantEntryId(messages, messageId),
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

/**
 * Pick the store id for a NEW assistant entry. The natural choice is the
 * event's ``message_id`` — but kernel streams reuse the TURN's id (the one
 * the ``message.user`` echo already claimed) for every assistant event, and
 * ids key both React rendering and upsert lookups, so a taken id must never
 * be shared across entries: fall back to a locally generated one.
 */
const assistantEntryId = (
  messages: ChatMessage[],
  messageId: string | null,
): string => {
  if (messageId && !messages.some((m) => m.id === messageId)) return messageId;
  return `assistant-${generateId()}`;
};

const ensureAssistantMessage = (
  state: Pick<ChatStoreState, "messages" | "streaming">,
  messageId: string | null,
): ChatMessage => {
  const { messages } = state;
  // The open entry pinned by the streaming cursor wins — with turn-scoped
  // message_ids the id alone cannot identify the entry (see ChatStreamCursor).
  const openId = state.streaming.assistantId;
  if (openId) {
    const open = messages.find(
      (m) => m.id === openId && m.role === "assistant",
    );
    if (open) return open;
  }
  const existing = messageId
    ? messages.find((m) => m.id === messageId && m.role === "assistant")
    : messages
        .slice()
        .reverse()
        .find((m) => m.role === "assistant" && !m.stopReason);
  if (existing) return existing;
  return {
    id: assistantEntryId(messages, messageId),
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
  // Role-scoped lookup: the turn's user echo can share ``target.id`` when
  // the kernel reuses the turn id — matching on id alone would patch the
  // assistant payload onto the user bubble.
  const idx = messages.findIndex(
    (m) => m.id === target.id && m.role === "assistant",
  );
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
