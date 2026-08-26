/**
 * Pure display helpers for notifications (docs/design/notifications.md).
 *
 * The backend stores DATA snapshots (agent_slug for a question, task_title for
 * a failure); the FRONTEND composes the localized display line per kind — so
 * localization lives here, on the surface that renders, and the OS notification
 * / drawer / toast all read one consistent title. Kept React-free for testing.
 */

import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import type { NotificationEntry } from "@valuz/core";

export interface NotificationDisplay {
  title: string;
  body: string;
  route: string;
  /** Collapses OS-level repeats for the same subject. */
  tag: string;
}

/** Alert/list surfaces hard-cap the body — a raw provider error dump can be
 * several KB and blows the toast / OS notification / history row across the
 * screen. The notification is a pointer; the full text lives in the session
 * ("查看详情" there). */
const BODY_MAX = 300;

const clampBody = (text: string): string =>
  text.length <= BODY_MAX ? text : `${text.slice(0, BODY_MAX - 1)}…`;

export function notificationDisplay(entry: NotificationEntry): NotificationDisplay {
  const route =
    entry.route ??
    (entry.task_id ? `/tasks/${entry.task_id}` : `/conversation/${entry.session_id ?? ""}`);

  if (entry.kind === "question") {
    return {
      title: _t("notification.notifQuestionTitle" as I18nKey).replace(
        "{agent}",
        entry.title,
      ),
      body: clampBody(entry.body),
      route,
      tag: `question:${entry.pending_id ?? entry.id}`,
    };
  }
  if (entry.kind === "task_failed") {
    return {
      title: _t("notification.notifFailureTitle" as I18nKey),
      body: clampBody(
        entry.body ||
          _t("notification.notifFailureBody" as I18nKey).replace(
            "{task}",
            entry.title,
          ),
      ),
      route,
      // Per-task tag so repeat failures of one task collapse in the OS.
      tag: `failure:${entry.task_id ?? entry.id}`,
    };
  }
  if (entry.kind === "task_completed") {
    return {
      title: _t("notification.notifCompletedTitle" as I18nKey).replace(
        "{task}",
        entry.title,
      ),
      body: clampBody(entry.body),
      route,
      // Per-task tag: a re-fire for one task replaces its toast.
      tag: `completed:${entry.task_id ?? entry.id}`,
    };
  }
  if (entry.kind === "run_failed") {
    return {
      title: _t("notification.notifRunFailedTitle" as I18nKey).replace(
        "{agent}",
        entry.title || "",
      ),
      body: clampBody(entry.body),
      route,
      // Per-session tag so repeat failures of one conversation collapse in the OS.
      tag: `run_failed:${entry.session_id ?? entry.id}`,
    };
  }
  if (entry.kind === "backup_failed") {
    return {
      // Backend sends an empty title for this kind — the localized label is
      // composed entirely here (the body carries the raw error).
      title: _t("notification.notifBackupFailedTitle" as I18nKey),
      body: clampBody(entry.body),
      route,
      // One tag for all backup failures — repeats collapse in the OS.
      tag: "backup_failed",
    };
  }
  // Unknown kind — render whatever the backend snapshotted (clamped).
  return {
    title: entry.title,
    body: clampBody(entry.body),
    route,
    tag: `notif:${entry.id}`,
  };
}
