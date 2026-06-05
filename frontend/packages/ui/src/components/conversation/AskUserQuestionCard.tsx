/**
 * Card rendered in the conversation stream when the agent calls the
 * ``AskUserQuestion`` tool. Parses the structured input (questions with
 * selectable options) and lets the user pick answers, then submit them
 * back as a chat message.
 *
 * Pure presentational — the parent wires the submit callback to the
 * page's message-sending mechanism.
 */
import { memo, useState } from "react";
import { CircleHelp, Loader2, Send } from "lucide-react";

import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export interface AskUserQuestionOption {
  label: string;
  description?: string;
  preview?: string;
}

export interface AskUserQuestionItem {
  question: string;
  header?: string;
  options: AskUserQuestionOption[];
  multiSelect?: boolean;
}

export interface AskUserQuestionInput {
  questions: AskUserQuestionItem[];
}

interface AskUserQuestionCardProps {
  questions: AskUserQuestionItem[];
  /** Called with the user's selections when they hit submit. */
  onSubmit: (answers: Record<string, string>) => void;
  /** When true, shows a loading spinner on the submit button. */
  submitting?: boolean;
  /** When true, the card is in a read-only answered state. */
  answered?: boolean;
}

function QuestionBlock({
  question,
  header,
  options,
  multiSelect,
  answered,
  selected,
  otherSelected,
  customText,
  onToggle,
  onToggleOther,
  onCustomTextChange,
}: {
  question: string;
  header?: string;
  options: AskUserQuestionOption[];
  multiSelect?: boolean;
  answered: boolean;
  selected: Set<string>;
  otherSelected: boolean;
  customText: string;
  onToggle: (label: string) => void;
  onToggleOther: () => void;
  onCustomTextChange: (text: string) => void;
}) {
  const { t } = useI18n();
  return (
    <fieldset disabled={answered} className="space-y-1.5">
      <div className="flex items-center gap-2">
        {header ? (
          <span className="inline-flex rounded-md bg-brand/10 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-brand">
            {header}
          </span>
        ) : null}
        <span className="text-sm font-medium text-ink-heading">{question}</span>
      </div>
      <div className="space-y-1">
        {options.map((opt) => {
          const isSelected = selected.has(opt.label);
          return (
            <button
              key={opt.label}
              type="button"
              onClick={() => onToggle(opt.label)}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-md border px-3 py-2 text-left text-[13px] transition-colors",
                isSelected
                  ? "border-brand/40 bg-brand/5"
                  : "border-surface-border bg-surface hover:border-surface-border/80 hover:bg-surface-2",
                answered && "cursor-default",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors",
                  isSelected ? "border-brand bg-brand" : "border-ink-label/30",
                  multiSelect ? "rounded-sm" : "rounded-full",
                )}
              >
                {isSelected ? (
                  multiSelect ? (
                    <svg
                      viewBox="0 0 12 12"
                      className="h-2.5 w-2.5 text-white"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path d="M2 6l3 3 5-5" />
                    </svg>
                  ) : (
                    <span className="block h-1.5 w-1.5 rounded-full bg-white" />
                  )
                ) : null}
              </span>
              <div className="min-w-0 flex-1">
                <span className="font-medium text-ink-body">{opt.label}</span>
                {opt.description ? (
                  <p className="mt-0.5 text-[12px] leading-snug text-ink-meta">
                    {opt.description}
                  </p>
                ) : null}
              </div>
            </button>
          );
        })}
        {/* "Other" option — a div (not button) so the nested input can
            take focus and keystrokes; nesting <input> inside <button> is
            invalid HTML and breaks focus in Chrome/Edge. */}
        <div
          role="button"
          tabIndex={answered ? -1 : 0}
          aria-pressed={otherSelected}
          onClick={() => !answered && onToggleOther()}
          onKeyDown={(e) => {
            if (answered) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onToggleOther();
            }
          }}
          className={cn(
            "flex w-full items-start gap-2.5 rounded-md border px-3 py-2 text-left text-[13px] transition-colors",
            otherSelected
              ? "border-brand/40 bg-brand/5"
              : "border-surface-border bg-surface hover:border-surface-border/80 hover:bg-surface-2",
            answered ? "cursor-default" : "cursor-pointer",
          )}
        >
          <span
            className={cn(
              "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors",
              otherSelected ? "border-brand bg-brand" : "border-ink-label/30",
              multiSelect ? "rounded-sm" : "rounded-full",
            )}
          >
            {otherSelected ? (
              multiSelect ? (
                <svg
                  viewBox="0 0 12 12"
                  className="h-2.5 w-2.5 text-white"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path d="M2 6l3 3 5-5" />
                </svg>
              ) : (
                <span className="block h-1.5 w-1.5 rounded-full bg-white" />
              )
            ) : null}
          </span>
          <div className="min-w-0 flex-1" onClick={(e) => e.stopPropagation()}>
            <span className="font-medium text-ink-body">
              {t("common.other")}
            </span>
            <input
              type="text"
              disabled={answered}
              value={customText}
              onChange={(e) => onCustomTextChange(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
              placeholder={t("conversation.customAnswerPlaceholder")}
              className="mt-1 block w-full rounded border border-surface-border bg-surface px-2 py-1 text-[12px] text-ink-body placeholder:text-ink-meta/60 focus:border-brand focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            />
          </div>
        </div>
      </div>
    </fieldset>
  );
}

