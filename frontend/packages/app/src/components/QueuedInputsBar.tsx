import { useState } from "react";
import { Button, Textarea } from "@valuz/ui";
import { useTranslation, type QueuedInput } from "@valuz/core";
import {
  Check,
  CornerDownRight,
  Loader2,
  Paperclip,
  Pencil,
  Play,
  Trash2,
  X,
} from "lucide-react";

interface QueuedInputsBarProps {
  queue: QueuedInput[];
  /**
   * The item the drain is executing right now (already out of ``queue``, its
   * turn possibly not yet visible in the transcript). Rendered as a
   * non-editable "sending" bubble so the accepted message never disappears
   * from both the queue bar and the transcript at once.
   */
  dispatching?: QueuedInput | null;
  paused: boolean;
  onEdit: (queueId: string, text: string) => void | Promise<void>;
  onDelete: (queueId: string) => void | Promise<void>;
  onResume: () => void | Promise<void>;
  /** Steer — send this item now, interrupting the active turn. */
  onSteer: (queueId: string) => void | Promise<void>;
}

/**
 * Pending follow-up inputs queued while a turn is running, rendered above the
 * Composer (docs/design/session-input-queue.md). Each item drains FIFO after
 * the active turn; queued items can be edited, deleted, or steered (sent now,
 * interrupting the active turn). After an interrupt the queue is soft-paused
 * and shows a "Continue" affordance.
 */
export const QueuedInputsBar = ({
  queue,
  dispatching = null,
  paused,
  onEdit,
  onDelete,
  onResume,
  onSteer,
}: QueuedInputsBarProps) => {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  if (queue.length === 0 && !dispatching) return null;

  const startEdit = (id: string, text: string) => {
    setEditingId(id);
    setEditText(text);
  };

  const saveEdit = async (id: string) => {
    const text = editText.trim();
    if (text) await onEdit(id, text);
    setEditingId(null);
  };

  return (
    // Match the Composer's outer box (``mx-auto max-w-[760px]``) so the queue
    // lines up with the input below it instead of spanning the full column.
    <div className="mx-auto mb-1.5 w-full max-w-[760px] space-y-1.5">
      <div className="flex items-center justify-between px-1">
        <span className="text-2xs text-ink-meta">
          {queue.length > 0
            ? `${t("common.queueRunsAfter")} (${queue.length})`
            : t("common.queueSending")}
        </span>
        {paused && (
          <Button
            type="button"
            size="xs"
            variant="outline"
            className="gap-1"
            onClick={() => void onResume()}
          >
            <Play className="size-3" />
            {t("common.queueContinue")}
          </Button>
        )}
      </div>

      {dispatching && (
        <div
          key={dispatching.id}
          className="rounded-lg border border-surface-border bg-surface-soft px-3 py-2"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-sm text-ink-body">
                {dispatching.text}
              </p>
              {dispatching.attachment_count > 0 && (
                <p className="mt-0.5 flex items-center gap-1 text-xs text-ink-meta">
                  <Paperclip className="size-3" />
                  {dispatching.attachment_count}
                </p>
              )}
            </div>
            <span
              className="flex h-6 shrink-0 items-center gap-1 text-xs text-ink-meta"
              title={t("common.queueSending")}
            >
              <Loader2 className="size-3 animate-spin" />
              {t("common.queueSending")}
            </span>
          </div>
        </div>
      )}

      {queue.map((item) => (
        <div
          key={item.id}
          className="rounded-lg border border-surface-border bg-surface-soft px-3 py-2"
        >
          {editingId === item.id ? (
            <div className="space-y-1.5">
              <Textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                rows={2}
                className="resize-none text-sm"
              />
              <div className="flex justify-end gap-1.5">
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={() => setEditingId(null)}
                  aria-label={t("common.cancel")}
                >
                  <X className="size-3" />
                </Button>
                <Button
                  type="button"
                  size="xs"
                  onClick={() => void saveEdit(item.id)}
                  aria-label={t("common.save")}
                >
                  <Check className="size-3" />
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm text-ink-body">
                  {item.text}
                </p>
                {item.status === "blocked" && (
                  <p className="mt-0.5 text-2xs text-error-text">
                    {item.error_message || t("common.queueBlocked")}
                  </p>
                )}
                {item.attachment_count > 0 && (
                  <p className="mt-0.5 flex items-center gap-1 text-2xs text-ink-meta">
                    <Paperclip className="size-3" />
                    {item.attachment_count}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 gap-0.5">
                {item.status === "queued" && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="size-6 text-brand hover:text-brand"
                    onClick={() => void onSteer(item.id)}
                    aria-label={t("common.queueSteer")}
                    title={t("common.queueSteer")}
                  >
                    <CornerDownRight className="size-3" />
                  </Button>
                )}
                {item.status === "queued" && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="size-6"
                    onClick={() => startEdit(item.id, item.text)}
                    aria-label={t("common.edit")}
                  >
                    <Pencil className="size-3" />
                  </Button>
                )}
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="size-6"
                  onClick={() => void onDelete(item.id)}
                  aria-label={t("common.delete")}
                >
                  <Trash2 className="size-3" />
                </Button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
