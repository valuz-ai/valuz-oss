/**
 * Compact "user replied to AskUserQuestion" summary card.
 *
 * Rendered in place of ``AskUserQuestionCard`` once the kernel emits
 * an ``action_resolved`` event with ``decision: "answer"`` for the
 * corresponding pending. The interactive question card disappears and
 * this read-only summary takes its slot, showing each ``question →
 * answer`` pair so the turn transcript stays readable.
 *
 * Pure presentational; the parent picks ``answers`` out of the
 * ``action_resolved`` event and matches it to the source
 * ``AskUserQuestion`` tool call by ``pending_id``.
 */
import { memo } from "react";
import { Check } from "lucide-react";

import { useI18n } from "@valuz/ui/hooks/use-i18n";

import type { AskUserQuestionItem } from "./AskUserQuestionCard";

export interface UserAnswerSummaryCardProps {
  questions: AskUserQuestionItem[];
  /** Map keyed by question text — same shape the kernel ships back in
   *  ``action_resolved.answers``. Multi-select answers come back as
   *  arrays; single-select as plain strings. */
  answers: Record<string, string | string[]>;
}

function _formatAnswer(value: string | string[] | undefined): string {
  if (value === undefined) return "—";
  if (Array.isArray(value)) return value.filter(Boolean).join("、");
  return value || "—";
}

export const UserAnswerSummaryCard = memo(function UserAnswerSummaryCard({
  questions,
  answers,
}: UserAnswerSummaryCardProps) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border border-surface-border bg-surface-soft shadow-sm">
      <div className="flex items-center gap-2 px-4 py-3">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-emerald-50 text-emerald-700">
          <Check className="h-3.5 w-3.5" />
        </div>
        <span className="text-sm font-medium text-ink-heading">
          {t("conversation.askUserAnswerTitle")}
        </span>
      </div>
      <div className="space-y-2 px-4 pb-3">
        {questions.map((q, idx) => {
          const answerText = _formatAnswer(answers[q.question]);
          return (
            <div key={idx} className="space-y-0.5">
              {q.header ? (
                <span className="inline-flex rounded-md bg-brand/10 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-brand">
                  {q.header}
                </span>
              ) : null}
              <p className="text-[13px] text-ink-meta">{q.question}</p>
              <p className="text-[13px] font-medium text-ink-heading">
                <span className="mr-1 text-ink-meta/70">→</span>
                {answerText}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
});