export const AskUserQuestionCard = memo(function AskUserQuestionCard({
  questions,
  onSubmit,
  submitting,
  answered,
}: AskUserQuestionCardProps) {
  const { t } = useI18n();
  // Track selections per question index
  const [selections, setSelections] = useState<Record<number, Set<string>>>({});
  // Track whether "Other" is selected per question index
  const [otherSelected, setOtherSelected] = useState<Record<number, boolean>>(
    {},
  );
  // Track custom text per question index
  const [customTexts, setCustomTexts] = useState<Record<number, string>>({});

  const toggleOption = (qIdx: number, label: string) => {
    const q = questions[qIdx];
    setSelections((prev) => {
      const current = new Set(prev[qIdx] ?? []);
      if (q?.multiSelect) {
        if (current.has(label)) {
          current.delete(label);
        } else {
          current.add(label);
        }
      } else {
        // Single select: replace
        current.clear();
        current.add(label);
      }
      return { ...prev, [qIdx]: current };
    });
    // Clear "Other" and custom text when picking a predefined option in single-select
    if (!q?.multiSelect) {
      setOtherSelected((prev) => ({ ...prev, [qIdx]: false }));
      setCustomTexts((prev) => ({ ...prev, [qIdx]: "" }));
    }
  };

  const toggleOther = (qIdx: number) => {
    const q = questions[qIdx];
    setOtherSelected((prev) => {
      const next = !prev[qIdx];
      if (!q?.multiSelect && next) {
        // Single select: clear predefined selections
        setSelections((s) => {
          const updated = { ...s };
          delete updated[qIdx];
          return updated;
        });
      }
      return { ...prev, [qIdx]: next };
    });
    if (!q?.multiSelect) {
      setCustomTexts((prev) => ({ ...prev, [qIdx]: "" }));
    }
  };

  const setCustomText = (qIdx: number, text: string) => {
    setCustomTexts((prev) => ({ ...prev, [qIdx]: text }));
    // Typing into "Other" auto-selects it
    const q = questions[qIdx];
    if (!q?.multiSelect && text) {
      setSelections((prev) => {
        const updated = { ...prev };
        delete updated[qIdx];
        return updated;
      });
      setOtherSelected((prev) => ({ ...prev, [qIdx]: true }));
    }
  };

  const canSubmit = questions.every((_q, idx) => {
    const hasSelection = (selections[idx]?.size ?? 0) > 0;
    const hasOther = otherSelected[idx];
    return hasSelection || hasOther;
  });

  const handleSubmit = () => {
    if (!canSubmit || submitting || answered) return;
    const answers: Record<string, string> = {};
    questions.forEach((q, idx) => {
      const selected = selections[idx];
      const custom = customTexts[idx]?.trim();
      const selectedArr = selected ? [...selected] : [];
      if (otherSelected[idx]) {
        selectedArr.push(custom || t("common.other"));
      }
      answers[q.question] = selectedArr.join(", ");
    });
    onSubmit(answers);
  };

  return (
    <div className="rounded-lg border border-surface-border bg-surface-soft shadow-sm">
      <div className="flex items-center gap-2 px-4 py-3">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
          <CircleHelp className="h-3.5 w-3.5" />
        </div>
        <span className="text-sm font-medium text-ink-heading">
          {answered
            ? t("conversation.answered")
            : t("conversation.pleaseSelect")}
        </span>
      </div>

      <div className="space-y-4 px-4 pb-3">
        {questions.map((q, idx) => (
          <QuestionBlock
            key={idx}
            question={q.question}
            header={q.header}
            options={q.options}
            multiSelect={q.multiSelect}
            answered={!!answered}
            selected={selections[idx] ?? new Set()}
            otherSelected={!!otherSelected[idx]}
            customText={customTexts[idx] ?? ""}
            onToggle={(label) => toggleOption(idx, label)}
            onToggleOther={() => toggleOther(idx)}
            onCustomTextChange={(text) => setCustomText(idx, text)}
          />
        ))}
      </div>

      {!answered ? (
        <div className="flex items-center justify-end border-t border-surface-border px-4 py-2">
          <button
            type="button"
            disabled={!canSubmit || submitting}
            onClick={handleSubmit}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "bg-brand text-white hover:bg-brand/90",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {submitting ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : (
              <Send className="mr-1.5 h-3 w-3" />
            )}
            {t("conversation.answer")}
          </button>
        </div>
      ) : null}
    </div>
  );
});

/**
 * Parse the JSON tool input into ``AskUserQuestionInput`` if possible.
 * Returns ``null`` on malformed input — caller falls back to the generic
 * tool renderer.
 */
export function parseAskUserQuestionInput(
  raw: string | undefined | null,
): AskUserQuestionInput | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (
      typeof obj !== "object" ||
      obj === null ||
      !Array.isArray(obj.questions)
    ) {
      return null;
    }
    return obj as AskUserQuestionInput;
  } catch {
    return null;
  }
}
