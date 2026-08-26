import type { CitationBundleV1 } from "./citation";

/* ── Tool call types ──────────────────────────────────────── */

export type PrototypeToolCallStatus =
  "success" | "running" | "cached" | "error";

export type PrototypeToolCallKind = "kb" | "fetch" | "skill" | "file" | "bash";

export interface PrototypeToolCall {
  id: string;
  kind: PrototypeToolCallKind;
  title: string;
  subtitle?: string;
  status: PrototypeToolCallStatus;
  input?: string;
  output?: string;
  /** Tool-scoped reasoning stream (``tool.call.thinking_delta``, live-only —
   * the ephemeral generate_ui session's thinking forwarded onto the calling
   * session). Accumulated separately from ``output`` on purpose: output IS
   * the tool's result stream (e.g. A2UI JSONL) and must not be polluted. */
  thinking?: string;
}

/* ── Background tasks (run_in_background shell commands) ──── */

export type BackgroundTaskStatus =
  "running" | "completed" | "failed" | "stopped";

/**
 * One background task the agent launched (``run_in_background`` Bash).
 * Derived by folding the persisted ``session.bg_task.*`` events, so it is
 * correct on live streams AND on history replay: a task that started in an
 * earlier page visit and is still running shows as ``running`` after
 * re-entering the conversation.
 */
export interface BackgroundTaskState {
  taskId: string;
  /** tool_use_id of the Bash call that spawned it — lets a card attach to
   *  the matching tool block if needed. */
  toolUseId?: string;
  description: string;
  status: BackgroundTaskStatus;
  /** Human summary from the terminal event (e.g. exit-code line). */
  summary?: string;
  /** Path of the file the task's stdout/stderr is streamed to. */
  outputFile?: string;
  /** Unix epoch ms of the started event, when the wire carried one. */
  startedAtMs?: number;
}

/* ── Conversation turn types ─────────────────────────────── */

export type ConversationBlock =
  // ``parentToolUseId`` (assistant/thinking/tool): set when the event was
  // produced INSIDE a subagent (Task/Agent tool run). Such blocks are
  // out-of-band relative to the lead's sequential flow — a background
  // agent's events arrive interleaved with the lead's live stream, so the
  // turn builder must not let them split or seal the lead's open streaming
  // block.
  | {
      kind: "assistant";
      text: string;
      messageId?: string;
      sealed?: boolean;
      parentToolUseId?: string;
      /** Present only on the canonical/final assistant block. */
      citationBundle?: CitationBundleV1;
    }
  | {
      kind: "thinking";
      text: string;
      messageId?: string;
      sealed?: boolean;
      elapsedMs?: number;
      parentToolUseId?: string;
    }
  | {
      kind: "tool";
      tool: PrototypeToolCall;
      elapsedMs?: number;
      parentToolUseId?: string;
    }
  // Context-compaction marker (``/compact`` or autocompact), for either
  // runtime. Label-only — the kernel ``compaction`` event's raw data is
  // intentionally not parsed for display; it just marks where the context
  // window was summarized within the turn.
  | { kind: "compaction"; messageId?: string };

export interface ConversationTurnAttachment {
  name: string;
  size: number;
}

/** Normalized per-turn token buckets emitted by ``runtime.engine.usage``. */
export interface ConversationTokenUsage {
  /** Uncached input tokens. */
  inputTokens: number;
  /** Output tokens, including provider-reported reasoning output. */
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  totalTokens: number;
  /** Cache reads divided by all input-side buckets; null with no input. */
  cacheHitRate: number | null;
  /** Models observed in the runtime's per-model usage breakdown. */
  models: string[];
}

export interface ConversationTurn {
  id: string;
  /** Seq of the user_message event this turn was built from. ``0`` for
   * live broadcast frames not yet persisted to the events DB; the
   * persisted copy arrives later with a real seq. The dedup logic in
   * ``effectiveTurns`` uses this directly instead of parsing the id,
   * which now uses ``message_id`` (a UUID) for stability across the
   * live → persisted transition. */
  userMessageSeq: number;
  userText: string;
  blocks: ConversationBlock[];
  failedMessage: string | null;
  /** The run ended because the user cancelled it (``run.failed`` with
   *  ``category === "user_interrupt"``). Rendered as a quiet grey line rather
   *  than the ``ErrorMessageCard`` a genuine failure gets. Optional: absent is
   *  treated as ``false``. */
  cancelled?: boolean;
  /** The run ended because the runtime/agent subprocess was torn down or
   *  crashed mid-turn (``category === "interrupted"`` — NOT a user action).
   *  Rendered as the same quiet grey line as ``cancelled`` but with a distinct
   *  label so a system interruption is not misattributed to the user ("用户取消
   *  了当前对话"). Optional: absent is treated as ``false``. */
  interrupted?: boolean;
  /** The run stopped because the runtime hit one of its per-run budgets
   *  (``stop_reason.type === "budget_exhausted"``). NOT an error and NOT a
   *  cancel: the turn is recorded as completed, so without an explicit marker
   *  a ``max_cost`` stop renders as an empty assistant area with no
   *  explanation — the CLI rejects the query before any model call, so the
   *  turn has no blocks at all. Rendered as a visible notice with a retry
   *  affordance. ``"unknown"`` = the stop reason said ``budget_exhausted``
   *  but carried no reason we recognize; still surfaced, just without naming
   *  a cause. Optional: absent = the turn ended within budget. */
  budgetHalt?: "max_cost" | "max_turns" | "unknown";
  /** Whether this turn's message carries a runtime-native fork anchor
   *  (terminal ``session.update`` frames stamp it). ``false`` disables
   *  "Fork from here"; ``undefined`` = recorded before the signal existed —
   *  keep the affordance, the server 409 backstops. */
  forkAnchor?: boolean;
  attachments?: ConversationTurnAttachment[];
  /** Unix epoch milliseconds (UTC). */
  userTimestamp?: number;
  /** Unix epoch milliseconds (CLIENT clock) at which the user pressed Send
   * for this turn.
   *
   * ``userTimestamp`` comes off the kernel's ``message.user`` event, which is
   * stamped when the RUNTIME started — after local warm-up or, in sandboxed /
   * cloud execution, after the whole instance boots. Anchoring the turn
   * header's elapsed counter on it therefore makes the counter jump back to
   * zero the moment the optimistic turn is replaced by the real one. The host
   * page carries the send time across that handover here so one continuous
   * counter spans "runtime starting" and "processing".
   *
   * Absent on history-loaded turns (nobody was watching the clock) — consumers
   * fall back to ``userTimestamp``. */
  clientSentAtMs?: number;
  /** Unix epoch milliseconds (UTC) of the last event currently associated
   * with this turn. Used as the fallback "finish time" when computing the
   * ``已处理 X 秒`` header for turns that have no thinking/tool work to
   * derive elapsedMs from (e.g. a direct one-shot assistant reply). */
  endTimestamp?: number;
  /** Token consumption accumulated across all runtime segments in this turn. */
  tokenUsage?: ConversationTokenUsage;
}
