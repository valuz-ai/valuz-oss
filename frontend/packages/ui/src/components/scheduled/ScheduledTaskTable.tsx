import type { ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock,
  FilePenLine,
  MoreHorizontal,
  Pause,
  Play,
  Power,
  Trash2,
} from "lucide-react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { CardContent } from "../ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/cn";

export interface ScheduledTaskTableProps {
  tasks: Array<{
    id: string;
    name: string;
    prompt: string;
    trigger: string;
    triggerTimezone?: string;
    last: string;
    status: "on" | "off";
    /** CLIENT-side execution-origin tag ("local"/"cloud") from list fan-out;
     *  undefined on single-backend builds. */
    exec_origin?: string;
  }>;
  onToggle?: (id: string) => void;
  onDelete?: (id: string) => void;
  onRowClick?: (id: string) => void;
  onRunNow?: (id: string) => void;
  title?: string;
  taskCountLabel?: string;
  lastRunLabel?: string;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  /** Renders the origin indicator next to each row's name when the row carries
   *  an ``exec_origin``. App supplies this so the icon component (which needs
   *  the targets registry) stays in app; the table stays package-agnostic. */
  renderOrigin?: (origin: string) => ReactNode;
}

const ScheduledTaskActionMenu = ({
  task,
  onToggle,
  onDelete,
  onRunNow,
  onRowClick,
}: {
  task: ScheduledTaskTableProps["tasks"][number];
  onToggle?: (id: string) => void;
  onDelete?: (id: string) => void;
  onRunNow?: (id: string) => void;
  onRowClick?: (id: string) => void;
}) => {
  const { t } = useI18n();
  const canTest = Boolean(onRunNow && task.status === "on");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="h-8 w-8 hover:bg-[#f3f4f6] hover:text-inherit dark:hover:bg-surface-muted"
          aria-label={t("cron.actionColumn")}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[140px]">
        {onRowClick && (
          <DropdownMenuItem onSelect={() => onRowClick(task.id)}>
            <FilePenLine />
            {t("common.edit")}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={() => onToggle?.(task.id)}>
          {task.status === "on" ? <Pause /> : <Power />}
          {task.status === "on" ? t("cron.pause") : t("cron.enable")}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!canTest}
          onSelect={() => {
            if (canTest) onRunNow?.(task.id);
          }}
        >
          <Play />
          {t("cron.runNow")}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          onSelect={() => onDelete?.(task.id)}
        >
          <Trash2 />
          {t("common.delete")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export const ScheduledTaskTable = ({
  tasks,
  onToggle,
  onDelete,
  onRowClick,
  onRunNow,
  title,
  taskCountLabel,
  collapsed = false,
  onToggleCollapse,
  renderOrigin,
}: ScheduledTaskTableProps) => {
  const { t } = useI18n();
  const Chevron = collapsed ? ChevronRight : ChevronDown;

  const statusLabel = (status: "on" | "off") =>
    status === "on" ? t("cron.enable") : t("cron.paused");
  const statusBadge = (status: "on" | "off") => (
    <Badge
      variant={status === "on" ? "success" : "warning"}
      className="gap-1.5"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {statusLabel(status)}
    </Badge>
  );

  return (
    <Card className="gap-0 overflow-hidden border-0 py-0 shadow-[var(--shadow-1)]">
      <CardContent className="px-0 py-0">
        {title && (
          <button
            type="button"
            onClick={onToggleCollapse}
            className="flex h-10 w-full items-center justify-between gap-4 px-5 text-left"
            aria-expanded={!collapsed}
          >
            <div className="flex min-w-0 items-center gap-3">
              <Chevron className="h-4 w-4 shrink-0 text-ink-meta" />
              <span className="truncate text-sm font-semibold text-ink-heading">
                {title}
                {taskCountLabel ? (
                  <span className="font-medium text-[#6e7481]">
                    {" · "}
                    {taskCountLabel}
                  </span>
                ) : null}
              </span>
            </div>
          </button>
        )}

        {collapsed ? null : (
          <>
            {/* Header row — hidden on mobile */}
            <div className="hidden border-b border-[#f7f8fa] px-5 py-2 text-xs font-medium text-[#6E7481] md:grid md:grid-cols-[2fr_1.1fr_1.1fr_0.8fr_0.7fr_72px] dark:border-surface-border dark:text-ink-body">
              <div>{t("cron.taskColumn")}</div>
              <div className="text-center">{t("cron.triggerColumn")}</div>
              <div className="text-center">{t("cron.timezoneColumn")}</div>
              <div className="text-center">{t("cron.lastRunColumn")}</div>
              <div className="text-center">{t("cron.statusColumn")}</div>
              <div className="text-center">{t("cron.actionColumn")}</div>
            </div>

            {tasks.map((task) => (
              <div key={task.id}>
                {/* Desktop row */}
                <div className="hidden items-center px-5 py-4 md:grid md:grid-cols-[2fr_1.1fr_1.1fr_0.8fr_0.7fr_72px]">
                  <div className="flex min-w-0 items-start gap-2">
                    <Clock
                      className={cn(
                        "mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta",
                        task.status === "off" && "opacity-50",
                      )}
                    />
                    <div className="min-w-0">
                      <button
                        type="button"
                        onClick={() => onRowClick?.(task.id)}
                        className={cn(
                          "flex items-center gap-1 truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand",
                          task.status === "off" && "opacity-50",
                        )}
                      >
                        <span className="truncate">{task.name}</span>
                        {task.exec_origin && renderOrigin
                          ? renderOrigin(task.exec_origin)
                          : null}
                      </button>
                      <div
                        className={cn(
                          "mt-1 truncate text-xs text-ink-body",
                          task.status === "off" && "opacity-50",
                        )}
                      >
                        {task.prompt}
                      </div>
                    </div>
                  </div>
                  <div className="text-center font-mono text-xs text-ink-label">
                    {task.trigger}
                  </div>
                  <div className="truncate text-center font-mono text-xs text-ink-meta">
                    {task.triggerTimezone || "—"}
                  </div>
                  <div className="text-center text-xs text-ink-body">
                    {task.last}
                  </div>
                  <div className="flex justify-center">
                    {statusBadge(task.status)}
                  </div>
                  <div className="flex justify-center">
                    <ScheduledTaskActionMenu
                      task={task}
                      onToggle={onToggle}
                      onRunNow={onRunNow}
                      onDelete={onDelete}
                      onRowClick={onRowClick}
                    />
                  </div>
                </div>

                {/* Mobile card */}
                <div className="px-5 py-4 md:hidden">
                  <div className="flex items-center justify-between">
                    <div className="flex min-w-0 items-start gap-2">
                      <Clock
                        className={cn(
                          "h-3.5 w-3.5 shrink-0 text-ink-meta",
                          task.status === "off" && "opacity-50",
                        )}
                      />
                      <button
                        type="button"
                        onClick={() => onRowClick?.(task.id)}
                        className={cn(
                          "flex items-center gap-1 truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand",
                          task.status === "off" && "opacity-50",
                        )}
                      >
                        <span className="truncate">{task.name}</span>
                        {task.exec_origin && renderOrigin
                          ? renderOrigin(task.exec_origin)
                          : null}
                      </button>
                    </div>
                    {statusBadge(task.status)}
                  </div>
                  <div
                    className={cn(
                      "mt-1 ml-[22px] truncate text-xs text-ink-body",
                      task.status === "off" && "opacity-50",
                    )}
                  >
                    {task.prompt}
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="font-mono text-xs text-ink-label">
                      {task.trigger}
                      {task.triggerTimezone && (
                        <span className="ml-1.5 text-ink-meta">
                          · {task.triggerTimezone}
                        </span>
                      )}
                    </span>
                    <div className="flex justify-center">
                      <ScheduledTaskActionMenu
                        task={task}
                        onToggle={onToggle}
                        onRunNow={onRunNow}
                        onDelete={onDelete}
                        onRowClick={onRowClick}
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </CardContent>
    </Card>
  );
};
