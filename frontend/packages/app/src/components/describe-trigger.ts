import type { AutomationItem } from "@valuz/core";

type Translate = (
  key: string,
  params?: Record<string, string | number>,
) => string;

const pad = (value: number) => String(value).padStart(2, "0");

/**
 * Human wording for the common cron shapes (daily / weekdays / weekly /
 * monthly / hourly / every N minutes or hours); anything else falls back to
 * the raw expression. Interval and manual triggers already arrive with a
 * server-side sentence (`trigger_human_readable`).
 */
export function describeCron(expr: string, t: Translate): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;
  const [minute, hour, dom, month, dow] = parts as [
    string,
    string,
    string,
    string,
    string,
  ];
  const isNum = (value: string) => /^\d+$/.test(value);
  if (month !== "*") return expr;
  if (isNum(minute) && isNum(hour)) {
    const time = `${pad(Number(hour))}:${pad(Number(minute))}`;
    if (dom === "*" && dow === "*") return t("cron.human.daily", { time });
    if (dom === "*" && dow === "1-5") return t("cron.human.weekdays", { time });
    if (dom === "*" && /^[0-6]$/.test(dow)) {
      return t("cron.human.weekly", {
        day: t(`cron.human.weekday.${dow}`),
        time,
      });
    }
    if (isNum(dom) && dow === "*")
      return t("cron.human.monthly", { day: Number(dom), time });
    return expr;
  }
  if (isNum(minute) && hour === "*" && dom === "*" && dow === "*") {
    return t("cron.human.hourly", { minute: pad(Number(minute)) });
  }
  const everyMin = /^\*\/(\d+)$/.exec(minute);
  if (everyMin && hour === "*" && dom === "*" && dow === "*") {
    return t("cron.human.everyMinutes", { n: Number(everyMin[1]) });
  }
  const everyHour = /^\*\/(\d+)$/.exec(hour);
  if (isNum(minute) && everyHour && dom === "*" && dow === "*") {
    return t("cron.human.everyHours", { n: Number(everyHour[1]) });
  }
  return expr;
}

export function describeTrigger(item: AutomationItem, t: Translate): string {
  if (item.trigger.kind === "cron")
    return describeCron(item.trigger.cron_expr, t);
  return item.trigger_human_readable;
}

// ── Run status / trigger-type labels (AutomationRunItem) ──────────────
//
// Shared between the run list (AutomationDetailPage) and the run detail
// panel so both surfaces read the same labels for every status/trigger
// combination the backend can send, including the newer ``timeout`` /
// ``cancelled`` statuses and ``api`` / ``event`` trigger types.

/** ``StatusPill``'s ``status`` prop + the i18n key for its label, keyed by
 *  the raw run/task status string. Unknown values fall back to the
 *  "failed" pill style with the raw string as the label — this must never
 *  throw, since the backend can add new terminal statuses over time. */
const RUN_STATUS_MAP: Record<string, { pillStatus: string; labelKey: string }> =
  {
    completed: { pillStatus: "completed", labelKey: "automation.execStatusOk" },
    success: { pillStatus: "completed", labelKey: "automation.execStatusOk" },
    failed: { pillStatus: "failed", labelKey: "automation.execStatusErr" },
    timeout: { pillStatus: "failed", labelKey: "automation.execStatusTimeout" },
    active: { pillStatus: "running", labelKey: "cron.running" },
    running: { pillStatus: "running", labelKey: "cron.running" },
    queued: { pillStatus: "running", labelKey: "automation.execStatusPending" },
    paused: { pillStatus: "paused", labelKey: "cron.paused" },
    cancelled: {
      pillStatus: "cancelled",
      labelKey: "automation.execStatusCancelled",
    },
    skipped: { pillStatus: "skipped", labelKey: "automation.execStatusSkip" },
    interrupted_by_shutdown: {
      pillStatus: "skipped",
      labelKey: "automation.execStatusSkip",
    },
  };

export function describeRunStatus(
  status: string,
  t: Translate,
): { pillStatus: string; label: string } {
  const mapped = RUN_STATUS_MAP[status];
  if (!mapped) return { pillStatus: "failed", label: status };
  return { pillStatus: mapped.pillStatus, label: t(mapped.labelKey) };
}

const TRIGGER_TYPE_LABEL_KEY: Record<string, string> = {
  cron: "automation.execTriggerCron",
  interval: "automation.execTriggerInterval",
  manual: "automation.execTriggerManual",
  agent: "automation.execTriggerAgent",
  api: "automation.execTriggerApi",
  event: "automation.execTriggerEvent",
  recovered_skip: "automation.execTriggerRecoveredSkip",
  system: "automation.execTriggerSystem",
};

export function describeTriggerType(type: string, t: Translate): string {
  const key = TRIGGER_TYPE_LABEL_KEY[type];
  return key ? t(key) : type;
}
