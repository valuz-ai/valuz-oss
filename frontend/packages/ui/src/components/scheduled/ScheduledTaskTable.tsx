import {
  ChevronDown,
  ChevronRight,
  Clock,
  MoreHorizontal,
  Play,
  Power,
  PowerOff,
  Trash2,
} from "lucide-react";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { CardContent } from "../ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { StatusDot } from "./StatusDot";
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
}

const ScheduledTaskActionMenu = ({
  task,
  onToggle,
  onDelete,
  onRunNow,
}: {
  task: ScheduledTaskTableProps["tasks"][number];
  onToggle?: (id: string) => void;
  onDelete?: (id: string) => void;
  onRunNow?: (id: string) => void;
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
      <DropdownMenuContent align="end" className="min-w-[128px]">
        <DropdownMenuItem
          disabled={!canTest}
          onSelect={() => {
            if (canTest) onRunNow?.(task.id);
          }}
          className="focus:bg-surface-2 focus:text-ink-heading"
        >
          <Play className="h-3.5 w-3.5" />
          运行测试
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => onToggle?.(task.id)}
          className="focus:bg-surface-2 focus:text-ink-heading"
        >
          {task.status === "on" ? (
            <PowerOff className="h-3.5 w-3.5" />
          ) : (
            <Power className="h-3.5 w-3.5" />
          )}
          {task.status === "on" ? t("cron.pause") : "执行"}
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => onDelete?.(task.id)}
          className="text-[#f54b4b] focus:bg-[#f54b4b]/10 focus:text-[#f54b4b] [&_svg]:text-[#f54b4b]"
        >
          <Trash2 className="h-3.5 w-3.5 text-[#f54b4b]" />
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
}: ScheduledTaskTableProps) => {
  const { t } = useI18n();
  const Chevron = collapsed ? ChevronRight : ChevronDown;

  const statusLabel = (status: "on" | "off") =>
    status === "on" ? t("cron.enable") : t("cron.paused");

  return (
    <Card className="gap-0 overflow-hidden py-0">
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
        <div className="hidden border-b border-[#f7f8fa] px-5 py-2 text-[12px] font-medium text-[#6E7481] md:grid md:grid-cols-[2fr_1.1fr_1.1fr_0.8fr_0.7fr_72px] dark:border-surface-border dark:text-ink-body">
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
                      "block truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand",
                      task.status === "off" && "opacity-50",
                    )}
                  >
                    {task.name}
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
                <span
                  className={
                    task.status === "on"
                      ? "inline-flex items-center gap-1.5 rounded-full bg-[#53cb76]/10 px-2 py-0.5 text-[11px] font-medium text-[#53cb76]"
                      : "inline-flex items-center gap-1.5 rounded-full bg-surface-soft px-2 py-0.5 text-[11px] font-medium text-ink-meta"
                  }
                >
                  <StatusDot
                    status={task.status}
                    className={task.status === "on" ? "bg-[#53cb76]" : ""}
                  />
                  {statusLabel(task.status)}
                </span>
              </div>
              <div className="flex justify-center">
                <ScheduledTaskActionMenu
                  task={task}
                  onToggle={onToggle}
                  onRunNow={onRunNow}
                  onDelete={onDelete}
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
                      "block truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand",
                      task.status === "off" && "opacity-50",
                    )}
                  >
                    {task.name}
                  </button>
                </div>
                <span
                  className={
                    task.status === "on"
                      ? "inline-flex items-center gap-1.5 rounded-full bg-[#53cb76]/10 px-2 py-0.5 text-[11px] font-medium text-[#53cb76]"
                      : "inline-flex items-center gap-1.5 rounded-full bg-surface-soft px-2 py-0.5 text-[11px] font-medium text-ink-meta"
                  }
                >
                  <StatusDot
                    status={task.status}
                    className={task.status === "on" ? "bg-[#53cb76]" : ""}
                  />
                  {statusLabel(task.status)}
                </span>
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
