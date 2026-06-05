/**
 * PlanCard — inline Plan rendering in the chat conversation flow
 * (VALUZ-CHATPLAN S3 / exec-plan §3.8 / Q6).
 *
 * Every ``task_plan_update`` event appends a new PlanCard to the
 * conversation history (cards are immutable, like chat messages).
 * Only the latest card for a given task is interactive — older cards
 * render greyed-out so the user can scroll back through plan history
 * without accidentally Executing the wrong version.
 *
 * Status surface (matching ``TaskPlan.to_panel`` on the backend):
 *  - draft   → "Execute" + "Abandon" buttons
 *  - active  → "Open Task Detail" link
 *  - paused / blocked / completed / stopped / abandoned / failed
 *            → "View Final State" link (terminal cards)
 */

import type { ReactElement } from "react";
import type { PlanSubtask } from "@valuz/core";

/** One subtask cell in the card. The four glyphs map to backend
 * ``to_panel`` statuses: planned / in_progress / done / failed. */
const STATUS_GLYPH: Record<string, string> = {
  planned: "☐",
  in_progress: "▶",
  in_review: "▶",
  rework: "▶",
  done: "✓",
  failed: "✗",
};

const STATUS_TONE: Record<string, string> = {
  planned: "text-ink-muted",
  in_progress: "text-brand",
  in_review: "text-brand",
  rework: "text-brand",
  done: "text-emerald-600",
  failed: "text-rose-600",
};

export interface PlanCardProps {
  taskId: string;
  taskTitle: string;
  /** ``draft`` | ``active`` | ``paused`` | ``stopped`` | ``completed`` |
   *  ``blocked`` | ``abandoned`` — exactly the values backend
   *  ``TaskRow.status`` can hold. */
  status: string;
  planVersion: number;
  subtasks: PlanSubtask[];
  /** When false, the card renders greyed-out (older plan version for
   *  the same task) and all action buttons are disabled. The latest
   *  card is the one whose ``planVersion`` equals
   *  ``useTaskStore.latestPlanIdByTaskId[taskId]``. */
  isLatest: boolean;
  onExecute?: () => void;
  onAbandon?: () => void;
  onOpenDetail?: () => void;
}

export function PlanCard(props: PlanCardProps): ReactElement {
  const {
    taskTitle,
    status,
    planVersion,
    subtasks,
    isLatest,
    onExecute,
    onAbandon,
    onOpenDetail,
  } = props;

  const isDraft = status === "draft";
  const isActive = status === "active" || status === "paused";
  const isTerminal =
    status === "completed" ||
    status === "stopped" ||
    status === "abandoned" ||
    status === "blocked" ||
    status === "failed";

  // Older versions of the same plan render half-opacity, read-only.
  const wrapperClass = [
    "rounded-lg border border-surface-border bg-surface-soft px-4 py-3 text-sm",
    isLatest ? "" : "opacity-60",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={wrapperClass} data-testid="plan-card">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-medium text-ink-heading">
            Plan v{planVersion}
          </span>
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs uppercase tracking-wide text-ink-muted">
            {status}
          </span>
          <span className="truncate text-ink-body">{taskTitle}</span>
        </div>
        {isLatest && <span className="text-xs text-ink-muted">(latest)</span>}
      </div>

      <ul className="mb-3 space-y-1.5">
        {subtasks.map((s) => (
          <li key={s.key} className="flex items-baseline gap-2">
            <span
              className={`inline-block w-4 text-center ${
                STATUS_TONE[s.status] ?? "text-ink-muted"
              }`}
              aria-label={s.status}
            >
              {STATUS_GLYPH[s.status] ?? "·"}
            </span>
            <span className="font-mono text-xs text-ink-muted">{s.key}</span>
            <span className="flex-1 truncate text-ink-body">{s.label}</span>
            {s.agent && (
              <span className="text-xs text-ink-muted">@{s.agent}</span>
            )}
          </li>
        ))}
      </ul>

      <div className="flex justify-end gap-2">
        {isDraft && (
          <>
            <button
              type="button"
              disabled={!isLatest || !onExecute}
              onClick={onExecute}
              className="rounded bg-brand px-3 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              Execute
            </button>
            <button
              type="button"
              disabled={!isLatest || !onAbandon}
              onClick={onAbandon}
              className="rounded border border-surface-border px-3 py-1 text-xs text-ink-body disabled:cursor-not-allowed disabled:opacity-40"
            >
              Abandon
            </button>
          </>
        )}
        {isActive && (
          <button
            type="button"
            disabled={!onOpenDetail}
            onClick={onOpenDetail}
            className="rounded border border-surface-border px-3 py-1 text-xs text-ink-body disabled:cursor-not-allowed disabled:opacity-40"
          >
            Open Task Detail
          </button>
        )}
        {isTerminal && (
          <button
            type="button"
            disabled={!onOpenDetail}
            onClick={onOpenDetail}
            className="rounded border border-surface-border px-3 py-1 text-xs text-ink-muted disabled:cursor-not-allowed disabled:opacity-40"
          >
            View Final State
          </button>
        )}
      </div>
    </div>
  );
}
