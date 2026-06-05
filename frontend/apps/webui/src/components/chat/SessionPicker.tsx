import { useEffect } from "react";
import { Button, ScrollArea, Spinner } from "@valuz/ui";
import { useSessionStore, type SessionListItem } from "@valuz/core";
import { Plus, MessageSquare } from "lucide-react";

interface SessionPickerProps {
  activeSessionId: string | null;
  onPick: (sessionId: string) => void;
  onCreate?: () => void | Promise<void>;
}

export const SessionPicker = ({
  activeSessionId,
  onPick,
  onCreate,
}: SessionPickerProps) => {
  const sessions = useSessionStore((s) => s.sessions);
  const loading = useSessionStore((s) => s.loading);
  const fetchSessions = useSessionStore((s) => s.fetchSessions);

  useEffect(() => {
    void fetchSessions();
  }, [fetchSessions]);

  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border/70 bg-background/40">
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3 py-2.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Sessions
        </span>
        {onCreate && (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2 text-xs"
            onClick={() => void onCreate()}
          >
            <Plus className="size-3.5" />
            New
          </Button>
        )}
      </div>

      <ScrollArea className="flex-1">
        {loading && sessions.length === 0 ? (
          <div className="flex items-center justify-center p-6">
            <Spinner className="size-4" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            No sessions yet.
          </div>
        ) : (
          <ul className="space-y-0.5 p-1.5">
            {sessions.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                active={s.id === activeSessionId}
                onPick={onPick}
              />
            ))}
          </ul>
        )}
      </ScrollArea>
    </div>
  );
};

const SessionRow = ({
  session,
  active,
  onPick,
}: {
  session: SessionListItem;
  active: boolean;
  onPick: (id: string) => void;
}) => {
  const title =
    session.name ??
    session.last_user_message_text?.slice(0, 40) ??
    "Untitled session";
  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(session.id)}
        className={`flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left transition-colors ${
          active
            ? "bg-primary/10 text-foreground"
            : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
        }`}
      >
        <MessageSquare className="mt-0.5 size-3.5 shrink-0 opacity-60" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title}</div>
          <div className="truncate text-[11px] opacity-60">
            {session.status}
          </div>
        </div>
      </button>
    </li>
  );
};
