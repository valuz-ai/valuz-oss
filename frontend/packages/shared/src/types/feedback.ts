/**
 * User outcome signals on assistant turns — mirrors ``api/openapi.yaml``
 * (``FeedbackRecord`` / ``RecordFeedbackRequest``). One row per
 * ``(user, message, action, block_ref)``; repeats bump ``occurrences``.
 * See docs/design/feedback-signals.md.
 */

export type FeedbackAction =
  | "rating"
  | "copy"
  | "regenerate"
  | "fork"
  | "share"
  | "research_share";

export type FeedbackValue = "up" | "down";

/** Closed set shared with the backend (``FEEDBACK_REASON_CODES``). */
export const FEEDBACK_REASON_CODES = [
  "inaccurate",
  "incomplete",
  "off_topic",
  "too_slow",
  "format",
  "unsafe",
  "other",
] as const;

export type FeedbackReasonCode = (typeof FEEDBACK_REASON_CODES)[number];

export interface FeedbackTarget {
  type: "message" | "session" | "share" | "research_message";
  id: string;
}

export interface FeedbackRecord {
  id: string;
  session_id: string;
  message_id: string;
  action: FeedbackAction;
  block_ref: string;
  value: FeedbackValue | null;
  reason_code: string | null;
  reason: string | null;
  target: FeedbackTarget | null;
  source: "ui" | "api" | "server";
  surface: string | null;
  occurrences: number;
  /** Unix epoch ms (UTC) — first occurrence. */
  created_at: number;
  /** Unix epoch ms (UTC) — latest occurrence. */
  updated_at: number;
  metadata: Record<string, unknown>;
}

export interface FeedbackList {
  items: FeedbackRecord[];
}

/** Clients may only submit ``rating`` and ``copy``; the rest are server-emitted. */
export interface RecordFeedbackRequest {
  message_id: string;
  action: "rating" | "copy";
  value?: FeedbackValue | null;
  reason_code?: FeedbackReasonCode | null;
  reason?: string | null;
  block_ref?: string;
  source?: "ui" | "api";
  surface?: string | null;
  metadata?: Record<string, unknown> | null;
}
