import { useI18n } from "../../hooks/use-i18n";
import { Card, CardContent } from "../ui/card";

export type ExecutionLogTriggerType =
  | "cron"
  | "interval"
  | "manual"
  | "recovered_skip";

export interface ExecutionLogRow {
  /** Unique per-run id (e.g. the backend's ``run.id``). Used as the
   *  React key so 同分钟内的多条 run 不会撞 key — keying on
   *  ``time + output`` alone breaks when a task fails 2+ times in
   *  the same minute with the same error_code. */
  id: string;
  time: string;
  status: "ok" | "err" | "skip" | "pending";
  duration: string;
  output: string;
  triggerType?: ExecutionLogTriggerType;
  /** Display name of the schedule/automation task that fired this run.
   *  Renders as a clickable link (when ``sessionId`` is present) so the
   *  user can jump straight from a recent-execution row to the session
   *  the run produced. Undefined when the lookup couldn't resolve a
   *  task — defensive against deleted tasks; the link is suppressed. */
  taskName?: string;
  /** Session id created by this run. ``null`` for runs that haven't
   *  produced a session yet (queued / running but pre-spawn,
   *  ``recovered_skip``). The link is suppressed when null. */
  sessionId?: string | null;
}

export interface ExecutionLogProps {
  rows: ExecutionLogRow[];
  /** Click handler for the per-row task-name link. Receives the
   *  ``sessionId`` from the row. Wire to ``navigate(`/conversation/${id}`)``
   *  at the call site. Omit to render the task name as plain text. */
  onSessionClick?: (sessionId: string) => void;
}

function cnStatus(status: string) {
  if (status === "ok")
    return "bg-[#53cb76]/10 text-[#53cb76] [&_[data-slot=status-dot]]:bg-[#53cb76]";
  if (status === "err")
    return "bg-[#f54b4b]/10 text-[#f54b4b] [&_[data-slot=status-dot]]:bg-[#f54b4b]";
  if (status === "pending")
    return "bg-brand/10 text-brand [&_[data-slot=status-dot]]:bg-brand";
  return "bg-surface-soft text-ink-meta";
}

export const ExecutionLog = ({ rows, onSessionClick }: ExecutionLogProps) => {
  // i18n hook lives at the component level (per project rule: no parent
  // closure). Status / trigger labels resolve fresh on every render so a
  // locale flip rebuilds the badge text without the component caching
  // stale strings.
  const { t } = useI18n();

  const labelForStatus = (status: string): string => {
    if (status === "ok")
      return t("automation.execStatusOk" as Parameters<typeof t>[0]);
    if (status === "err")
      return t("automation.execStatusErr" as Parameters<typeof t>[0]);
    if (status === "pending")
      return t("automation.execStatusPending" as Parameters<typeof t>[0]);
    return t("automation.execStatusSkip" as Parameters<typeof t>[0]);
  };

  const labelForTrigger = (type?: ExecutionLogTriggerType): string => {
    if (type === "manual")
      return t("automation.execTriggerManual" as Parameters<typeof t>[0]);
    if (type === "interval")
      return t("automation.execTriggerInterval" as Parameters<typeof t>[0]);
    if (type === "recovered_skip")
      return t(
        "automation.execTriggerRecoveredSkip" as Parameters<typeof t>[0],
      );
    return t("automation.execTriggerCron" as Parameters<typeof t>[0]);
  };

  return (
    <Card
      className="gap-0 overflow-hidden border-0 py-0 shadow-none"
      style={{
        fontFamily:
          '"PingFang SC", "PingFang", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
      }}
    >
      <CardContent className="px-0 py-0">
        {rows.map((row) => {
          const logKey = row.id;
          const output = row.output.trim();
          const canOpenSession = Boolean(row.sessionId && onSessionClick);
          const rowClassName = `w-full rounded-[12px] px-3 py-3.5 text-left transition-colors hover:bg-[#f7f8fa] dark:hover:bg-surface-muted ${
            canOpenSession ? "cursor-pointer" : ""
          }`;
          const rowContent = (
            <>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    {row.taskName ? (
                      <span
                        className="min-w-0 max-w-[360px] truncate text-left text-sm font-medium text-ink-heading"
                        title={row.taskName}
                      >
                        {row.taskName}
                      </span>
                    ) : (
                      <span className="text-sm font-medium text-ink-heading">
                        {t("automation.execNoTask" as Parameters<typeof t>[0])}
                      </span>
                    )}

                    <span
                      className={`inline-flex shrink-0 items-center justify-start rounded-full px-2 py-0.5 text-[11px] font-medium ${cnStatus(row.status)}`}
                    >
                      <span>{labelForStatus(row.status)}</span>
                    </span>

                    <span className="inline-flex shrink-0 items-center rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-ink-meta">
                      {labelForTrigger(row.triggerType)}
                    </span>
                  </div>

                  {output && output !== "—" ? (
                    <div
                      className="mt-1.5 truncate text-xs font-normal text-ink-body"
                      title={output}
                    >
                      {output}
                    </div>
                  ) : null}
                </div>

                <div className="hidden shrink-0 text-right sm:block">
                  <div className="text-xs font-normal text-ink-body">
                    {row.time}
                    <span className="mx-1.5 text-ink-body">·</span>
                    <span className="text-ink-body">{row.duration}</span>
                  </div>
                </div>
              </div>

              <div className="mt-2 flex items-center gap-2 text-[11px] text-ink-body sm:hidden">
                <span>{row.time}</span>
                <span className="h-1 w-1 rounded-full bg-surface-border" />
                <span>{row.duration}</span>
              </div>
            </>
          );

          if (canOpenSession) {
            return (
              <button
                key={logKey}
                type="button"
                className={rowClassName}
                onClick={() => onSessionClick?.(row.sessionId!)}
              >
                {rowContent}
              </button>
            );
          }

          return (
            <div key={logKey} className={rowClassName}>
              {rowContent}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
};
