import { useState } from "react";
import { Check, Plus, ThumbsDown, ThumbsUp } from "lucide-react";
import {
  FEEDBACK_NEGATIVE_REASON_CODES,
  FEEDBACK_POSITIVE_REASON_CODES,
  type FeedbackReasonCode,
  type FeedbackValue,
  type TurnFeedbackDetails,
} from "@valuz/shared";
import { useI18n } from "../../hooks/use-i18n";
import { FormDialog } from "../common/FormDialog";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { TurnActionButton } from "./TurnActionButton";

/**
 * 👍/👎 on an assistant turn (docs/design/feedback-signals.md §8).
 *
 * Two buttons side by side, the way every other chat surface puts them: one
 * click rates, a second click on the same thumb withdraws it, a click on the
 * other side switches. Rating records IMMEDIATELY (the signal survives
 * everything that follows) and then offers the optional "提交反馈" dialog —
 * reason chips for that side plus a free-text box, which refine the same row.
 * Closing the dialog (✕ / Escape) keeps the thumb.
 *
 * This was one entry button opening a two-item menu: a click and a popover
 * spent saying what two adjacent icons say on their own.
 *
 * ``@valuz/ui`` owns no transport: the host passes ``onRate`` down, the same
 * way it passes ``onRetry``.
 */
export interface TurnFeedbackControlProps {
  rating?: FeedbackValue | null;
  onRate: (value: FeedbackValue | null, details?: TurnFeedbackDetails) => void;
}

const SIDES = [
  { value: "up", Icon: ThumbsUp, key: "conversation.feedback.good" },
  { value: "down", Icon: ThumbsDown, key: "conversation.feedback.bad" },
] as const;

export function TurnFeedbackControl({
  rating,
  onRate,
}: TurnFeedbackControlProps) {
  const { t } = useI18n();
  const [detailsFor, setDetailsFor] = useState<FeedbackValue | null>(null);

  const toggle = (value: FeedbackValue) => {
    // The thumb already carrying the rating is the withdraw action.
    if (rating === value) {
      onRate(null);
      return;
    }
    onRate(value);
    setDetailsFor(value);
  };

  return (
    <>
      {SIDES.map(({ value, Icon, key }) => {
        const chosen = rating === value;
        const sideLabel = t(key as Parameters<typeof t>[0]);
        return (
          <TurnActionButton
            key={value}
            // Hover on the chosen thumb says what the click does now —
            // 移除"回复优秀"反馈 — rather than repeating the side's name.
            label={
              chosen
                ? t("conversation.feedback.remove" as Parameters<typeof t>[0], {
                    label: sideLabel,
                  })
                : sideLabel
            }
            pressed={chosen}
            active={chosen}
            onClick={() => toggle(value)}
          >
            {/* The chosen thumb thickens its outline rather than filling —
                a solid glyph at this size reads as a block of colour. */}
            <Icon className="h-3.5 w-3.5" strokeWidth={chosen ? 2.75 : 2} />
          </TurnActionButton>
        );
      })}
      {detailsFor ? (
        <FeedbackDetailsDialog
          key={detailsFor}
          value={detailsFor}
          onClose={() => setDetailsFor(null)}
          onSubmit={(details) => {
            onRate(detailsFor, details);
            setDetailsFor(null);
          }}
        />
      ) : null}
    </>
  );
}

/**
 * "提交反馈" — the optional second step. Mounted only while open (the
 * ``key`` on the caller resets chips + text per side), so no effect is
 * needed to clear state between openings.
 */
export function FeedbackDetailsDialog({
  value,
  onClose,
  onSubmit,
}: {
  value: FeedbackValue;
  onClose: () => void;
  onSubmit: (details: TurnFeedbackDetails) => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<FeedbackReasonCode[]>([]);
  const [text, setText] = useState("");
  const codes =
    value === "up"
      ? FEEDBACK_POSITIVE_REASON_CODES
      : FEEDBACK_NEGATIVE_REASON_CODES;
  const canSubmit = selected.length > 0 || text.trim().length > 0;

  const toggle = (code: FeedbackReasonCode) =>
    setSelected((current) =>
      current.includes(code)
        ? current.filter((c) => c !== code)
        : [...current, code],
    );

  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("conversation.feedback.dialogTitle" as Parameters<typeof t>[0])}
      maxWidthClass="sm:max-w-lg"
      // One full-width primary action; the dialog's own ✕ (or Escape) is
      // the skip — the thumb already landed, so there is nothing to cancel.
      footer={
        <Button
          className="w-full"
          disabled={!canSubmit}
          onClick={() =>
            onSubmit({
              reasonCodes: selected.length ? selected : undefined,
              reason: text.trim() || undefined,
            })
          }
        >
          {t("conversation.feedback.submit" as Parameters<typeof t>[0])}
        </Button>
      }
    >
      <div className="flex flex-wrap gap-2">
        {codes.map((code) => {
          const on = selected.includes(code);
          return (
            <button
              key={code}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(code)}
              className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-sm transition-colors ${
                on
                  ? "border-brand bg-brand-light text-brand"
                  : "border-surface-border text-ink-body hover:bg-surface-muted"
              }`}
            >
              {on ? (
                <Check className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <Plus className="h-3.5 w-3.5" aria-hidden />
              )}
              {t(
                `conversation.feedback.reason.${code}` as Parameters<
                  typeof t
                >[0],
              )}
            </button>
          );
        })}
      </div>
      <Textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={t(
          "conversation.feedback.detailsPlaceholder" as Parameters<typeof t>[0],
        )}
        rows={4}
        maxLength={500}
      />
      <p className="text-xs text-ink-muted">
        {t("conversation.feedback.privacyNote" as Parameters<typeof t>[0])}
      </p>
    </FormDialog>
  );
}
