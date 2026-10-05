import type { ReactNode } from "react";
import { useUiBusStore, type UiLine } from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";
import { cn } from "@valuz/ui";

/**
 * Where UI bus pushes are drawn (``modules/plugin_ui/push.py``). Each is
 * mounted into its slot only once the first push of its kind arrives, so a
 * build without plugins that push renders exactly as before.
 */

const TONE_TEXT: Record<string, string> = {
  success: "text-success-text",
  warning: "text-warning-text",
  error: "text-error-text",
};

function Line({ line }: { line: UiLine }) {
  return (
    <div
      data-plugin={line.owner}
      className={cn(
        "text-xs leading-5",
        TONE_TEXT[line.tone ?? ""] ?? "text-ink-muted",
      )}
    >
      {line.text}
    </div>
  );
}

const _noLines: UiLine[] = [];

/** ``ui.status`` — each plugin's status line, above the session's composer. */
export function PluginStatusLine({ sessionId }: { sessionId?: string | null }) {
  const status = useUiBusStore((s) =>
    sessionId ? s.status[sessionId] : undefined,
  );
  const entries = Object.entries(status ?? {});
  if (entries.length === 0) return null;
  return (
    <div
      data-slot="plugin-status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 pb-1.5 text-xs text-ink-muted"
    >
      {entries.map(([owner, text]) => (
        <span key={owner} data-plugin={owner} className="truncate">
          {text}
        </span>
      ))}
    </div>
  );
}

/** ``ui.log`` — the lines pushed while a turn ran, at the end of that turn. */
export function PluginLogLines({
  turn,
  sessionId,
  isLatest,
}: {
  turn?: ConversationTurn;
  sessionId?: string | null;
  isLatest?: boolean;
}) {
  const logs = useUiBusStore((s) =>
    sessionId ? (s.logs[sessionId] ?? _noLines) : _noLines,
  );
  if (!turn || logs.length === 0) return null;
  const start = turn.userTimestamp ?? 0;
  const end = isLatest ? Infinity : (turn.endTimestamp ?? Infinity);
  const lines = logs.filter(
    (line) => line.timestamp >= start && line.timestamp <= end,
  );
  if (lines.length === 0) return null;
  return (
    <div data-slot="plugin-log" className="space-y-0.5">
      {lines.map((line, index) => (
        <Line key={`${line.timestamp}-${index}`} line={line} />
      ))}
    </div>
  );
}

/** ``ui.notice`` — lines under one tool card (a ``conversation.tool-call`` wrapper). */
export function PluginToolNotices({
  toolUseId,
  renderDefault,
}: {
  toolUseId?: string;
  renderDefault?: (overrides?: Record<string, unknown>) => ReactNode;
}) {
  const notices = useUiBusStore((s) =>
    toolUseId ? (s.notices[toolUseId] ?? _noLines) : _noLines,
  );
  const card = renderDefault?.() ?? null;
  if (notices.length === 0) return <>{card}</>;
  return (
    <>
      {card}
      <div data-slot="plugin-notice" className="space-y-0.5 pt-1">
        {notices.map((line, index) => (
          <Line key={`${line.timestamp}-${index}`} line={line} />
        ))}
      </div>
    </>
  );
}
