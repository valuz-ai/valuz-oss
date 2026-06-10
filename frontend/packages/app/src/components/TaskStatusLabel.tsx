import type { CSSProperties } from "react";
import { useTranslation } from "@valuz/core";

/**
 * Inline task-status label shown under the title on the project task list
 * card and the task detail header.
 *
 * When a task is ``active`` it reads "Running" with a sliding-highlight
 * sweep — reusing the global ``shimmer-text`` keyframe (see
 * ``@valuz/ui`` project.css) that ConversationTurnList rides for
 * in-progress turns, so a live task's status visibly signals "still
 * running". Every other status falls back to its plain localized label.
 */

// Per-status i18n keys (mirrors the maps the pages used inline before).
const TASK_STATUS_KEY: Record<string, string> = {
  draft: "task.statusDraft",
  active: "task.statusActive",
  paused: "task.statusPaused",
  stopped: "task.statusStopped",
  completed: "task.statusCompleted",
  failed: "task.statusFailed",
  blocked: "task.statusBlocked",
};

// Brand-purple base (#725cf9) with a lighter band the animation slides
// across. The gradient is clipped to the glyphs, so the word stays purple
// while a highlight glints over it — matches the page's active-state color.
const RUNNING_SHIMMER: CSSProperties = {
  backgroundImage:
    "linear-gradient(90deg, #725cf9 0%, #725cf9 35%, #c9beff 50%, #725cf9 65%, #725cf9 100%)",
  backgroundSize: "200% 100%",
  backgroundClip: "text",
  WebkitBackgroundClip: "text",
  color: "transparent",
  WebkitTextFillColor: "transparent",
};

export function TaskStatusLabel({ status }: { status: string }) {
  const { t } = useTranslation();

  if (status === "active") {
    return (
      <span
        className="animate-[shimmer-text_2s_linear_infinite] font-medium"
        style={RUNNING_SHIMMER}
      >
        {t("task.statusRunningLabel" as Parameters<typeof t>[0])}
      </span>
    );
  }

  const key = TASK_STATUS_KEY[status];
  return <>{key ? t(key as Parameters<typeof t>[0]) : status}</>;
}
