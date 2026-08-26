import type { SessionEventDTO } from "../api/sessions-api";
import type { BackgroundTaskState, BackgroundTaskStatus } from "@valuz/shared";

/**
 * Background-task lifecycle frames (``run_in_background`` Bash & friends).
 * The kernel maps the CLI's task pushes 1:1 and PERSISTS them, so unlike
 * ``session.workflow_progress`` (live-only) these arrive both over the live
 * stream and on history replay — deriving from the same ``events`` array that
 * feeds ``buildTurns`` covers both without extra page state to reconcile.
 */
export const SESSION_BG_TASK_STARTED_EVENT = "session.bg_task.started";
export const SESSION_BG_TASK_UPDATED_EVENT = "session.bg_task.updated";
export const SESSION_BG_TASK_FINISHED_EVENT = "session.bg_task.finished";

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

const asStatus = (raw: unknown): BackgroundTaskStatus | null =>
  typeof raw === "string" && TERMINAL_STATUSES.has(raw)
    ? (raw as BackgroundTaskStatus)
    : raw === "running"
      ? "running"
      : null;

/** Values in the legacy SSE payload are strings; nested objects arrive
 *  JSON-stringified (``patch``). */
const safeJson = (raw: string | undefined): Record<string, unknown> | null => {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
};

/**
 * Fold the session's event list into the latest per-task state, ordered by
 * first appearance. Pure — recompute on every events change (the array is
 * append-only per session and swapped on session switch).
 */
export function deriveBackgroundTasks(
  events: SessionEventDTO[],
): BackgroundTaskState[] {
  const tasks = new Map<string, BackgroundTaskState>();
  for (const item of events) {
    const type = item.event.event_type;
    if (!type.startsWith("session.bg_task.")) continue;
    const payload = item.event.payload ?? {};
    const taskId = payload.task_id;
    if (!taskId) continue;

    if (type === SESSION_BG_TASK_STARTED_EVENT) {
      tasks.set(taskId, {
        taskId,
        toolUseId: payload.tool_use_id || undefined,
        description: payload.description ?? "",
        status: "running",
        startedAtMs: item.timestamp,
      });
      continue;
    }

    const prev: BackgroundTaskState = tasks.get(taskId) ?? {
      // updated/finished without a loaded started event (e.g. the start
      // scrolled out of the fetched window) — synthesize a minimal entry so
      // terminal state still lands.
      taskId,
      description: payload.description ?? "",
      status: "running",
    };

    if (type === SESSION_BG_TASK_UPDATED_EVENT) {
      const patch = safeJson(payload.patch);
      const status = asStatus(patch?.status);
      tasks.set(taskId, { ...prev, status: status ?? prev.status });
    } else if (type === SESSION_BG_TASK_FINISHED_EVENT) {
      tasks.set(taskId, {
        ...prev,
        status: asStatus(payload.status) ?? "completed",
        summary: payload.summary || undefined,
        outputFile: payload.output_file || undefined,
      });
    }
  }
  return [...tasks.values()];
}

/**
 * The subset that should keep a "running" affordance visible in the UI.
 *
 * Use this to render the task LIST (descriptions, per-task state). To ask
 * the yes/no question "does this session have background work in flight?",
 * read the server's `background` flag instead — it ships on both
 * `SessionDetail` and `RunSummary`, both sourced from
 * `bg_busy_session_ids()`, so every surface agrees.
 *
 * The two can legitimately differ: this folds PERSISTED events, so an
 * orphaned `bg_task.started` (kernel killed mid-task, no `finished` ever
 * written) reads as running indefinitely, while the server's live registry
 * is cleared by that same restart.
 */
export function runningBackgroundTasks(
  tasks: BackgroundTaskState[],
): BackgroundTaskState[] {
  return tasks.filter((task) => task.status === "running");
}

/**
 * True while a background task has FINISHED but the CLI's spontaneous
 * wake-up turn (the agent reading the result and replying) has not landed
 * yet — i.e. the newest ``bg_task.finished`` is newer than the newest
 * ``session.idle``. The wake-up turn always ends with its own
 * ``session.idle``, which flips this back to false.
 *
 * Used to keep the idle-time event watcher alive past the moment the
 * running strip clears, so the wake-up reply appears live instead of only
 * after a page reload. Callers should still cap the wait: a synthetic
 * ``stopped`` terminal (runtime closed) has no wake-up turn behind it.
 */
export function awaitingBackgroundWakeup(events: SessionEventDTO[]): boolean {
  let lastFinishedSeq = 0;
  let lastIdleSeq = 0;
  for (const item of events) {
    const type = item.event.event_type;
    if (type === SESSION_BG_TASK_FINISHED_EVENT) {
      lastFinishedSeq = Math.max(lastFinishedSeq, item.seq);
    } else if (type === "session.idle") {
      lastIdleSeq = Math.max(lastIdleSeq, item.seq);
    }
  }
  return lastFinishedSeq > lastIdleSeq;
}
