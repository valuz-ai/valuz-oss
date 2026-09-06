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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/cn";

export interface ScheduledTaskRow {
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
}

export interface ScheduledTaskSection {
  id: string;
  title: string;
  countLabel?: string;
  /** Rendered after the title (e.g. the execution-origin icon). */
  titleAdornment?: ReactNode;
  tasks: ScheduledTaskRow[];
}

export interface ScheduledTaskTableProps {
  /** Multi-section mode: one sticky column header, a section row per
   *  project, rows underneath (the 自动化 page). Takes precedence over
   *  `tasks` + `title`. */
  sections?: ScheduledTaskSection[];
  tasks?: Array<{
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
  task: ScheduledTaskRow;
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
  sections,
  tasks = [],
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
  const grid = "md:grid-cols-[2.4fr_1.4fr_0.9fr_0.7fr_56px]";
  const resolved: ScheduledTaskSection[] = sections ?? [
    { id: "__single", title: title ?? "", countLabel: taskCountLabel, tasks },
  ];
  const singleCollapsible = !sections && Boolean(title);
  const Chevron = collapsed ? ChevronRight : ChevronDown;

  const sectionHeading = (section: ScheduledTaskSection) =>
    singleCollapsible ? (
      <button
        type="button"
        onClick={onToggleCollapse}
        className="flex h-9 w-full items-center gap-3 px-0 text-left"
        aria-expanded={!collapsed}
      >
        <Chevron className="h-4 w-4 shrink-0 text-ink-meta" />
        <span className="truncate text-sm font-semibold text-ink-heading">
          {section.title}
          {section.countLabel ? (
            <span className="font-medium text-[#6e7481]">
              {" · "}
              {section.countLabel}
            </span>
          ) : null}
        </span>
      </button>
    ) : (
      <div className="mt-4 flex h-8 items-center gap-2 rounded-md bg-surface-soft px-2 text-xs font-semibold text-ink-heading first:mt-0">
        <span className="truncate">{section.title}</span>
        {section.titleAdornment}
        {section.countLabel ? (
          <span className="font-normal text-ink-meta">{section.countLabel}</span>
        ) : null}
      </div>
    );

  const renderRow = (task: ScheduledTaskRow) => (
    <div
      key={task.id}
      className="rounded-md transition-colors hover:bg-surface-soft/60"
    >
      {/* Desktop row */}
      <div className={cn("hidden items-center px-2 py-2 md:grid", grid)}>
        <div className="flex min-w-0 items-center gap-2">
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
              "flex min-w-0 items-center gap-1.5 truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand",
              task.status === "off" && "opacity-50",
            )}
          >
            <span className="truncate">{task.name}</span>
            {task.exec_origin && renderOrigin
              ? renderOrigin(task.exec_origin)
              : null}
          </button>
          {task.prompt ? (
            <span
              className={cn(
                "truncate text-xs text-ink-meta",
                task.status === "off" && "opacity-50",
              )}
            >
              {task.prompt}
            </span>
          ) : null}
        </div>
        <div className="min-w-0 truncate text-xs text-ink-body">
          {task.trigger}
          {task.triggerTimezone ? (
            <span className="ml-1.5 text-ink-meta">· {task.triggerTimezone}</span>
          ) : null}
        </div>
        <div className="text-xs text-ink-body">{task.last}</div>
        <div className="flex">{statusBadge(task.status)}</div>
        <div className="flex justify-end">
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
      <div className="px-0 py-3 md:hidden">
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
          <span className="text-xs text-ink-body">
            {task.trigger}
            {task.triggerTimezone && (
              <span className="ml-1.5 text-ink-meta">· {task.triggerTimezone}</span>
            )}
            <span className="ml-1.5 text-ink-meta">· {task.last}</span>
          </span>
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
  );

  const hasRows = resolved.some((section) => section.tasks.length > 0);
  return (
    <section>
      {singleCollapsible ? sectionHeading(resolved[0]!) : null}
      {singleCollapsible && collapsed ? null : (
        <>
          {hasRows ? (
            <div
              className={cn(
                "sticky top-0 z-10 hidden border-b border-surface-border bg-card px-2 py-2 text-xs font-medium text-[#6E7481] md:grid dark:text-ink-body",
                grid,
              )}
            >
              <div>{t("cron.taskColumn")}</div>
              <div>{t("cron.scheduleColumn")}</div>
              <div>{t("cron.lastRunColumn")}</div>
              <div>{t("cron.statusColumn")}</div>
              <div className="text-right">{t("cron.actionColumn")}</div>
            </div>
          ) : null}
          {resolved.map((section) => (
            <div key={section.id} className={sections ? "first:mt-0" : undefined}>
              {sections && section.title ? sectionHeading(section) : null}
              {section.tasks.map(renderRow)}
            </div>
          ))}
        </>
      )}
    </section>
  );
};
