import { useState, type KeyboardEvent } from "react";
import { Button, Spinner, Textarea } from "@valuz/ui";
import { Square, ArrowUp } from "lucide-react";

interface ChatComposerProps {
  isStreaming: boolean;
  isInterrupting: boolean;
  disabled?: boolean;
  onSend: (text: string) => void | Promise<void>;
  onInterrupt: () => void | Promise<void>;
}

const isImeCompositionEvent = (
  event: KeyboardEvent<HTMLTextAreaElement>,
): boolean => event.nativeEvent.isComposing || event.keyCode === 229;

/**
 * Lean composer paired with the chat-store turn lifecycle:
 *
 * - When idle:        shows ``Send`` (Enter to submit; Shift+Enter for newline).
 * - During streaming: replaces ``Send`` with ``Stop``; clicking calls interrupt.
 * - After Stop click: ``Stop`` enters a ``Stopping...`` state until the turn
 *   actually finalises (chat-store clears ``isInterrupting`` on session.idle).
 */
export const ChatComposer = ({
  isStreaming,
  isInterrupting,
  disabled,
  onSend,
  onInterrupt,
}: ChatComposerProps) => {
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSend = async () => {
    const text = draft.trim();
    if (!text || submitting || isStreaming) return;
    setSubmitting(true);
    try {
      await onSend(text);
      setDraft("");
    } finally {
      setSubmitting(false);
    }
  };

  const handleKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !isImeCompositionEvent(event)
    ) {
      event.preventDefault();
      void handleSend();
    }
  };

  return (
    <div className="rounded-2xl border border-border/70 bg-background/80 p-3 shadow-sm">
      <Textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKey}
        rows={3}
        placeholder={
          isStreaming
            ? "Agent is responding... press Stop to interrupt."
            : "Send a message — Enter to submit, Shift+Enter for newline."
        }
        disabled={disabled || isStreaming}
        className="min-h-20 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
      />
      <div className="mt-2 flex items-center justify-between">
        <div className="text-[11px] text-muted-foreground">
          {isStreaming ? "Streaming response..." : ""}
        </div>
        {isStreaming ? (
          <Button
            type="button"
            variant="destructive"
            size="sm"
            disabled={isInterrupting}
            onClick={() => void onInterrupt()}
            className="gap-1.5"
            data-testid="stop-button"
          >
            {isInterrupting ? (
              <Spinner className="size-3.5" />
            ) : (
              <Square className="size-3.5" />
            )}
            {isInterrupting ? "Stopping..." : "Stop"}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={disabled || submitting || draft.trim().length === 0}
            onClick={() => void handleSend()}
            className="gap-1.5"
            data-testid="send-button"
          >
            <ArrowUp className="size-3.5" />
            Send
          </Button>
        )}
      </div>
    </div>
  );
};
