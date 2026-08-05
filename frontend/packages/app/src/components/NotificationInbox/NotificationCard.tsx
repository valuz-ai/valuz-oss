/**
 * One notification card in the drawer, dispatched by kind
 * (docs/design/notifications.md):
 * - ``question``    → the SAME ``AskUserQuestionCard`` used inline, answered via
 *   ``/sessions/{id}/actions`` (the kernel ``action_resolved`` then resolves the
 *   notification, so no optimistic removal).
 * - ``task_failed`` → failure summary + a Resume action (``:intervene resume``,
 *   which clears the notification server-side) and a Dismiss.
 */

import { useCallback, useState, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AlertTriangle, MessageCircleQuestion } from "lucide-react";

import {
  dismissNotification,
  sessionsApi,
  tasksApi,
  useTranslation,
  type NotificationEntry,
} from "@valuz/core";
import { AskUserQuestionCard, Button, type AskUserQuestionInput } from "@valuz/ui";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";

export interface NotificationCardProps {
  entry: NotificationEntry;
  onNavigateAway?: () => void;
}

export function NotificationCard({
  entry,
  onNavigateAway,
}: NotificationCardProps): ReactElement {
  if (entry.kind === "question") {
    return <QuestionCard entry={entry} onNavigateAway={onNavigateAway} />;
  }
  if (entry.kind === "backup_failed") {
    return <BackupFailedCard entry={entry} onNavigateAway={onNavigateAway} />;
  }
  return <FailureCard entry={entry} onNavigateAway={onNavigateAway} />;
}

