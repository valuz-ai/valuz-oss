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
  getEntityOrigin,
  recordEntityOrigin,
} from "@valuz/core";
import { Badge } from "@valuz/ui";
import { TaskStatusLabel } from "./TaskStatusLabel";

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
  paused: "text-warning-text",
  completed: "text-success-text",
  done: "text-success-text",
  failed: "text-error-text",
};

const STATUS_DONE = new Set(["completed", "done"]);
const STATUS_RUNNING = new Set([
  "active",
  "in_progress",
  "in_review",
  "rework",
]);

// Task statuses with nothing left to stream. Finished cards accumulate in a
// long conversation, and every open SSE stream pins one of the browser's 6
// per-host HTTP/1.1 connections — enough finished cards starve every other
// request to the backend (all pages hang Pending). The snapshot fetched on
// mount is enough for these; ``stopped`` trades a possibly-stale card (if
// the task is revived from another surface) for the freed connection.
const STREAM_DONE_STATUSES = new Set([
  "completed",
  "failed",
  "stopped",
  "abandoned",
]);

interface Meta {
  title: string;
  status: string;
  planVersion: number;
}

const taskStatusVariant = (
  status: string,
): "brand" | "success" | "error" | "outline" => {
  if (status === "active") return "brand";
  if (status === "completed") return "success";
  if (status === "failed" || status === "blocked") return "error";
  return "outline";
};

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
        // Lifecycle events carry ``status``; a plan SNAPSHOT carries
        // ``task_status`` (an unqualified ``status`` there would read as the
        // plan's) — see TaskPlanUpdateEvent in api/openapi.yaml.
        status?: string;
        task_status?: string;
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
          // ``task_status`` is the contract field (an unqualified ``status``
          // in a plan snapshot would read as "the plan's"); reading ``status``
          // here was a permanent no-op, so the badge never moved on a plan
          // write. ``task_plan_update`` is a SELF-CONTAINED snapshot, so it
          // can also bootstrap the card when the initial fetch lost the race
          // (otherwise the card sat on "loading" forever).
          const nextStatus = payload.task_status ?? payload.status;
          setMeta((m) =>
            m
              ? {
                  ...m,
                  planVersion: v || m.planVersion,
                  title: payload.title ?? m.title,
                  status: nextStatus ?? m.status,
                }
              : payload.title
                ? {
                    title: payload.title,
                    status: nextStatus ?? "active",
                    planVersion: v,
                  }
                : null,
          );
          break;
        }
        // Anything that can change a subtask's state → refetch the plan.
        // `subtask_reported` is member→lead, `subtask_message` is lead→member;
        // they were one type until 2026-07, so pre-split rows arrive under
        // `subtask_message` in BOTH directions and must stay handled.
        case "subtask_spawned":
        case "subtask_completed":
        case "subtask_failed":
        case "subtask_reviewed":
        case "subtask_reported":
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
        // `stop_task` emits `paused` or `stopped` depending on the target;
        // each maps to the SAME name as the task status it just wrote. This
        // used to project `stopped` onto `paused` (and handle no `paused`
        // event at all), so a stopped task rendered as merely paused and every
        // status-derived affordance — resume/stop buttons, the attention dot —
        // was computed from a status the backend never set.
        case "paused":
          setMeta((m) => (m ? { ...m, status: "paused" } : m));
          break;
        case "stopped":
          setMeta((m) => (m ? { ...m, status: "stopped" } : m));
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

  // Seed the draft task's origin from its caller session BEFORE the event
  // subscription below resolves its stream URL (multi-target editions).
  useEffect(() => {
    const sessionOrigin = getEntityOrigin(callerSessionId, "session");
    if (sessionOrigin) recordEntityOrigin(taskId, sessionOrigin);
  }, [taskId, callerSessionId]);

  // Subscribe only while the task can still change (or before the snapshot
  // resolves — a not-yet-visible draft arrives as ``task_drafted`` over SSE).
  // Once a terminal event flips ``meta.status`` the stream closes itself.
  useTaskEvents(
    meta && STREAM_DONE_STATUSES.has(meta.status) ? null : taskId,
    handleEvent,
  );

  // On a halted task (anything but ``active``) no member is live, yet the
  // backend still projects ``in_review`` / ``rework`` nodes as the spinning
  // ``active`` panel state (it parks only ``in_progress``). Show those as
  // ``paused`` so nothing spins on a stopped task — display-only; the stored
  // node status is untouched and resume reconciles it. Mirrors
  // TaskContextPanel's displaySubtaskStatus.
  const displayStatus = useCallback(
    (status: string): string =>
      meta && meta.status !== "active" && status === "active" ? "paused" : status,
    [meta],
  );

  const counts = useMemo(() => {
    let done = 0;
    let failed = 0;
    let inProgress = 0;
    for (const s of subtasks) {
      const st = displayStatus(s.status);
      if (STATUS_DONE.has(st)) done++;
      else if (st === "failed") failed++;
      else if (STATUS_RUNNING.has(st)) inProgress++;
    }
    return { done, failed, inProgress, total: subtasks.length };
  }, [subtasks, displayStatus]);

  const handleExecute = useCallback(async () => {
    if (busy) return;
    setBusy("commit");
    try {
      // A draft task minted inside a routed session lives on that session's
      // backend — seed its origin before the first task-scoped call.
      const sessionOrigin = getEntityOrigin(callerSessionId, "session");
      if (sessionOrigin) recordEntityOrigin(taskId, sessionOrigin);
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
            <Badge
              variant={taskStatusVariant(status)}
              className="shrink-0 tracking-wide"
            >
              {/* Localized, not the raw backend enum uppercased — the detail
                  page has always used this component. */}
              <TaskStatusLabel status={status} />
            </Badge>
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
                  STATUS_TONE[displayStatus(s.status)] ?? "text-ink-muted"
                }`}
                aria-label={displayStatus(s.status)}
              >
                {STATUS_GLYPH[displayStatus(s.status)] ?? "·"}
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
                className="rounded-md bg-brand px-3.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-hover focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
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
