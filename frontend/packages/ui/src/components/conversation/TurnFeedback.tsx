import { useState } from "react";
import { Check, Plus, RotateCcw, ThumbsDown, ThumbsUp, type LucideIcon } from "lucide-react";
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
import { DialogFooter } from "../ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Textarea } from "../ui/textarea";

/**
 * 👍/👎 on an assistant turn (docs/design/feedback-signals.md §8).
 *
 * One entry button in the action row ("评价回复"). Clicking it opens a
 * two-item menu — 回复优秀 / 回复不佳 (+ 撤销评价 once rated). Choosing a
 * side records the rating IMMEDIATELY (the signal survives everything that
 * follows) and then offers the optional "提交反馈" dialog: multi-select
 * reason chips for that side plus a free-text box, which refine the same
 * row. Skipping the dialog keeps the thumb.
 *
 * ``@valuz/ui`` owns no transport: the host passes ``onRate`` down, the same
 * way it passes ``onRetry``.
 */
export interface TurnFeedbackControlProps {
  rating?: FeedbackValue | null;
  onRate: (value: FeedbackValue | null, details?: TurnFeedbackDetails) => void;
}

const ENTRY_BUTTON =
  "flex h-7 w-7 items-center justify-center rounded transition-colors hover:bg-surface-muted";

export function TurnFeedbackControl({ rating, onRate }: TurnFeedbackControlProps) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const [detailsFor, setDetailsFor] = useState<FeedbackValue | null>(null);
  const EntryIcon = rating === "down" ? ThumbsDown : ThumbsUp;
  const rateLabel = t("conversation.feedback.rate" as Parameters<typeof t>[0]);

  const choose = (value: FeedbackValue) => {
    setMenuOpen(false);
    // Re-choosing the current side only reopens the details dialog — the
    // row already exists; 撤销 is its own item.
    if (rating !== value) onRate(value);
    setDetailsFor(value);
  };

  return (
    <>
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-pressed={rating != null}
            aria-label={rateLabel}
            title={rateLabel}
            className={`${ENTRY_BUTTON} ${rating ? "text-brand" : "text-ink-body"}`}
          >
            <EntryIcon className={`h-3.5 w-3.5 ${rating ? "fill-current" : ""}`} />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" side="bottom" className="w-44 p-1">
          <MenuItem
            icon={ThumbsUp}
            label={t("conversation.feedback.good" as Parameters<typeof t>[0])}
            active={rating === "up"}
            onClick={() => choose("up")}
          />
          <MenuItem
            icon={ThumbsDown}
            label={t("conversation.feedback.bad" as Parameters<typeof t>[0])}
            active={rating === "down"}
            onClick={() => choose("down")}
          />
          {rating ? (
            <MenuItem
              icon={RotateCcw}
              label={t("conversation.feedback.withdraw" as Parameters<typeof t>[0])}
              onClick={() => {
                setMenuOpen(false);
                onRate(null);
              }}
            />
          ) : null}
        </PopoverContent>
      </Popover>
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

function MenuItem({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      aria-current={active ? "true" : undefined}
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors hover:bg-surface-muted ${
        active ? "text-brand" : "text-ink-body"
      }`}
    >
      <Icon className={`h-3.5 w-3.5 ${active ? "fill-current" : ""}`} aria-hidden />
      <span className="flex-1 text-left">{label}</span>
      {active ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
    </button>
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
    value === "up" ? FEEDBACK_POSITIVE_REASON_CODES : FEEDBACK_NEGATIVE_REASON_CODES;
  const canSubmit = selected.length > 0 || text.trim().length > 0;

  const toggle = (code: FeedbackReasonCode) =>
    setSelected((current) =>
      current.includes(code) ? current.filter((c) => c !== code) : [...current, code],
    );

  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("conversation.feedback.dialogTitle" as Parameters<typeof t>[0])}
      maxWidthClass="sm:max-w-lg"
      footer={
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("conversation.feedback.skip" as Parameters<typeof t>[0])}
          </Button>
          <Button
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
        </DialogFooter>
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
              {t(`conversation.feedback.reason.${code}` as Parameters<typeof t>[0])}
            </button>
          );
        })}
      </div>
      <Textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={t("conversation.feedback.detailsPlaceholder" as Parameters<typeof t>[0])}
        rows={4}
        maxLength={500}
      />
      <p className="text-xs text-ink-muted">
        {t("conversation.feedback.privacyNote" as Parameters<typeof t>[0])}
      </p>
    </FormDialog>
  );
}
