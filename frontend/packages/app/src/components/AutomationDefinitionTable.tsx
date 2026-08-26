import type { AutomationItem } from "@valuz/core";
import { ScheduledTaskTable } from "@valuz/ui";

import { OriginIcon } from "./ExecutionLocationPicker";

export interface AutomationDefinitionTableProps {
  automations: AutomationItem[];
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onOpen: (id: string) => void;
  onRunNow: (id: string) => void;
  title?: string;
  countLabel?: string;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

function relativeTime(ms: number | null): string {
  if (ms == null) return "—";
  const diff = Date.now() - ms;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function triggerColumn(item: AutomationItem): string {
  if (item.trigger.kind === "cron") return item.trigger.cron_expr;
  return item.trigger_human_readable;
}

/**
 * The canonical Automation list presentation shared by the global library and
 * Project/workspace surfaces. Keeping the row mapping here prevents status
 * copy and columns from drifting between entry points.
 */
export function AutomationDefinitionTable({
  automations,
  onToggle,
  onDelete,
  onOpen,
  onRunNow,
  title,
  countLabel,
  collapsed,
  onToggleCollapse,
}: AutomationDefinitionTableProps) {
  const tasks = [...automations]
    .sort(
      (a, b) =>
        Number(b.status === "enabled") - Number(a.status === "enabled"),
    )
    .map((item) => ({
      id: item.automation_id,
      name: item.name,
      prompt: item.agent_name ?? "",
      trigger: triggerColumn(item),
      triggerTimezone:
        item.trigger.kind === "cron"
          ? (item.trigger.timezone ?? undefined)
          : undefined,
      last: relativeTime(item.last_run_at),
      status: (item.status === "enabled" ? "on" : "off") as "on" | "off",
      exec_origin: item.exec_origin,
    }));

  return (
    <ScheduledTaskTable
      tasks={tasks}
      title={title}
      taskCountLabel={countLabel}
      collapsed={collapsed}
      onToggleCollapse={onToggleCollapse}
      onRowClick={onOpen}
      onToggle={onToggle}
      onRunNow={onRunNow}
      onDelete={onDelete}
      renderOrigin={(origin) => <OriginIcon origin={origin} />}
    />
  );
}
