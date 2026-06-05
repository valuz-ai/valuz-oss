import { useEffect, useState } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { Input } from "../ui/input";
import { SegmentedControl } from "../ui/segmented-control";
import { useI18n } from "../../hooks/use-i18n";

export interface CronInputProps {
  value: string;
  onChange: (cron: string) => void;
}

type Frequency = "daily" | "weekdays" | "weekly" | "monthly";

interface SimpleParts {
  frequency: Frequency;
  hour: number;
  minute: number;
  weekday: number;
  monthDay: number;
}

const FREQUENCIES = ["daily", "weekdays", "weekly", "monthly"] as const;

const FREQUENCY_KEYS: Record<string, string> = {
  daily: "cron.everyDay",
  weekdays: "cron.weekdays",
  weekly: "cron.everyWeek",
  monthly: "cron.everyMonth",
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);
// Sub-minute granularity is irrelevant for a 30s-tick personal scheduler.
// Keep the simple-mode list at quarter-hour anchors so it stays compact;
// odd minutes (e.g. ``5 9 * * *``) fall through to the advanced text
// input via the parse step below.
const MINUTES = [0, 15, 30, 45];

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7];

const WEEKDAY_KEYS: Record<number, string> = {
  1: "cron.mon",
  2: "cron.tue",
  3: "cron.wed",
  4: "cron.thu",
  5: "cron.fri",
  6: "cron.sat",
  7: "cron.sun",
};

const MONTH_DAYS = Array.from({ length: 31 }, (_, i) => i + 1);

const DEFAULT_PARTS: SimpleParts = {
  frequency: "daily",
  hour: 9,
  minute: 0,
  weekday: 1,
  monthDay: 1,
};

/**
 * Parse a 5-field cron expression back into simple-mode parts.
 *
 * Returns ``null`` when the expression uses anything the simple form
 * can't represent (ranges, lists, step expressions like ``*\/15``,
 * second granularity). The caller then falls back to advanced mode so
 * the user sees the actual expression rather than a misleading
 * default-9am view.
 *
 * Recognised shapes (all variants of "single fixed minute + single
 * fixed hour"):
 *   M H * * *      → daily
 *   M H * * 1-5    → weekdays
 *   M H * * <wd>   → weekly (1=Mon..7=Sun; ``0`` also accepted for Sun
 *                    since croniter accepts both)
 *   M H <dom> * *  → monthly
 */
function cronToSimpleParts(cron: string): SimpleParts | null {
  if (!cron || typeof cron !== "string") return null;
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minuteStr, hourStr, dom, month, dow] = parts;
  if (month !== "*") return null; // we only support every-month patterns
  const minute = Number(minuteStr);
  const hour = Number(hourStr);
  if (!Number.isInteger(minute) || minute < 0 || minute > 59) return null;
  if (!Number.isInteger(hour) || hour < 0 || hour > 23) return null;

  // daily / weekly / weekdays — day-of-month must be wildcard
  if (dom === "*") {
    if (dow === "*") {
      return { ...DEFAULT_PARTS, frequency: "daily", hour, minute };
    }
    if (dow === "1-5") {
      return { ...DEFAULT_PARTS, frequency: "weekdays", hour, minute };
    }
    const wd = Number(dow);
    if (!Number.isInteger(wd) || wd < 0 || wd > 7) return null;
    // Normalise Sun: cron commonly accepts both 0 and 7. WEEKDAYS uses 7.
    const normalised = wd === 0 ? 7 : wd;
    return {
      ...DEFAULT_PARTS,
      frequency: "weekly",
      hour,
      minute,
      weekday: normalised,
    };
  }

  // monthly — day-of-month fixed, day-of-week must be wildcard.
  if (dow !== "*") return null;
  const md = Number(dom);
  if (!Number.isInteger(md) || md < 1 || md > 31) return null;
  return {
    ...DEFAULT_PARTS,
    frequency: "monthly",
    hour,
    minute,
    monthDay: md,
  };
}

