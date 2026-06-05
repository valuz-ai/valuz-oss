import { useEffect, useRef } from "react";
import { ScrollArea, AssistantMessage, UserMessageBubble } from "@valuz/ui";
import type { ChatMessage } from "@valuz/core";
import { StreamingThinkingPreview } from "./StreamingThinkingPreview";

interface MessageListProps {
  messages: ChatMessage[];
  streamingText: string;
  streamingThinking: string;
  isStreaming: boolean;
}

const StopReasonHint = ({ reason }: { reason: string }) => (
  <span className="ml-2 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-300">
    {reason === "user_interrupt" ? "stopped" : reason}
  </span>
);

export const MessageList = ({
  messages,
  streamingText,
  streamingThinking,
  isStreaming,
}: MessageListProps) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Pin to bottom on new content. Users can break out by scrolling up,
  // but the simple "always-stick" behaviour matches what most chat UIs
  // ship and avoids the extra complexity of intent detection.
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, streamingText, streamingThinking]);

  return (
    <ScrollArea className="h-full pr-2">
      <div ref={scrollRef} className="space-y-5 px-2 pb-4 pt-2">
        {messages.map((msg) => (
          <div key={msg.id} className="flex flex-col gap-1">
            {msg.role === "user" ? (
              <UserMessageBubble>
                <p className="whitespace-pre-wrap">{msg.text}</p>
              </UserMessageBubble>
            ) : (
              <AssistantMessage
                toolCalls={msg.tools.map((t) => (
                  <div
                    key={t.id}
                    className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs"
                  >
                    <div className="font-medium">{t.name}</div>
                    {t.output != null && (
                      <pre
                        className={`mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] ${t.isError ? "text-red-500" : "opacity-80"}`}
                      >
                        {t.output}
                      </pre>
                    )}
                  </div>
                ))}
              >
                {msg.thinking.length > 0 && (
                  <div className="mb-2 space-y-1 border-l-2 border-violet-500/40 pl-2 text-xs italic opacity-70">
                    {msg.thinking.map((t, i) => (
                      <p key={i} className="whitespace-pre-wrap">
                        {t}
                      </p>
                    ))}
                  </div>
                )}
                <p className="whitespace-pre-wrap text-sm leading-6">
                  {msg.text || (msg.stopReason ? "" : "...")}
                  {msg.stopReason && <StopReasonHint reason={msg.stopReason} />}
                </p>
              </AssistantMessage>
            )}
          </div>
        ))}

        {/* Live streaming preview — shown only when a turn is in flight
            and we haven't yet committed the assistant message. */}
        {isStreaming && (streamingText || streamingThinking) && (
          <AssistantMessage>
            <StreamingThinkingPreview content={streamingThinking} />
            {streamingText && (
              <p className="whitespace-pre-wrap text-sm leading-6">
                {streamingText}
                <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-foreground/60 align-middle" />
              </p>
            )}
          </AssistantMessage>
        )}

        {messages.length === 0 && !isStreaming && (
          <div className="flex h-full items-center justify-center pt-24 text-sm text-muted-foreground">
            Send a message to begin the conversation.
          </div>
        )}
      </div>
    </ScrollArea>
  );
};