function BackupFailedCard({
  entry,
  onNavigateAway,
}: NotificationCardProps): ReactElement {
  const { t } = useTranslation();
  const navigate = useNavigate();

  // Optimistic — the card leaves the drawer immediately; a failed persist
  // self-heals on the next SSE snapshot.
  const handleDismiss = useCallback(() => {
    dismissNotification(entry.id);
  }, [entry.id]);

  return (
    <CardShell
      icon={<AlertTriangle className="h-3 w-3 text-error-text" />}
      label={t("notification.kindBackupFailed" as I18nKey)}
      title={t("notification.notifBackupFailedTitle" as I18nKey)}
    >
      <div className="flex flex-col gap-3 px-4 py-3">
        {entry.body && (
          <p className="whitespace-pre-wrap text-xs leading-5 text-ink-body">
            {entry.body}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="text-xs"
            onClick={handleDismiss}
          >
            {t("notification.dismiss" as I18nKey)}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-xs"
            onClick={() => {
              onNavigateAway?.();
              navigate(entry.route ?? "/settings?tab=backup");
            }}
          >
            {t("notification.openBackupSettings" as I18nKey)}
          </Button>
        </div>
      </div>
    </CardShell>
  );
}

function CardShell({
  icon,
  label,
  title,
  children,
}: {
  icon: ReactElement;
  label: string;
  title: string;
  children: ReactElement;
}): ReactElement {
  return (
    <div className="rounded-xl border border-surface-border bg-surface shadow-outline">
      <div className="flex flex-col gap-0.5 border-b border-surface-border bg-surface-soft/40 px-4 py-2.5">
        <div className="flex items-center gap-1.5 text-2xs font-medium text-ink-muted">
          {icon}
          {label}
        </div>
        <span className="truncate text-sm font-medium text-ink-heading">
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function QuestionCard({ entry, onNavigateAway }: NotificationCardProps): ReactElement {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);

  const questions =
    (entry.payload?.question_payload as AskUserQuestionInput | undefined)
      ?.questions ?? [];

  const handleSubmit = useCallback(
    async (answers: Record<string, string>) => {
      if (!entry.session_id || !entry.pending_id) return;
      setSubmitting(true);
      try {
        await sessionsApi.submitAction(entry.session_id, {
          pending_id: entry.pending_id,
          decision: "answer",
          answers,
        });
        // No optimistic removal — the ``action_resolved`` event resolves the
        // notification and the SSE ``resolved`` frame clears it.
      } catch (err) {
        setSubmitting(false);
        toast.error(
          err instanceof Error ? err.message : _t("common.saveFailed" as I18nKey),
        );
      }
    },
    [entry.session_id, entry.pending_id],
  );

  return (
    <CardShell
      icon={<MessageCircleQuestion className="h-3 w-3" />}
      label={t("notification.kindQuestion" as I18nKey)}
      title={t("notification.notifQuestionTitle" as I18nKey).replace(
        "{agent}",
        entry.title,
      )}
    >
      <div className="px-1 py-1">
        {questions.length > 0 ? (
          <AskUserQuestionCard
            questions={questions}
            onSubmit={handleSubmit}
            submitting={submitting}
          />
        ) : (
          <div className="flex items-center justify-between px-3 py-3">
            <span className="text-sm text-ink-muted">{entry.body}</span>
            {entry.session_id && (
              <button
                type="button"
                onClick={() => {
                  onNavigateAway?.();
                  navigate(`/conversation/${encodeURIComponent(entry.session_id ?? "")}`);
                }}
                className="rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-surface-muted hover:text-ink-body"
              >
                {t("decisionInbox.openInSession" as I18nKey)}
              </button>
            )}
          </div>
        )}
      </div>
    </CardShell>
  );
}

function FailureCard({ entry, onNavigateAway }: NotificationCardProps): ReactElement {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  // A ``task_failed`` entry has a task_id and offers Resume / Open task; a
  // ``run_failed`` (plain conversation) entry has none — it can only be opened
  // in its session and dismissed.
  const isTaskFailure = Boolean(entry.task_id);

  const handleResume = useCallback(async () => {
    if (!entry.task_id) return;
    setBusy(true);
    try {
      await tasksApi.intervene(entry.task_id, { action: "resume" });
      toast.success(t("task.resumed"));
      // Resume clears the failure notification server-side (resolve_task).
    } catch {
      setBusy(false);
      toast.error(t("task.interveneFailed"));
    }
  }, [entry.task_id, t]);

  // Optimistic — the card leaves the drawer immediately; a failed persist
  // self-heals on the next SSE snapshot.
  const handleDismiss = useCallback(() => {
    dismissNotification(entry.id);
  }, [entry.id]);

  return (
    <CardShell
      icon={<AlertTriangle className="h-3 w-3 text-error-text" />}
      label={t(
        (isTaskFailure
          ? "notification.kindFailure"
          : "notification.kindRunFailed") as I18nKey,
      )}
      title={
        isTaskFailure
          ? entry.title
          : t("notification.notifRunFailedTitle" as I18nKey).replace(
              "{agent}",
              entry.title || "",
            )
      }
    >
      <div className="flex flex-col gap-3 px-4 py-3">
        {entry.body && (
          <p className="whitespace-pre-wrap text-xs leading-5 text-ink-body">
            {entry.body}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="text-xs"
            onClick={handleDismiss}
            disabled={busy}
          >
            {t("notification.dismiss" as I18nKey)}
          </Button>
          {isTaskFailure ? (
            <>
              <Button
                size="sm"
                variant="outline"
                className="text-xs"
                onClick={() => {
                  onNavigateAway?.();
                  if (entry.task_id) {
                    navigate(`/tasks/${encodeURIComponent(entry.task_id)}`);
                  }
                }}
                disabled={busy}
              >
                {t("notification.openTask" as I18nKey)}
              </Button>
              <Button
                size="sm"
                className="text-xs"
                onClick={() => void handleResume()}
                disabled={busy}
                loading={busy}
              >
                {t("notification.resume" as I18nKey)}
              </Button>
            </>
          ) : (
            entry.session_id && (
              <Button
                size="sm"
                variant="outline"
                className="text-xs"
                onClick={() => {
                  onNavigateAway?.();
                  navigate(
                    `/conversation/${encodeURIComponent(entry.session_id ?? "")}`,
                  );
                }}
                disabled={busy}
              >
                {t("decisionInbox.openInSession" as I18nKey)}
              </Button>
            )
          )}
        </div>
      </div>
    </CardShell>
  );
}
