import { useState } from "react";
import { Button, Textarea } from "@valuz/ui";
import {
  Check,
  CornerDownRight,
  Paperclip,
  Pencil,
  Play,
  Trash2,
  X,
} from "lucide-react";
import { useChatStore } from "@valuz/core";

/**
 * Pending follow-up inputs queued while a turn is running, rendered above the
 * composer (docs/design/session-input-queue.md). Each item drains FIFO after
 * the active turn; queued items can be edited, deleted, or steered (sent now,
 * interrupting the active turn). After an interrupt the queue is soft-paused
 * and shows a "Continue" affordance.
 */
export const QueuedInputs = () => {
  const queue = useChatStore((s) => s.queue);
  const paused = useChatStore((s) => s.queuePaused);
  const editQueued = useChatStore((s) => s.editQueued);
  const deleteQueued = useChatStore((s) => s.deleteQueued);
  const resumeQueue = useChatStore((s) => s.resumeQueue);
  const steerQueued = useChatStore((s) => s.steerQueued);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  if (queue.length === 0) return null;

  const startEdit = (id: string, text: string) => {
    setEditingId(id);
    setEditText(text);
  };

  const saveEdit = async (id: string) => {
    const text = editText.trim();
    if (text) await editQueued(id, text);
    setEditingId(null);
  };

  return (
    <div className="mb-2 space-y-1.5">
      <div className="flex items-center justify-between px-1">
        <span className="text-2xs text-muted-foreground">
          Queued · runs after the current turn ({queue.length})
        </span>
        {paused && (
          <Button
            type="button"
            size="xs"
            variant="outline"
            className="gap-1"
            onClick={() => void resumeQueue()}
            data-testid="queue-resume"
          >
            <Play className="size-3" />
            Continue
          </Button>
        )}
      </div>

      {queue.map((item) => (
        <div
          key={item.id}
          className="rounded-lg border border-border/70 bg-background/60 px-3 py-2"
          data-testid="queued-item"
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
                >
                  <X className="size-3" />
                </Button>
                <Button
                  type="button"
                  size="xs"
                  onClick={() => void saveEdit(item.id)}
                >
                  <Check className="size-3" />
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm">{item.text}</p>
                {item.status === "blocked" && (
                  <p className="mt-0.5 text-2xs text-destructive">
                    {item.error_message || "Could not run"}
                  </p>
                )}
                {item.attachment_count > 0 && (
                  <p className="mt-0.5 flex items-center gap-1 text-2xs text-muted-foreground">
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
                    onClick={() => void steerQueued(item.id)}
                    title="Send now"
                    data-testid="queued-steer"
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
                    data-testid="queued-edit"
                  >
                    <Pencil className="size-3" />
                  </Button>
                )}
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="size-6"
                  onClick={() => void deleteQueued(item.id)}
                  data-testid="queued-delete"
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
