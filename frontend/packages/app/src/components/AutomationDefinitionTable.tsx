import { useTranslation, type AutomationItem } from "@valuz/core";
import { ScheduledTaskTable, type ScheduledTaskRow } from "@valuz/ui";

import { describeTrigger } from "./describe-trigger";
import { OriginIcon } from "./ExecutionLocationPicker";

export interface AutomationDefinitionGroup {
  id: string;
  name: string;
  countLabel?: string;
  /** Execution origin of the group's project (local / cloud), shown next
   *  to the section title when the list fans out across backends. */
  origin?: string;
  automations: AutomationItem[];
}

export interface AutomationDefinitionTableProps {
  /** Single-group mode (workspace surfaces). */
  automations?: AutomationItem[];
  /** Multi-group mode: one table, a section row per project (自动化 page). */
  groups?: AutomationDefinitionGroup[];
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

/**
 * The canonical Automation list presentation shared by the global library and
 * Project/workspace surfaces. Keeping the row mapping here prevents status
 * copy and columns from drifting between entry points.
 */
export function AutomationDefinitionTable({
  automations = [],
  groups,
  onToggle,
  onDelete,
  onOpen,
  onRunNow,
  title,
  countLabel,
  collapsed,
  onToggleCollapse,
}: AutomationDefinitionTableProps) {
  const { t } = useTranslation();
  const translate = (key: string, params?: Record<string, string | number>) =>
    t(key as Parameters<typeof t>[0], params);
  const toRows = (items: AutomationItem[]): ScheduledTaskRow[] =>
    [...items]
      .sort(
        (a, b) =>
          Number(b.status === "enabled") - Number(a.status === "enabled"),
      )
      .map((item) => ({
        id: item.automation_id,
        name: item.name,
        prompt: item.agent_name ?? "",
        trigger: describeTrigger(item, translate),
        triggerTimezone:
          item.trigger.kind === "cron"
            ? (item.trigger.timezone ?? undefined)
            : undefined,
        last: relativeTime(item.last_run_at),
        status: (item.status === "enabled" ? "on" : "off") as "on" | "off",
        exec_origin: item.exec_origin,
      }));

  const shared = {
    onRowClick: onOpen,
    onToggle,
    onRunNow,
    onDelete,
    renderOrigin: (origin: string) => <OriginIcon origin={origin} />,
  };

  if (groups) {
    return (
      <ScheduledTaskTable
        sections={groups.map((group) => ({
          id: group.id,
          title: group.name,
          countLabel: group.countLabel,
          titleAdornment: group.origin ? (
            <OriginIcon origin={group.origin} />
          ) : null,
          tasks: toRows(group.automations),
        }))}
        {...shared}
      />
    );
  }
  return (
    <ScheduledTaskTable
      tasks={toRows(automations)}
      title={title}
      taskCountLabel={countLabel}
      collapsed={collapsed}
      onToggleCollapse={onToggleCollapse}
      {...shared}
    />
  );
}
