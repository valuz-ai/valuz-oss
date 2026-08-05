/**
 * The drawer's History tab (docs/design/notifications.md) — resolved
 * (dismissed / handled) notifications from ``GET /v1/notifications/history``.
 * Fetched on mount (the tab mounts fresh on each switch, so a just-dismissed
 * entry shows up immediately) and paged by the last entry's ``created_at``
 * cursor. Read-only rows — the actionable cards stay on the open tab.
 */

import { useCallback, useEffect, useState, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  MessageCircleQuestion,
} from "lucide-react";

import {
  notificationsApi,
  useTranslation,
  type NotificationEntry,
} from "@valuz/core";
import { Button } from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

import { formatCreatedAt } from "../format-created-at";
import { notificationDisplay } from "./notification-display";

const PAGE_SIZE = 50;

const KIND_LABEL: Record<string, string> = {
  question: "notification.kindQuestion",
  task_failed: "notification.kindFailure",
  run_failed: "notification.kindRunFailed",
  backup_failed: "notification.kindBackupFailed",
  task_completed: "notification.kindCompleted",
};

function kindIcon(kind: string): ReactElement {
  if (kind === "question") return <MessageCircleQuestion className="h-3 w-3" />;
  if (kind.endsWith("_failed")) {
    return <AlertTriangle className="h-3 w-3 text-error-text" />;
  }
  if (kind.endsWith("_completed")) {
    return <CheckCircle2 className="h-3 w-3 text-success-text" />;
  }
  return <Bell className="h-3 w-3" />;
}

/** In-app target for a history row — the same fallback chain the open cards
 *  navigate by, but ``null`` (row not clickable) when there is nowhere to go. */
function rowTarget(entry: NotificationEntry): string | null {
  if (entry.route) return entry.route;
  if (entry.task_id) return `/tasks/${encodeURIComponent(entry.task_id)}`;
  if (entry.session_id) {
    return `/conversation/${encodeURIComponent(entry.session_id)}`;
  }
  return null;
}

function HistoryRow({
  entry,
  onOpen,
}: {
  entry: NotificationEntry;
  onOpen?: (route: string) => void;
}): ReactElement {
  const { t } = useTranslation();
  const display = notificationDisplay(entry);
  const labelKey = KIND_LABEL[entry.kind];
  const target = rowTarget(entry);

  const body = (
    <>
      <div className="flex items-center gap-1.5 text-2xs font-medium text-ink-muted">
        {kindIcon(entry.kind)}
        {labelKey ? t(labelKey as I18nKey) : entry.kind}
        <span className="ml-auto shrink-0">
          {formatCreatedAt(entry.created_at, t)}
        </span>
      </div>
      <p className="mt-1 truncate text-sm font-medium text-ink-heading">
        {display.title}
      </p>
      {display.body && (
        <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-ink-muted">
          {display.body}
        </p>
      )}
    </>
  );

  if (target == null) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface px-3 py-2.5">
        {body}
      </div>
    );
  }
  // Whole row opens the source (task / conversation / settings), same as
  // clicking through from the open tab's cards.
  return (
    <button
      type="button"
      onClick={() => onOpen?.(target)}
      className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2.5 text-left transition-colors hover:bg-surface-soft"
    >
      {body}
    </button>
  );
}

export function NotificationHistoryList({
  onNavigateAway,
}: {
  onNavigateAway?: () => void;
}): ReactElement {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [entries, setEntries] = useState<NotificationEntry[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (before?: number) => {
    setLoading(true);
    setFailed(false);
    try {
      const res = await notificationsApi.fetchHistory({
        limit: PAGE_SIZE,
        before,
      });
      setEntries((prev) =>
        before == null ? res.entries : [...prev, ...res.entries],
      );
      setHasMore(Boolean(res.has_more));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (entries.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
        {loading ? (
          <p className="text-sm text-ink-muted">{t("common.loading")}</p>
        ) : failed ? (
          <>
            <p className="text-sm text-ink-muted">
              {t("notification.historyLoadFailed" as I18nKey)}
            </p>
            <Button
              size="sm"
              variant="outline"
              className="text-xs"
              onClick={() => void load()}
            >
              {t("common.retry")}
            </Button>
          </>
        ) : (
          <>
            <span className="text-3xl opacity-40">🕘</span>
            <p className="text-sm font-medium text-ink-body">
              {t("notification.historyEmpty" as I18nKey)}
            </p>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-2 overflow-y-auto px-4 py-4">
      {entries.map((entry) => (
        <HistoryRow
          key={entry.id}
          entry={entry}
          onOpen={(route) => {
            onNavigateAway?.();
            navigate(route);
          }}
        />
      ))}
      {hasMore && (
        <div className="flex justify-center pt-1">
          <Button
            size="sm"
            variant="ghost"
            className="text-xs text-ink-muted"
            onClick={() => void load(entries[entries.length - 1]?.created_at)}
            disabled={loading}
            loading={loading}
          >
            {t("notification.loadMore" as I18nKey)}
          </Button>
        </div>
      )}
    </div>
  );
}
