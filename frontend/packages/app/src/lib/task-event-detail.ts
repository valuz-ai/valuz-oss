/** Shared task-timeline event → one-line detail rendering.
 *
 * Used by the task-detail timeline AND the activity overview so a task run's
 * description matches what the timeline shows for the same event. Accepts any
 * ``{type, payload}`` (a full ``TaskEvent`` or the activity ``RunSummary``'s
 * ``last_event``). */

export type Translator = (
  key: string,
  params?: Record<string, string | number>,
) => string;

export interface TaskEventLike {
  type: string;
  payload: Record<string, unknown>;
}

/** A one-line, type-specific detail string for a task timeline event. Reads the
 *  right payload field per type so each row carries useful info instead of a
 *  generic label — e.g. ``task_planned`` surfaces "拆解为 N 个子任务",
 *  ``subtask_reviewed`` surfaces the approve/rework decision + feedback. Falls
 *  back to the legacy ``text|summary|goal|error`` lookup otherwise. */
export function eventDetail(evt: TaskEventLike, t: Translator): string {
  const p = (evt.payload ?? {}) as Record<string, unknown>;
  switch (evt.type) {
    case "task_planned": {
      const subs = (p as { subtasks?: unknown[] }).subtasks;
      const n = Array.isArray(subs) ? subs.length : 0;
      if (n > 0) return t("task.event.planSummary", { count: n });
      break;
    }
    case "plan_revised": {
      const add = Array.isArray((p as { add?: unknown[] }).add)
        ? (p as { add: unknown[] }).add.length
        : 0;
      const upd = Array.isArray((p as { update?: unknown[] }).update)
        ? (p as { update: unknown[] }).update.length
        : 0;
      const rem = Array.isArray((p as { remove?: unknown[] }).remove)
        ? (p as { remove: unknown[] }).remove.length
        : 0;
      const parts: string[] = [];
      if (add) parts.push(t("task.event.planAdd", { count: add }));
      if (upd) parts.push(t("task.event.planUpdate", { count: upd }));
      if (rem) parts.push(t("task.event.planRemove", { count: rem }));
      if (parts.length > 0) return parts.join(" · ");
      break;
    }
    case "subtask_reviewed": {
      const decision = String((p as { decision?: unknown }).decision || "");
      const feedback =
        typeof (p as { feedback?: unknown }).feedback === "string"
          ? ((p as { feedback: string }).feedback || "").trim()
          : "";
      if (decision === "approve") {
        return feedback
          ? t("task.event.subtaskReviewApproveReason", { feedback })
          : t("task.event.subtaskReviewApprove");
      }
      if (decision === "rework") {
        return feedback
          ? t("task.event.subtaskReviewRework", { feedback })
          : t("task.event.subtaskReviewReworkNoFeedback");
      }
      break;
    }
  }
  for (const key of ["text", "summary", "goal", "error"]) {
    const v = p[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  return "";
}
