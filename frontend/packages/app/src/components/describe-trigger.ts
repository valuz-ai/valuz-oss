import type { AutomationItem } from "@valuz/core";

type Translate = (key: string, params?: Record<string, string | number>) => string;

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
  const [minute, hour, dom, month, dow] = parts as [string, string, string, string, string];
  const isNum = (value: string) => /^\d+$/.test(value);
  if (month !== "*") return expr;
  if (isNum(minute) && isNum(hour)) {
    const time = `${pad(Number(hour))}:${pad(Number(minute))}`;
    if (dom === "*" && dow === "*") return t("cron.human.daily", { time });
    if (dom === "*" && dow === "1-5") return t("cron.human.weekdays", { time });
    if (dom === "*" && /^[0-6]$/.test(dow)) {
      return t("cron.human.weekly", { day: t(`cron.human.weekday.${dow}`), time });
    }
    if (isNum(dom) && dow === "*") return t("cron.human.monthly", { day: Number(dom), time });
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
  if (item.trigger.kind === "cron") return describeCron(item.trigger.cron_expr, t);
  return item.trigger_human_readable;
}
