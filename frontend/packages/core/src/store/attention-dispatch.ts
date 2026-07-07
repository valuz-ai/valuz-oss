/**
 * Attention dispatch policy (question-attention) — the PRD's reminder
 * matrix as a pure function, so the rule set is unit-testable without
 * mounting the Provider.
 *
 * | user location                     | channel  |
 * |-----------------------------------|----------|
 * | window focused, watching session  | silent   | (inline card is on screen)
 * | window focused, elsewhere         | toast    |
 * | window in background              | system   | (OS notification + badge)
 *
 * "Watched" only silences while the window is FOCUSED — a conversation
 * left open behind a hidden/minimized window has nobody looking at its
 * inline card, so background always escalates to the system channel.
 *
 * Badge counts and the Activity attention group are driven directly by the
 * store and ignore this policy — it only gates the interruptive channels.
 */

import type { DecisionEntry } from "../api/decisions-api";

export type AttentionChannel = "silent" | "toast" | "system";

export function decideAttentionChannel(
  isWatched: boolean,
  hasFocus: boolean,
): AttentionChannel {
  if (!hasFocus) return "system";
  return isWatched ? "silent" : "toast";
}

/** In-app route that answers ``entry`` — task entries land on the task
 *  page (the question renders in its timeline), conversations open the
 *  session itself. */
export function attentionRoute(entry: DecisionEntry): string {
  if (entry.source_kind === "task" && entry.task_id) {
    return `/tasks/${encodeURIComponent(entry.task_id)}`;
  }
  return `/conversation/${encodeURIComponent(entry.session_id)}`;
}

/** One-line context label: task chain for task entries, session title
 *  (or the first question text) for conversations. */
export function attentionContextLabel(entry: DecisionEntry): string {
  if (entry.source_kind === "task") {
    return [entry.task_title, entry.subtask_label].filter(Boolean).join(" · ");
  }
  if (entry.session_title) return entry.session_title;
  const q = (
    entry.question_payload as { questions?: Array<{ question?: string }> }
  )?.questions?.[0]?.question;
  return q ?? "";
}
