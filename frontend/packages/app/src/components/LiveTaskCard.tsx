/**
 * LiveTaskCard — single live, SSE-driven task card embedded in the
 * conversation flow (VALUZ-CHATPLAN follow-up).
 *
 * One card per task_id, mounted at the message where the task was first
 * referenced (draft_task / plan_task / create_task). Subscribes to
 * ``/v1/tasks/{taskId}/events/stream`` via ``useTaskEvents`` so title /
 * status / subtask states update in real time without polling. Older
 * plan-write events still render as pills above/below — this card always
 * reflects the *current* state.
 *
 * Compared to ``PlanCard`` (which is an immutable per-version snapshot
 * intended for ``PlanCardFeed``'s versioned history), ``LiveTaskCard``
 * mutates in place. Only one instance per task is rendered.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";
import {
  tasksApi,
  useTaskEvents,
  useTranslation,
  type PlanSubtask,
  type TaskEvent,
} from "@valuz/core";

// Backend ``TaskPlan.to_panel()`` (plan.py:_PANEL_MAP) collapses the
// 6 internal subtask statuses into a 4-state UI vocabulary —
// ``pending / active / completed / failed``. Map glyphs + tone for both
// the panel states (what we actually receive) AND the internal ones, in
// case a future runtime ships internal names through a different path.
const STATUS_GLYPH: Record<string, string> = {
  pending: "☐",
  planned: "☐",
  active: "▶",
  in_progress: "▶",
  in_review: "▶",
  rework: "▶",
  paused: "⏸",
  completed: "✓",
  done: "✓",
  failed: "✗",
};

const STATUS_TONE: Record<string, string> = {
  pending: "text-ink-muted",
  planned: "text-ink-muted",
  active: "text-brand",
  in_progress: "text-brand",
  in_review: "text-brand",
  rework: "text-brand",
  paused: "text-amber-600",
  completed: "text-emerald-600",
  done: "text-emerald-600",
  failed: "text-rose-600",
};

const STATUS_DONE = new Set(["completed", "done"]);
const STATUS_RUNNING = new Set([
  "active",
  "in_progress",
  "in_review",
  "rework",
]);

interface Meta {
  title: string;
  status: string;
  planVersion: number;
}

export interface LiveTaskCardProps {
  taskId: string;
  /** Caller session id — threaded into commit/abandon as
   *  ``caller_session_id``. Usually the current chat session. */
  callerSessionId: string;
  onNavigate?: (path: string) => void;
}

