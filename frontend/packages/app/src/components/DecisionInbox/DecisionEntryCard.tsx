/**
 * One pending-decision card inside the inbox drawer (ADR-022).
 *
 * Header carries the cross-session context the user needs to answer
 * without navigating: agent · project · task · subtask. Body reuses the
 * SAME ``AskUserQuestionCard`` rendered inline in conversation pages, so
 * the answer UX is identical everywhere. Submit POSTs to the existing
 * ``/v1/sessions/{id}/actions`` endpoint; we DON'T optimistically remove
 * the entry — the kernel's paired ``action_resolved`` event fans back
 * through SSE and clears it from the store, keeping the drawer and any
 * open conversation page in lock-step.
 */

import { useCallback, useState, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { sessionsApi, type DecisionEntry } from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { AskUserQuestionCard, type AskUserQuestionInput } from "@valuz/ui";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";

export interface DecisionEntryCardProps {
  entry: DecisionEntry;
  /** Called after the user clicks "前往会话" so the drawer can close. */
  onNavigateAway?: () => void;
}

export function DecisionEntryCard({
  entry,
  onNavigateAway,
}: DecisionEntryCardProps): ReactElement {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);

  // ``question_payload`` is the raw kernel AskUserQuestion blob
  // (``{questions: [...]}``) typed as ``Record<string, unknown>`` over
  // the wire. Cast through ``unknown`` since the two shapes are
  // intentionally disjoint at the type level.
  const questions =
    (entry.question_payload as unknown as AskUserQuestionInput | undefined)
      ?.questions ?? [];

  const handleSubmit = useCallback(
    async (answers: Record<string, string>) => {
      setSubmitting(true);
      try {
        await sessionsApi.submitAction(entry.session_id, {
          pending_id: entry.pending_id,
          decision: "answer",
          answers,
        });
        // No optimistic removal — wait for the SSE ``resolved`` frame to
        // clear this entry from the store. That keeps the drawer +
        // source conversation page consistent (both react to the same
        // event) and avoids a flash if the POST is retried.
      } catch (err) {
        setSubmitting(false);
        toast.error(
          err instanceof Error
            ? err.message
            : _t("common.saveFailed" as I18nKey),
        );
      }
    },
    [entry.session_id, entry.pending_id],
  );

  const handleOpenInSession = useCallback(() => {
    onNavigateAway?.();
    navigate(`/conversation/${encodeURIComponent(entry.session_id)}`);
  }, [entry.session_id, navigate, onNavigateAway]);

  return (
    <div className="rounded-xl border border-surface-border bg-surface shadow-sm">
      {/* Context header */}
      <div className="flex flex-col gap-1 border-b border-surface-border bg-surface-soft/40 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-1.5 text-xs text-ink-muted">
          <span className="shrink-0">🤖</span>
          <span className="shrink-0 font-medium text-ink-body">
            {entry.agent_slug}
          </span>
          {entry.project_title && (
            <>
              <span className="text-ink-faint">·</span>
              {entry.project_emoji && (
                <span className="shrink-0">{entry.project_emoji}</span>
              )}
              <span className="truncate">{entry.project_title}</span>
            </>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-1.5 text-sm">
          <span className="truncate font-medium text-ink-heading">
            {entry.task_title}
          </span>
          {entry.subtask_label && (
            <>
              <span className="text-ink-faint">·</span>
              <span className="shrink-0 rounded bg-surface-soft px-1.5 py-0.5 text-2xs text-ink-muted">
                {t("decisionInbox.headerSubtask" as I18nKey)}
              </span>
              <span className="truncate text-xs text-ink-body">
                {entry.subtask_label}
              </span>
            </>
          )}
        </div>
      </div>

      {/* Question — reused AskUserQuestionCard */}
      <div className="px-1 py-1">
        {questions.length > 0 ? (
          <AskUserQuestionCard
            questions={questions}
            onSubmit={handleSubmit}
            submitting={submitting}
          />
        ) : (
          <div className="px-3 py-4 text-sm text-ink-muted">
            {t("decisionInbox.emptyTitle" as I18nKey)}
          </div>
        )}
      </div>

      {/* Secondary link */}
      <div className="flex justify-end border-t border-surface-border px-4 py-2">
        <button
          type="button"
          onClick={handleOpenInSession}
          className="rounded-md px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-muted hover:text-ink-body"
        >
          {t("decisionInbox.openInSession" as I18nKey)}
        </button>
      </div>
    </div>
  );
}
