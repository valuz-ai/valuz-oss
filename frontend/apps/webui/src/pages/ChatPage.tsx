import { useEffect, useState } from "react";
import { Badge } from "@valuz/ui";
import { useChatStore, useChatSession, useSessionStore } from "@valuz/core";
import { SessionPicker } from "../components/chat/SessionPicker";
import { MessageList } from "../components/chat/MessageList";
import { ChatComposer } from "../components/chat/ChatComposer";
import { ConnectionStatusBadge } from "../components/chat/ConnectionStatusBadge";
import { RecoveryBanner } from "../components/chat/RecoveryBanner";

/**
 * Full chat project. Selects a session in the sidebar, drives the
 * chat-store via ``useChatSession``, and renders three lifecycle
 * affordances:
 *
 * - ``ConnectionStatusBadge`` — always-visible header chip showing the
 *   live SSE state (Live / Connecting / Reconnecting / Disconnected).
 * - ``RecoveryBanner`` — appears only when auto-reconnect exhausts the
 *   backoff schedule; one-click manual recovery.
 * - ``ChatComposer`` Stop button — swaps in for Send while a turn is
 *   streaming; clicking calls the interrupt endpoint.
 */
export const ChatPage = () => {
  const sessions = useSessionStore((s) => s.sessions);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  // Auto-pick the first session once the list loads. Subsequent picks
  // are user-driven via SessionPicker.
  useEffect(() => {
    if (!activeSessionId && sessions.length > 0) {
      setActiveSessionId(sessions[0]!.id);
    }
  }, [sessions, activeSessionId]);

  useChatSession(activeSessionId);

  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const isInterrupting = useChatStore((s) => s.isInterrupting);
  const sessionStatus = useChatStore((s) => s.sessionStatus);
  const connection = useChatStore((s) => s.connection);
  const send = useChatStore((s) => s.send);
  const interrupt = useChatStore((s) => s.interrupt);
  const reconnect = useChatStore((s) => s.reconnect);

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-0 overflow-hidden rounded-2xl border border-border/70 bg-background/30">
      <SessionPicker
        activeSessionId={activeSessionId}
        onPick={setActiveSessionId}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {activeSessionId ? (
          <>
            <header className="flex items-start justify-between border-b border-border/70 px-4 pb-2.5 pt-5">
              <div className="flex items-center gap-3">
                <h2 className="text-sm font-semibold">Chat</h2>
                {sessionStatus && (
                  <Badge variant="outline" className="text-[10px]">
                    {sessionStatus}
                  </Badge>
                )}
              </div>
              <ConnectionStatusBadge connection={connection} />
            </header>

            <div className="flex min-h-0 flex-1 flex-col px-4">
              <RecoveryBanner connection={connection} onReconnect={reconnect} />
              <div className="min-h-0 flex-1">
                <MessageList
                  messages={messages}
                  streamingText={streaming.text}
                  streamingThinking={streaming.thinking}
                  isStreaming={isStreaming}
                />
              </div>
              <div className="py-3">
                <ChatComposer
                  isStreaming={isStreaming}
                  isInterrupting={isInterrupting}
                  onSend={(text) => send(text)}
                  onInterrupt={() => interrupt()}
                />
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a session from the sidebar to begin.
          </div>
        )}
      </div>
    </div>
  );
};