export function LiveTaskCard(props: LiveTaskCardProps): ReactElement | null {
  const { taskId, callerSessionId, onNavigate } = props;
  const { t } = useTranslation();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [subtasks, setSubtasks] = useState<PlanSubtask[]>([]);
  const [busy, setBusy] = useState<"commit" | "abandon" | null>(null);
  const refetchTimerRef = useRef<number | null>(null);

  // Initial fetch — getTask for title/status, getPlan for subtasks. The
  // plan endpoint 404s for tasks that haven't been planned yet (plan_version=0
  // and no subtasks); swallow that case and render the empty shell.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const detail = await tasksApi.getTask(taskId);
        if (cancelled) return;
        let planVersion = 0;
        try {
          const plan = await tasksApi.getPlan(taskId);
          if (cancelled) return;
          setSubtasks(plan.subtasks ?? []);
          planVersion = plan.current_version ?? 0;
        } catch {
          /* no plan yet — leave subtasks empty */
        }
        setMeta({
          title: detail.task.title,
          status: detail.task.status,
          planVersion,
        });
      } catch {
        /* task not yet visible — SSE will deliver task_drafted */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  // Debounced plan refetch — used when subtask_* events fire and we'd
  // rather pull the canonical snapshot than reconstruct from payload.
  const scheduleRefetchPlan = useCallback(() => {
    if (refetchTimerRef.current != null) return;
    refetchTimerRef.current = window.setTimeout(() => {
      refetchTimerRef.current = null;
      void (async () => {
        try {
          const plan = await tasksApi.getPlan(taskId);
          setSubtasks(plan.subtasks ?? []);
          setMeta((m) =>
            m
              ? { ...m, planVersion: plan.current_version ?? m.planVersion }
              : m,
          );
        } catch {
          /* ignore */
        }
      })();
    }, 250);
  }, [taskId]);

  useEffect(
    () => () => {
      if (refetchTimerRef.current != null) {
        window.clearTimeout(refetchTimerRef.current);
      }
    },
    [],
  );

  const handleEvent = useCallback(
    (ev: TaskEvent) => {
      const payload = (ev.payload ?? {}) as {
        plan_version?: number;
        subtasks?: PlanSubtask[];
        title?: string;
        status?: string;
        goal?: string;
      };
      switch (ev.type) {
        case "task_drafted":
          if (payload.title) {
            setMeta((m) =>
              m
                ? {
                    ...m,
                    title: payload.title!,
                    status: payload.status ?? m.status,
                  }
                : {
                    title: payload.title!,
                    status: payload.status ?? "draft",
                    planVersion: payload.plan_version ?? 0,
                  },
            );
          }
          break;
        case "task_planned":
        case "task_plan_update":
        case "plan_revised": {
          const v = payload.plan_version ?? 0;
          if (Array.isArray(payload.subtasks)) setSubtasks(payload.subtasks);
          setMeta((m) =>
            m
              ? {
                  ...m,
                  planVersion: v || m.planVersion,
                  title: payload.title ?? m.title,
                  status: payload.status ?? m.status,
                }
              : null,
          );
          break;
        }
        case "subtask_spawned":
        case "subtask_completed":
        case "subtask_failed":
        case "subtask_reviewed":
        case "subtask_message":
          scheduleRefetchPlan();
          break;
        case "committed":
          setMeta((m) => (m ? { ...m, status: "active" } : m));
          break;
        case "abandoned":
          setMeta((m) => (m ? { ...m, status: "abandoned" } : m));
          break;
        case "task_completed":
          setMeta((m) => (m ? { ...m, status: "completed" } : m));
          break;
        case "task_stopped":
          setMeta((m) => (m ? { ...m, status: "stopped" } : m));
          break;
        case "task_blocked":
          setMeta((m) => (m ? { ...m, status: "blocked" } : m));
          break;
        case "stopped":
          setMeta((m) => (m ? { ...m, status: "paused" } : m));
          break;
        case "resumed":
          setMeta((m) => (m ? { ...m, status: "active" } : m));
          break;
        default:
          break;
      }
    },
    [scheduleRefetchPlan],
  );

  useTaskEvents(taskId, handleEvent);

  const counts = useMemo(() => {
    let done = 0;
    let failed = 0;
    let inProgress = 0;
    for (const s of subtasks) {
      if (STATUS_DONE.has(s.status)) done++;
      else if (s.status === "failed") failed++;
      else if (STATUS_RUNNING.has(s.status)) inProgress++;
    }
    return { done, failed, inProgress, total: subtasks.length };
  }, [subtasks]);

  const handleExecute = useCallback(async () => {
    if (busy) return;
    setBusy("commit");
    try {
      await tasksApi.commit(taskId, { caller_session_id: callerSessionId });
    } catch (err) {
      console.warn("commit_task from LiveTaskCard failed", err);
    } finally {
      setBusy(null);
    }
  }, [taskId, callerSessionId, busy]);

  const handleAbandon = useCallback(async () => {
    if (busy) return;
    setBusy("abandon");
    try {
      await tasksApi.abandon(taskId, { caller_session_id: callerSessionId });
    } catch (err) {
      console.warn("abandon_task from LiveTaskCard failed", err);
    } finally {
      setBusy(null);
    }
  }, [taskId, callerSessionId, busy]);

  const handleOpenDetail = useCallback(() => {
    onNavigate?.(`/tasks/${encodeURIComponent(taskId)}`);
  }, [taskId, onNavigate]);

  if (!meta) {
    return (
      <div className="rounded-lg border border-dashed border-surface-border bg-surface-soft px-4 py-3 text-xs text-ink-muted">
        {t("conversation.taskLoading" as Parameters<typeof t>[0])}
      </div>
    );
  }

  const status = meta.status;
  const isDraft = status === "draft";
  const isActive = status === "active" || status === "paused";
  const isTerminal =
    status === "completed" ||
    status === "stopped" ||
    status === "abandoned" ||
    status === "blocked" ||
    status === "failed";

  const statusToneClass =
    status === "active"
      ? "bg-brand/10 text-brand ring-1 ring-brand/20"
      : status === "completed"
        ? "bg-emerald-500/10 text-emerald-600 ring-1 ring-emerald-500/20"
        : status === "failed" || status === "blocked"
          ? "bg-rose-500/10 text-rose-600 ring-1 ring-rose-500/20"
          : status === "abandoned" || status === "stopped"
            ? "bg-surface-soft text-ink-muted ring-1 ring-surface-border"
            : "bg-surface-soft text-ink-body ring-1 ring-surface-border";

  const progressPct =
    counts.total > 0 ? Math.round((counts.done / counts.total) * 100) : 0;

  return (
    <div
      className="overflow-hidden rounded-xl border border-surface-border bg-surface text-sm shadow-sm"
      data-testid="live-task-card"
    >
      <div className="flex items-center gap-3 border-b border-surface-border bg-gradient-to-r from-brand/5 via-surface to-surface px-4 py-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand/10 text-base">
          📋
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-semibold text-ink-heading">
              {meta.title}
            </span>
            <span
              className={`shrink-0 rounded-md px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide ${statusToneClass}`}
            >
              {status}
            </span>
            {meta.planVersion > 0 && (
              <span className="shrink-0 rounded bg-surface-soft px-1.5 py-0.5 text-2xs text-ink-muted">
                v{meta.planVersion}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            {counts.total > 0 ? (
              <>
                <span>
                  {t("conversation.taskProgress" as Parameters<typeof t>[0])
                    .replace("{done}", String(counts.done))
                    .replace("{total}", String(counts.total))}
                </span>
                {counts.inProgress > 0 && (
                  <span className="text-brand">
                    ·{" "}
                    {t(
                      "conversation.taskInProgress" as Parameters<typeof t>[0],
                      undefined,
                      { count: counts.inProgress },
                    )}
                  </span>
                )}
                {counts.failed > 0 && (
                  <span className="text-rose-600">
                    ·{" "}
                    {t(
                      "conversation.taskFailed" as Parameters<typeof t>[0],
                      undefined,
                      { count: counts.failed },
                    )}
                  </span>
                )}
                <span className="ml-auto font-mono text-ink-muted">
                  {progressPct}%
                </span>
              </>
            ) : (
              <span>
                {t("conversation.taskNoPlan" as Parameters<typeof t>[0])}
              </span>
            )}
          </div>
          {counts.total > 0 && (
            <div className="h-1 overflow-hidden rounded-full bg-surface-soft">
              <div
                className="h-full bg-brand transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          )}
        </div>
      </div>

      {subtasks.length > 0 && (
        <ul className="divide-y divide-surface-border">
          {subtasks.map((s) => (
            <li
              key={s.key}
              className="flex items-center gap-3 px-4 py-2 text-sm transition-colors hover:bg-surface-soft"
            >
              <span
                className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-xs ${
                  STATUS_TONE[s.status] ?? "text-ink-muted"
                }`}
                aria-label={s.status}
              >
                {STATUS_GLYPH[s.status] ?? "·"}
              </span>
              <span className="shrink-0 rounded bg-surface-soft px-1.5 py-0.5 font-mono text-2xs text-ink-muted">
                {s.key}
              </span>
              <span className="flex-1 truncate text-ink-body">{s.label}</span>
              {s.agent && (
                <span className="shrink-0 text-xs text-ink-muted">
                  @{s.agent}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {(isDraft || isActive || isTerminal) && (
        <div className="flex items-center justify-end gap-2 border-t border-surface-border bg-surface-soft/30 px-4 py-2.5">
          {isDraft && (
            <>
              <button
                type="button"
                disabled={busy !== null}
                onClick={handleAbandon}
                className="rounded-md border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-ink-body transition-colors hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "abandon"
                  ? t("common.processing" as Parameters<typeof t>[0])
                  : t("conversation.taskAbandon" as Parameters<typeof t>[0])}
              </button>
              <button
                type="button"
                disabled={busy !== null || subtasks.length === 0}
                onClick={handleExecute}
                className="rounded-md bg-brand px-3.5 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:bg-brand/90 hover:shadow disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "commit"
                  ? t("common.processing" as Parameters<typeof t>[0])
                  : t("conversation.taskExecute" as Parameters<typeof t>[0])}
              </button>
            </>
          )}
          {(isActive || isTerminal) && (
            <button
              type="button"
              onClick={handleOpenDetail}
              className="rounded-md border border-surface-border bg-surface px-3 py-1.5 text-xs text-ink-body transition-colors hover:border-brand/40 hover:bg-brand/5 hover:text-ink-heading"
            >
              {t("conversation.openTask" as Parameters<typeof t>[0])}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
