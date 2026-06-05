/* ── Tool call types ──────────────────────────────────────── */

export type PrototypeToolCallStatus =
  | "success"
  | "running"
  | "cached"
  | "error";

export type PrototypeToolCallKind = "kb" | "fetch" | "skill" | "file" | "bash";

export interface PrototypeToolCall {
  id: string;
  kind: PrototypeToolCallKind;
  title: string;
  subtitle?: string;
  status: PrototypeToolCallStatus;
  input?: string;
  output?: string;
}

/* ── Conversation turn types ─────────────────────────────── */

export type ConversationBlock =
  | { kind: "assistant"; text: string; messageId?: string; sealed?: boolean }
  | {
      kind: "thinking";
      text: string;
      messageId?: string;
      sealed?: boolean;
      elapsedMs?: number;
    }
  | { kind: "tool"; tool: PrototypeToolCall; elapsedMs?: number };

export interface ConversationTurnAttachment {
  name: string;
  size: number;
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
  attachments?: ConversationTurnAttachment[];
  /** Unix epoch milliseconds (UTC). */
  userTimestamp?: number;
  /** Unix epoch milliseconds (UTC) of the last event currently associated
   * with this turn. Used as the fallback "finish time" when computing the
   * ``已处理 X 秒`` header for turns that have no thinking/tool work to
   * derive elapsedMs from (e.g. a direct one-shot assistant reply). */
  endTimestamp?: number;
}
