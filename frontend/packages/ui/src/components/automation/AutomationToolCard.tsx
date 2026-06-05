/**
 * Card rendered in the conversation stream when the agent calls the
 * ``automation`` MCP tool (ADR-021; supersedes the legacy ``cronjob``
 * card per ADR-009 §Superseded). The structured result returned by the
 * tool is unpacked into a one-line summary plus a "open in automation"
 * link to the affected automation row.
 *
 * Pure presentational — the page parses ``tool.output`` (JSON string)
 * into ``AutomationToolResultPayload`` and passes the parsed object in.
 * Rendering stays focused on the success/failure summary and the
 * affected automation(s) without making any API calls of its own.
 */
import { memo } from "react";
import {
  AlarmClock,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Pause,
  Play,
  Sparkles,
  Trash2,
} from "lucide-react";

import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export type AutomationAction =
  | "create"
  | "list"
  | "update"
  | "pause"
  | "resume"
  | "run"
  | "remove";

/** Discriminated trigger union matching the backend ``Trigger`` schema. */
export type AutomationTrigger =
  | { kind: "cron"; cron_expr: string; timezone: string | null }
  | { kind: "interval"; seconds: number }
  | { kind: "manual" };

export interface AutomationToolItem {
  automation_id: string;
  workspace_id: string;
  workspace_name: string;
  workspace_kind: "chat" | "project";
  name: string;
  agent_kind: string;
  agent_slug: string;
  agent_name: string | null;
  action_kind: "chat" | "task";
  trigger: AutomationTrigger;
  trigger_human_readable: string;
  status: string;
  next_run_at: number | null;
  last_run_at: number | null;
  last_run_status: string | null;
}

export interface AutomationToolResultPayload {
  action: AutomationAction;
  ok: boolean;
  message: string;
  automation?: AutomationToolItem | null;
  automations?: AutomationToolItem[];
  next_runs?: number[];
  error_code?: string | null;
}

interface AutomationToolCardProps {
  result: AutomationToolResultPayload;
  /** Callback the parent wires to navigate into the automation page —
   *  kept abstract so this component stays free of router coupling. */
  onOpenInAutomation?: (automationId: string) => void;
}

const ACTION_LABEL_KEYS: Record<AutomationAction, string> = {
  create: "skill.createdTask",
  list: "cron.taskColumn",
  update: "skill.updated",
  pause: "skill.paused",
  resume: "skill.resumed",
  run: "skill.triggered",
  remove: "skill.deleted",
};

const ACTION_ICON: Record<AutomationAction, typeof AlarmClock> = {
  create: Sparkles,
  list: AlarmClock,
  update: CheckCircle2,
  pause: Pause,
  resume: Play,
  run: Play,
  remove: Trash2,
};

function formatNextRun(value: number | null | undefined): string | null {
  if (value == null) return null;
  try {
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return null;
    // Locale-default formatting from the epoch-ms instant; the browser
    // renders it in the user's local tz.
    return dt.toLocaleString();
  } catch {
    return null;
  }
}

function AutomationLine({
  item,
  onOpen,
}: {
  item: AutomationToolItem;
  onOpen?: (id: string) => void;
}) {
  const { t } = useI18n();
  const tz =
    item.trigger.kind === "cron" && item.trigger.timezone
      ? item.trigger.timezone
      : t("skill.defaultTimezone");
  const nextRun = formatNextRun(item.next_run_at);
  return (
    <button
      type="button"
      onClick={() => onOpen?.(item.automation_id)}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-[12.5px]",
        "transition-colors hover:bg-surface-muted",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium text-[#1f2937]">{item.name}</span>
        <span className="truncate text-[11.5px] text-[#6e7481]">
          {item.trigger_human_readable} · {tz}
          {nextRun
            ? ` ${t("cron.nextRunInline" as Parameters<typeof t>[0], { time: nextRun })}`
            : ""}
        </span>
      </div>
      <ExternalLink
        className="h-3.5 w-3.5 shrink-0 text-[#9ca3af]"
        aria-hidden="true"
      />
    </button>
  );
}

export const AutomationToolCard = memo(function AutomationToolCard({
  result,
  onOpenInAutomation,
}: AutomationToolCardProps) {
  const { t } = useI18n();
  const Icon = ACTION_ICON[result.action] ?? AlarmClock;
  const label =
    t(ACTION_LABEL_KEYS[result.action] as Parameters<typeof t>[0]) ??
    t("project.scheduledTasks");

  if (!result.ok) {
    return (
      <div className="rounded-lg border border-[#fecaca] bg-[#fef2f2] px-3 py-2.5 text-[12.5px]">
        <div className="flex items-center gap-1.5 text-[#b91c1c]">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="font-medium">{t("skill.automationFailed")}</span>
        </div>
        <p className="mt-1 text-[12px] text-[#7f1d1d]">{result.message}</p>
        {result.error_code ? (
          <p className="mt-0.5 font-mono text-[11px] text-[#b91c1c]/80">
            {result.error_code}
          </p>
        ) : null}
      </div>
    );
  }

  const items = result.automations ?? [];
  const showList = result.action === "list" && items.length > 0;
  const singleItem = result.automation ?? null;

  return (
    <div className="rounded-lg border border-surface-border bg-surface px-3 py-2.5 text-[12.5px] shadow-sm">
      <div className="flex items-center gap-1.5 text-[#1f2937]">
        <Icon className="h-3.5 w-3.5 text-[#525860]" aria-hidden="true" />
        <span className="font-medium">{label}</span>
      </div>
      <p className="mt-0.5 text-[12px] text-[#6e7481]">{result.message}</p>

      {singleItem ? (
        <div className="mt-1.5 border-t border-[#f1f3f5] pt-1.5">
          <AutomationLine item={singleItem} onOpen={onOpenInAutomation} />
        </div>
      ) : null}

      {showList ? (
        <div className="mt-1.5 max-h-64 space-y-0.5 overflow-auto border-t border-[#f1f3f5] pt-1.5">
          {items.map((it) => (
            <AutomationLine
              key={it.automation_id}
              item={it}
              onOpen={onOpenInAutomation}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
});

/**
 * Parse the JSON tool output into ``AutomationToolResultPayload`` if
 * possible. Returns ``null`` on malformed input — caller falls back to
 * the generic tool renderer.
 */
export function parseAutomationToolOutput(
  raw: string | undefined | null,
): AutomationToolResultPayload | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (
      typeof obj !== "object" ||
      obj === null ||
      typeof obj.action !== "string" ||
      typeof obj.ok !== "boolean"
    ) {
      return null;
    }
    return obj as AutomationToolResultPayload;
  } catch {
    return null;
  }
}
