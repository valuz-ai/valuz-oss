import { useI18n } from "../../hooks/use-i18n";
import { Badge } from "../ui/badge";
import { Card, CardContent } from "../ui/card";

export type ExecutionLogTriggerType =
  | "cron"
  | "interval"
  | "manual"
  | "agent"
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
  /** Title of the Task this run spawned (task-action automations only).
   *  Rendered as a "→ 任务《title》" line so the user sees which task the
   *  automation produced. ``null`` for chat-action / non-task runs. */
  taskTitle?: string | null;
}

export interface ExecutionLogProps {
  rows: ExecutionLogRow[];
  /** Click handler for the per-row task-name link. Receives the
   *  ``sessionId`` from the row. Wire to ``navigate(`/conversation/${id}`)``
   *  at the call site. Omit to render the task name as plain text. */
  onSessionClick?: (sessionId: string) => void;
}

function statusVariant(status: string): "success" | "error" | "brand" | "outline" {
  if (status === "ok") return "success";
  if (status === "err") return "error";
  if (status === "pending") return "brand";
  return "outline";
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
    if (type === "agent")
      return t("automation.execTriggerAgent" as Parameters<typeof t>[0]);
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
          const rowClassName = `w-full rounded-2xl px-3 py-3.5 text-left transition-colors hover:bg-[#f7f8fa] dark:hover:bg-surface-muted ${
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

                    <Badge variant={statusVariant(row.status)} className="shrink-0">
                      {labelForStatus(row.status)}
                    </Badge>

                    <Badge variant="outline" className="shrink-0">
                      {labelForTrigger(row.triggerType)}
                    </Badge>
                  </div>

                  {row.taskTitle ? (
                    <div
                      className="mt-1 truncate text-2xs text-ink-meta"
                      title={row.taskTitle}
                    >
                      {"→ "}
                      {t("automation.spawnedTask" as Parameters<typeof t>[0], {
                        title: row.taskTitle,
                      })}
                    </div>
                  ) : null}

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

              <div className="mt-2 flex items-center gap-2 text-2xs text-ink-body sm:hidden">
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