function cronToHumanReadable(
  cron: string,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  const parts = cron.split(" ");
  if (parts.length !== 5) return cron;

  const [minute, hour, dayOfMonth, , weekday] = parts;

  if (weekday === "1-5")
    return `${t("cron.weekdays")} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  if (weekday !== "*") {
    const wdLabel = WEEKDAY_KEYS[Number(weekday)]
      ? t(WEEKDAY_KEYS[Number(weekday)])
      : t("cron.everyWeek");
    return `${wdLabel} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  }
  if (dayOfMonth !== "*")
    return `${t("cron.everyMonth")}${t("cron.dayOrdinal", { d: dayOfMonth })} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;

  return `${t("cron.everyDay")} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
}

function frequencyToCron(
  freq: Frequency,
  hour: number,
  minute: number,
  weekday: number,
  monthDay: number,
): string {
  switch (freq) {
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekdays":
      return `${minute} ${hour} * * 1-5`;
    case "weekly":
      return `${minute} ${hour} * * ${weekday}`;
    case "monthly":
      return `${minute} ${hour} ${monthDay} * *`;
    default:
      return `${minute} ${hour} * * *`;
  }
}

export const CronInput = ({ value, onChange }: CronInputProps) => {
  const { t } = useI18n();
  // Default to simple mode but switch to advanced when an externally
  // supplied ``value`` doesn't fit any simple-mode pattern. The
  // ``mode`` state is also re-synced from ``value`` in the effect
  // below so reopening an edit dialog reflects what's actually saved.
  const [mode, setMode] = useState<"simple" | "advanced">(() =>
    cronToSimpleParts(value) === null && value ? "advanced" : "simple",
  );
  const [frequency, setFrequency] = useState<Frequency>(
    DEFAULT_PARTS.frequency,
  );
  const [hour, setHour] = useState(DEFAULT_PARTS.hour);
  const [minute, setMinute] = useState(DEFAULT_PARTS.minute);
  const [weekday, setWeekday] = useState(DEFAULT_PARTS.weekday);
  const [monthDay, setMonthDay] = useState(DEFAULT_PARTS.monthDay);

  // Sync simple-mode dropdown state from ``value`` whenever the parent
  // changes it. Without this the dropdowns always show 9:00 every day
  // regardless of the task's actual cron — the canonical bug report:
  // "编辑定时任务的时候没有获取到之前配置的内容". Re-parsing on every
  // ``value`` change also covers re-opening the dialog for a different
  // task (the component instance is reused).
  //
  // Why effect-driven instead of derived state: the user is allowed to
  // freely edit minute = 30 → 45 → … inside simple mode without us
  // overwriting their choice with a stale prop. So we only re-sync when
  // ``value`` actually differs from what the dropdowns currently encode
  // — i.e. an external write (open / reset).
  useEffect(() => {
    const parsed = cronToSimpleParts(value);
    if (parsed === null) {
      // Unrepresentable: switch to advanced so the user can see the
      // real expression. Don't bother re-seeding simple-mode state —
      // they'll come back to it via the tab toggle if needed.
      setMode("advanced");
      return;
    }
    const current = frequencyToCron(frequency, hour, minute, weekday, monthDay);
    if (current === value) return; // already in sync; ignore self-emitted writes
    setFrequency(parsed.frequency);
    setHour(parsed.hour);
    setMinute(parsed.minute);
    setWeekday(parsed.weekday);
    setMonthDay(parsed.monthDay);
    setMode("simple");
    // ``value`` is the controlled input. Resyncing local state above
    // doesn't fire ``onChange`` — that's intentional: an externally
    // driven seed shouldn't echo back as a write.
    //
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const emit = (
    freq: Frequency,
    h: number,
    m: number,
    wd: number,
    md: number,
  ) => {
    setFrequency(freq);
    setHour(h);
    setMinute(m);
    setWeekday(wd);
    setMonthDay(md);
    onChange(frequencyToCron(freq, h, m, wd, md));
  };

  return (
    <div className="space-y-3">
      <SegmentedControl
        value={mode}
        onValueChange={setMode}
        className="h-8"
        options={[
          { value: "simple", label: t("cron.simple") },
          { value: "advanced", label: t("cron.advanced") },
        ]}
      />

      {mode === "simple" ? (
        <div className="flex flex-wrap gap-2">
          {/* Frequency */}
          <div className="min-w-[100px] flex-1">
            <label className="mb-1 block text-xs font-medium text-ink-heading">
              {t("cron.frequency")}
            </label>
            <Select
              value={frequency}
              onValueChange={(v) =>
                emit(v as Frequency, hour, minute, weekday, monthDay)
              }
            >
              <SelectTrigger className="w-full text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FREQUENCIES.map((f) => (
                  <SelectItem key={f} value={f}>
                    {t(FREQUENCY_KEYS[f])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Weekday — only when weekly */}
          {frequency === "weekly" && (
            <div>
              <label className="mb-1 block text-xs text-ink-meta">
                {t("cron.dayOfWeek")}
              </label>
              <Select
                value={String(weekday)}
                onValueChange={(v) =>
                  emit(frequency, hour, minute, Number(v), monthDay)
                }
              >
                <SelectTrigger className="w-[80px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WEEKDAYS.map((wd) => (
                    <SelectItem key={wd} value={String(wd)}>
                      {t(WEEKDAY_KEYS[wd])}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Month day — only when monthly */}
          {frequency === "monthly" && (
            <div>
              <label className="mb-1 block text-xs text-ink-meta">
                {t("cron.dayOfMonth")}
              </label>
              <Select
                value={String(monthDay)}
                onValueChange={(v) =>
                  emit(frequency, hour, minute, weekday, Number(v))
                }
              >
                <SelectTrigger className="w-[80px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MONTH_DAYS.map((d) => (
                    <SelectItem key={d} value={String(d)}>
                      {t("cron.dayOrdinal", { d })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Hour */}
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-heading">
              {t("cron.hour")}
            </label>
            <Select
              value={String(hour)}
              onValueChange={(v) =>
                emit(frequency, Number(v), minute, weekday, monthDay)
              }
            >
              <SelectTrigger className="w-[72px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HOURS.map((h) => (
                  <SelectItem key={h} value={String(h)}>
                    {String(h).padStart(2, "0")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Minute */}
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-heading">
              {t("cron.minute")}
            </label>
            <Select
              value={String(minute)}
              onValueChange={(v) =>
                emit(frequency, hour, Number(v), weekday, monthDay)
              }
            >
              <SelectTrigger className="w-[72px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {/* The dropdown lists quarter-hour anchors, but an
                    incoming task may have any minute (e.g. 5). Show
                    that as the current value so it's not silently
                    rounded — picking another option will land on a
                    quarter, which is expected for the simple mode. */}
                {(MINUTES.includes(minute) ? MINUTES : [...MINUTES, minute])
                  .sort((a, b) => a - b)
                  .map((m) => (
                    <SelectItem key={m} value={String(m)}>
                      {String(m).padStart(2, "0")}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      ) : (
        <div>
          <label className="mb-1 block text-xs font-medium text-ink-heading">
            {t("cron.cronExpression")}
          </label>
          <Input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="0 9 * * 1-5"
            className="font-mono text-xs text-ink-label md:text-xs"
          />
          <p className="mt-1.5 text-2xs text-ink-meta">
            {value
              ? `→ ${cronToHumanReadable(value, t)}`
              : t("cron.cronFormat")}
          </p>
        </div>
      )}
    </div>
  );
};

// Exposed for unit tests — pure transform, no React or DOM coupling.
export { cronToSimpleParts };
