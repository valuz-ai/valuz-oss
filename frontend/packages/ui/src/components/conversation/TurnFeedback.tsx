import { useState } from "react";
import { Check, Plus, ThumbsDown, ThumbsUp, type LucideIcon } from "lucide-react";
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
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Textarea } from "../ui/textarea";

/**
 * 👍/👎 on an assistant turn (docs/design/feedback-signals.md §8).
 *
 * One entry button in the action row ("评价回复"). Unrated, it opens a
 * two-item menu — 回复优秀 / 回复不佳; rated, the same button becomes the
 * remove action (hover: 移除“回复不佳”反馈), one click. Choosing a side
 * records the rating IMMEDIATELY (the signal survives everything that
 * follows) and then offers the optional "提交反馈" dialog: multi-select
 * reason chips for that side plus a free-text box, which refine the same
 * row. Closing the dialog (✕ / Escape) keeps the thumb.
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

const THUMBS_UP_PATHS = (
  <>
    <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
    <path d="M7 10v12" />
  </>
);
const THUMBS_DOWN_PATHS = (
  <>
    <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
    <path d="M17 14V2" />
  </>
);
const GLYPH_SCALE = 0.62;
const GLYPH_STROKE = 3.2; // ≈ 2 after scaling — matches the sibling lucide icons

/**
 * 👍👎 in one glyph for the UNRATED entry — lucide's thumbs-up (left) and
 * thumbs-down (right, dropped a little) paths side by side, each scaled to
 * ~62%, no occlusion. Same stroke conventions as lucide (currentColor,
 * round caps/joins); the inner stroke width compensates for the scale so it
 * matches the sibling icons at 14px.
 */
function ThumbsUpDown({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={GLYPH_STROKE}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <g transform={`translate(0 0.5) scale(${GLYPH_SCALE})`}>{THUMBS_UP_PATHS}</g>
      <g transform={`translate(9.6 6.5) scale(${GLYPH_SCALE})`}>{THUMBS_DOWN_PATHS}</g>
    </svg>
  );
}

export function TurnFeedbackControl({ rating, onRate }: TurnFeedbackControlProps) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const [detailsFor, setDetailsFor] = useState<FeedbackValue | null>(null);
  // Unrated: the combined 👍👎 glyph; rated: the chosen thumb, filled.
  const RatedIcon = rating === "down" ? ThumbsDown : ThumbsUp;
  const rateLabel = t("conversation.feedback.rate" as Parameters<typeof t>[0]);

  const choose = (value: FeedbackValue) => {
    setMenuOpen(false);
    onRate(value);
    setDetailsFor(value);
  };

  // Rated: the entry IS the remove action (hover says which side it removes),
  // exactly one click to undo; the menu only exists while unrated.
  if (rating) {
    const sideLabel = t(
      (rating === "up"
        ? "conversation.feedback.good"
        : "conversation.feedback.bad") as Parameters<typeof t>[0],
    );
    const removeLabel = t("conversation.feedback.remove" as Parameters<typeof t>[0], {
      label: sideLabel,
    });
    return (
      <>
        <button
          type="button"
          aria-pressed
          aria-label={removeLabel}
          title={removeLabel}
          onClick={() => onRate(null)}
          className={`${ENTRY_BUTTON} text-brand`}
        >
          <RatedIcon className="h-3.5 w-3.5 fill-current" />
        </button>
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

  return (
    <>
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-pressed={false}
            aria-label={rateLabel}
            title={rateLabel}
            className={`${ENTRY_BUTTON} text-ink-body`}
          >
            <ThumbsUpDown className="h-4 w-4" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" side="bottom" className="w-44 p-1">
          <MenuItem
            icon={ThumbsUp}
            label={t("conversation.feedback.good" as Parameters<typeof t>[0])}
            onClick={() => choose("up")}
          />
          <MenuItem
            icon={ThumbsDown}
            label={t("conversation.feedback.bad" as Parameters<typeof t>[0])}
            onClick={() => choose("down")}
          />
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
