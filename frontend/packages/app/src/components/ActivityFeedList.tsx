/**
 * Shared renderer for the unified activity feed (``GET /v1/activity``) — used by
 * the project-home tabs (scoped) and the global 动态 history list. Chats + task
 * entities interleaved by time bucket, with a keyset "load more". The caller
 * owns the feed (``useActivityFeed``); this component only renders it.
 */
import { useEffect, useRef, useState } from "react";

import { useTranslation } from "@valuz/core";
import type { ActivityFeed, ActivityItem } from "@valuz/core";
import { Badge } from "@valuz/ui";
import { Clock3, ListChecks, Loader2, MessageSquare } from "lucide-react";

import { BUCKET_KEY, groupByTimeBucket } from "../lib/time-buckets";
import { RenameInput } from "./RenameInput";
import { RowActionsMenu } from "./RowActionsMenu";
import { formatCreatedAt } from "./format-created-at";
import { OriginBadge } from "./ExecutionLocationPicker";

// Session status -> i18n key for the right-edge Badge on chat rows. The feed
// carries the RAW kernel status, so an abnormally-ended chat (a user interrupt
// or a runtime error) arrives as ``terminated`` — not ``failed``/``cancelled``,
// which the kernel never persists. Without a ``terminated`` entry those rows
// showed no status badge at all; map it to the neutral 已停止 (it covers both a
// user-cancelled and an errored end, and reads muted rather than error-red).
const SESSION_STATUS_KEY: Record<string, string> = {
  running: "activity.statusRunning",
  idle: "activity.statusIdle",
  terminated: "activity.statusStopped",
  failed: "activity.statusFailed",
  cancelled: "activity.statusStopped",
  archived: "activity.statusStopped",
};

// Task status -> i18n key for task rows. ``active`` reuses the session
// "running" key so a task and a conversation both read 运行中 / Running in the
// same feed — ``task.statusActive`` (进行中 / Active) is kept for the task detail
// header, but mixing it into this list read inconsistently against chat rows.
const TASK_STATUS_KEY: Record<string, string> = {
  draft: "task.statusDraft",
  active: "activity.statusRunning",
  paused: "task.statusPaused",
  stopped: "task.statusStopped",
  completed: "task.statusCompleted",
  failed: "task.statusFailed",
  blocked: "task.statusBlocked",
};

const activityStatusVariant = (
  status: string,
): "brand" | "success" | "warning" | "error" | "outline" => {
  if (status === "running" || status === "active" || status === "draft")
    return "brand";
  if (status === "completed" || status === "idle") return "success";
  if (status === "failed") return "error";
  if (status === "blocked" || status === "paused") return "warning";
  return "outline";
};

export interface ActivityFeedListProps {
  feed: ActivityFeed;
  onOpenSession: (id: string) => void;
  onOpenTask: (id: string) => void;
  onRenameConfirm: (id: string, value: string) => void;
  onDeleteSession: (id: string, title: string) => void;
  /** Whole-session fork (docs/design/session-fork.md). Rendered on chat
   * rows that are not running; origin gating (automation/task chats) is
   * server-side — a 422 surfaces as the caller's failure toast. */
  onForkSession?: (id: string) => void;
  /** Hide the leading 对话/任务/自动化 chip (the 自动化 tab is already scoped). */
  hideScopeTag?: boolean;
  /** Append the project name after the title — the global 动态 list wants it. */
  showProjectName?: boolean;
  emptyLabel: string;
}

