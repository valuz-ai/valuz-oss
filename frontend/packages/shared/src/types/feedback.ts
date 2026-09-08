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

/** Closed sets shared with the backend (``FEEDBACK_*_REASON_CODES``):
 *  a 👍 offers the positive chips, a 👎 the negative ones; ``other`` is in both. */
export const FEEDBACK_POSITIVE_REASON_CODES = [
  "solved",
  "followed_instructions",
  "good_quality",
  "fast",
  "helpful_autonomy",
  "other",
] as const;

export const FEEDBACK_NEGATIVE_REASON_CODES = [
  "inaccurate",
  "incomplete",
  "ignored_instructions",
  "off_topic",
  "too_slow",
  "format",
  "unsafe",
  "other",
] as const;

export type FeedbackReasonCode =
  | (typeof FEEDBACK_POSITIVE_REASON_CODES)[number]
  | (typeof FEEDBACK_NEGATIVE_REASON_CODES)[number];

/** Every known chip, positive first, de-duplicated. */
export const FEEDBACK_REASON_CODES: readonly FeedbackReasonCode[] = [
  ...FEEDBACK_POSITIVE_REASON_CODES,
  ...FEEDBACK_NEGATIVE_REASON_CODES.filter((code) => code !== "other"),
];

/** What the "提交反馈" dialog adds to a rating after the thumb itself landed. */
export interface TurnFeedbackDetails {
  reasonCodes?: FeedbackReasonCode[];
  reason?: string;
}

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
  /** Every selected chip (multi-select); ``reason_code`` is its first entry. */
  reason_codes?: FeedbackReasonCode[] | null;
  reason?: string | null;
  block_ref?: string;
  source?: "ui" | "api";
  surface?: string | null;
  metadata?: Record<string, unknown> | null;
}