export const ActivityFeedList = ({
  feed,
  onOpenSession,
  onOpenTask,
  onRenameConfirm,
  onDeleteSession,
  onForkSession,
  hideScopeTag,
  showProjectName,
  emptyLabel,
}: ActivityFeedListProps) => {
  const { t } = useTranslation();
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const { items, loading, loadingMore, hasMore, loadMore } = feed;

  // Infinite scroll: auto-load the next page when the bottom sentinel scrolls
  // into view (pre-fetched via ``rootMargin``). ``loadMore`` no-ops while a page
  // is already in flight, so repeated hits are safe; re-observing on
  // ``items.length`` re-fires if the sentinel is still visible after a page
  // lands (so short content keeps filling until exhausted).
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadMore();
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loadMore, items.length]);

  if (loading && items.length === 0) {
    return (
      <div className="px-3 py-12 text-center text-sm text-ink-meta">
        {t("common.loading" as Parameters<typeof t>[0])}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="px-3 py-12 text-center text-sm text-ink-meta">
        {emptyLabel}
      </div>
    );
  }

  const grouped = groupByTimeBucket(items, (item) => item.sort_at);

  const renderItem = (item: ActivityItem) => {
    const Icon = item.is_automation
      ? Clock3
      : item.kind === "task"
        ? ListChecks
        : MessageSquare;
    const statusKey =
      item.kind === "task"
        ? TASK_STATUS_KEY[item.status]
        : SESSION_STATUS_KEY[item.status];
    const kindLabel = item.is_automation
      ? t("activity.automationTag" as Parameters<typeof t>[0])
      : item.kind === "task"
        ? t("project.tasksColumn" as Parameters<typeof t>[0])
        : t("project.chatTab" as Parameters<typeof t>[0]);
    // Leading scope chip. Global (动态) view prefixes the project name —
    // ``项目名 · 分类`` (project first, then category), matching the old list;
    // the project-scoped tabs show just the category.
    const scopeText =
      showProjectName && item.project_name
        ? `${item.project_name} · ${kindLabel}`
        : kindLabel;
    if (item.kind === "chat" && renamingId === item.id) {
      return (
        <li key={`${item.kind}-${item.id}`}>
          <div className="flex w-full items-center gap-2 rounded-xl px-3 py-3">
            <RenameInput
              initial={item.title}
              onConfirm={(v) => {
                onRenameConfirm(item.id, v);
                setRenamingId(null);
              }}
              onCancel={() => setRenamingId(null)}
            />
          </div>
        </li>
      );
    }
    return (
      <li key={`${item.kind}-${item.id}`} className="group relative">
        <div
          role="button"
          tabIndex={0}
          onClick={() =>
            item.kind === "task" ? onOpenTask(item.id) : onOpenSession(item.id)
          }
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (item.kind === "task") onOpenTask(item.id);
              else onOpenSession(item.id);
            }
          }}
          className="flex w-full cursor-default items-center gap-2 rounded-xl px-3 py-3 text-left outline-none transition-colors hover:bg-surface-soft focus-visible:bg-surface-soft"
        >
          {!hideScopeTag && (
            <span className="inline-flex max-w-[45%] shrink-0 items-center gap-1 text-[11px] text-ink-muted">
              <Icon className="h-3 w-3 shrink-0" strokeWidth={2} />
              <span className="truncate">{scopeText}</span>
            </span>
          )}
          {/* Execution origin (multi-target editions; fan-out tags rows). */}
          <OriginBadge origin={item.exec_origin} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
            {item.title}
          </span>
          <span className="shrink-0 whitespace-nowrap text-[11px] text-ink-meta">
            {formatCreatedAt(item.sort_at, t)}
          </span>
          <span className="relative inline-flex min-w-6 shrink-0 items-center justify-center">
            {statusKey && (
              <Badge
                variant={activityStatusVariant(item.status)}
                className={
                  item.kind === "chat"
                    ? "transition-opacity group-hover:opacity-0 group-has-[[data-state=open]]:opacity-0"
                    : undefined
                }
              >
                {t(statusKey as Parameters<typeof t>[0])}
              </Badge>
            )}
            {item.kind === "chat" && (
              <RowActionsMenu
                onRename={() => setRenamingId(item.id)}
                onDelete={() => onDeleteSession(item.id, item.title)}
                onFork={
                  onForkSession && item.status !== "running"
                    ? () => onForkSession(item.id)
                    : undefined
                }
              />
            )}
          </span>
        </div>
      </li>
    );
  };

  return (
    <div className="flex flex-col gap-5">
      {grouped.map(([bucket, bucketItems]) => (
        <div key={bucket}>
          <div className="mb-1.5 px-3 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
            {t(BUCKET_KEY[bucket] as Parameters<typeof t>[0])}
          </div>
          <ul className="flex flex-col">{bucketItems.map(renderItem)}</ul>
        </div>
      ))}
      {hasMore && (
        <div
          ref={sentinelRef}
          className="flex items-center justify-center py-4 text-xs text-ink-meta"
        >
          {loadingMore && (
            <span className="inline-flex items-center gap-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t("common.loading" as Parameters<typeof t>[0])}
            </span>
          )}
        </div>
      )}
    </div>
  );
};
