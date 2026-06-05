/**
 * CreateAutomationDialog — replaces CreateScheduledTaskDialog per ADR-021.
 *
 * Three fundamental UX changes from the legacy dialog:
 *
 * 1. **Agent picker** instead of model + runtime pickers. Execution
 *    identity follows the bound agent — the dialog just shows the
 *    candidates (project members or library agents) and the agent's
 *    configured model/runtime travels with it at fire time.
 * 2. **Trigger tabs** — Cron / Interval. Cron retains the legacy
 *    `CronInput` for parity; Interval is a simple seconds input with
 *    minimum 30s (server-enforced floor matching the runner tick).
 * 3. **Workspace target** is only shown when the dialog is opened from
 *    the global automation page. When opened inside a project, the
 *    workspace is fixed and hidden.
 */

import { useEffect, useRef, useState } from "react";
import type { ActionKind, Trigger } from "@valuz/core";
import { automationsApi } from "@valuz/core";
import {
  browserTimezone,
  timezoneLabel,
  timezoneOptions,
} from "@valuz/shared";
import {
  Button,
  CronInput,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
} from "@valuz/ui";
import { useI18n } from "@valuz/ui";

/** Minimum interval seconds — matches backend `MIN_INTERVAL_SECONDS` */
const MIN_INTERVAL_SECONDS = 30;

/**
 * Format an absolute epoch-ms instant in the given IANA zone for the
 * "next run" preview, e.g. "Sat, Jun 6, 18:30" — so the user sees the next
 * fire in the same timezone they're scheduling in.
 */
function formatNextRun(ms: number, tz: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: tz,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(ms));
  } catch {
    return new Date(ms).toLocaleString();
  }
}

export interface AutomationAgentChoice {
  slug: string;
  name: string;
}

/**
 * Pre-fill data for edit mode. When set, the dialog opens with these
 * values populated, the title switches to "Edit ...", and the submit
 * button maps to update rather than create on the parent side. Same
 * shape as the submit payload so an edit round-trip is symmetric.
 */
export interface AutomationEditInitial {
  name: string;
  prompt_template: string;
  agent_slug: string;
  trigger: Trigger;
  action_kind: ActionKind;
}

export interface CreateAutomationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Submission flows up — parent owns the actual API call so it can
   * trigger a list refresh on the same code path that pickers / project
   * detail consume. The same callback fires for both create and edit;
   * the parent decides which API to hit based on whether ``initial``
   * was set.
   */
  onSubmit: (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
  }) => Promise<void>;
  /**
   * Candidate agents the user can pick. Parent loads from either
   * `agentsApi.listAgents()` (chat) or `agentsApi.listMembers(ws)`
   * (project) — the dialog doesn't care about the source.
   */
  agents: AutomationAgentChoice[];
  /** Default selection — usually the first member / first library agent. */
  defaultAgentSlug?: string;
  /**
   * Whether the bound workspace can host project-task automations. Maps
   * to ``workspace_kind === "project"`` at the call site. When false,
   * the Task mode toggle is rendered but disabled with a hint —
   * matches the backend ``AutomationTaskOnlyOnProject`` constraint.
   */
  allowTaskMode?: boolean;
  /**
   * Pre-fill values for edit mode. When provided, the dialog opens with
   * these values populated and the title defaults to "Edit ...". Omit
   * for create flows.
   */
  initial?: AutomationEditInitial;
  /** Dialog title — defaults to i18n `automation.createTitle` (or
   *  `automation.editTitle` in edit mode). */
  title?: string;
  /** Dialog description. */
  description?: string;
}

export const CreateAutomationDialog = ({
  open,
  onOpenChange,
  onSubmit,
  agents,
  defaultAgentSlug,
  allowTaskMode = false,
  initial,
  title: titleProp,
  description: descriptionProp,
}: CreateAutomationDialogProps) => {
  const { t } = useI18n();
  // The dialog reuses `cron.*` keys for fields whose semantics didn't
  // change (taskName / instruction / period) and pulls from the new
  // `automation.*` namespace for everything ADR-021 introduced
  // (agent picker, interval hints, dialog title fallback).
  const isEdit = Boolean(initial);
  const title =
    titleProp ??
    t(
      (isEdit
        ? "automation.dialogTitleEdit"
        : "automation.dialogTitleNew") as Parameters<typeof t>[0],
    );
  const description = descriptionProp ?? "";

  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");

  // Trigger state — discriminated union driven by the tab.
  const [triggerKind, setTriggerKind] = useState<"cron" | "interval">("cron");
  const [cron, setCron] = useState("0 9 * * *");
  // Scheduling timezone — the IANA zone the cron rule is read in. Defaults to
  // the live BROWSER timezone (the user's real local zone, correct for desktop
  // AND headless+WebUI); the backend only OS-detects on the no-browser agent
  // path. Always sent explicitly so a schedule is never silently UTC.
  const [timezone, setTimezone] = useState<string>(browserTimezone());
  // Live next-run preview (absolute epoch ms). Recomputed via the backend
  // cron validator so the preview can't disagree with what the scheduler does.
  const [nextRunMs, setNextRunMs] = useState<number | null>(null);
  // Interval input — user picks a number + a unit; we convert to seconds
  // at submit time. Default is "5 minutes" so the dialog opens at the
  // common-case cadence instead of seconds (which is the floor but a
  // less likely choice in practice).
  type IntervalUnit = "seconds" | "minutes" | "hours" | "days";
  const UNIT_TO_SECONDS: Record<IntervalUnit, number> = {
    seconds: 1,
    minutes: 60,
    hours: 3600,
    days: 86400,
  };
  const [intervalValue, setIntervalValue] = useState<number>(5);
  const [intervalUnit, setIntervalUnit] = useState<IntervalUnit>("minutes");

  // Derived seconds the row will eventually store. We clamp at submit
  // (Math.max with MIN_INTERVAL_SECONDS) rather than mutating the
  // displayed value so the user keeps seeing what they typed.
  const intervalSeconds = Math.max(
    0,
    Math.floor(intervalValue * UNIT_TO_SECONDS[intervalUnit]),
  );

  // Agent selection. Falls through to the first agent so the user never
  // sees an empty picker when at least one option is available.
  const [agentSlug, setAgentSlug] = useState<string>("");

  // Execution mode toggle. Defaults to ``chat`` for create flows; edit
  // mode seeds from ``initial.action_kind``. When ``allowTaskMode`` is
  // false (chat workspaces) the Task radio is disabled and we coerce
  // ``task`` back to ``chat`` at submit time as a defence-in-depth.
  const [actionKind, setActionKind] = useState<ActionKind>("chat");

  // Edge-triggered reset: only when the dialog transitions from closed
  // to open. ``initial`` and ``agents`` are fresh references on every
  // parent render (the parent typically constructs them inline), so
  // including them in the dep array would re-fire this effect on every
  // parent re-render and clobber the user's in-progress edits — that's
  // exactly the bug where "Task" silently flipped back to "Simple"
  // moments after the user picked it. The ref tracks the previous
  // ``open`` value so the body runs exactly once per open transition,
  // while the deps stay broad enough to satisfy the exhaustive-deps
  // lint without changing behaviour.
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    if (wasOpenRef.current) return; // already initialised for this open cycle
    wasOpenRef.current = true;
    if (initial) {
      setName(initial.name);
      setPrompt(initial.prompt_template);
      setAgentSlug(initial.agent_slug);
      // Edit mode: seed from the existing row. If the row stored ``task``
      // but the workspace no longer permits it (e.g. moved to chat by an
      // admin), coerce back to ``chat`` so the dialog renders a valid
      // state — the user can still change it without an inconsistent
      // initial render.
      setActionKind(
        initial.action_kind === "task" && !allowTaskMode
          ? "chat"
          : initial.action_kind,
      );
      if (initial.trigger.kind === "cron") {
        setTriggerKind("cron");
        setCron(initial.trigger.cron_expr || "0 9 * * *");
        setTimezone(initial.trigger.timezone || browserTimezone());
        // Reset interval fields to the default so a subsequent tab
        // switch lands on a sensible value instead of stale 5m.
        setIntervalValue(5);
        setIntervalUnit("minutes");
      } else if (initial.trigger.kind === "interval") {
        setTriggerKind("interval");
        setCron("0 9 * * *");
        // Pick the largest unit that divides the stored seconds evenly
        // so 3600s shows as "1 hour" rather than "3600 seconds" —
        // round-trip fidelity for the common multiples; falls back to
        // raw seconds for anything else (e.g. 90s → "90 seconds").
        const s = initial.trigger.seconds;
        if (s % 86400 === 0) {
          setIntervalValue(s / 86400);
          setIntervalUnit("days");
        } else if (s % 3600 === 0) {
          setIntervalValue(s / 3600);
          setIntervalUnit("hours");
        } else if (s % 60 === 0) {
          setIntervalValue(s / 60);
          setIntervalUnit("minutes");
        } else {
          setIntervalValue(s);
          setIntervalUnit("seconds");
        }
      } else {
        // ``manual`` triggers aren't exposed in the UI yet — fall through
        // to the default cron view so editing still works.
        setTriggerKind("cron");
        setCron("0 9 * * *");
        setIntervalValue(5);
        setIntervalUnit("minutes");
      }
      return;
    }
    setName("");
    setPrompt("");
    setTriggerKind("cron");
    setCron("0 9 * * *");
    setTimezone(browserTimezone());
    setIntervalValue(5);
    setIntervalUnit("minutes");
    setAgentSlug(defaultAgentSlug ?? agents[0]?.slug ?? "");
    setActionKind("chat");
  }, [open, initial, defaultAgentSlug, agents, allowTaskMode]);

  // Debounced next-run preview: re-validate the cron in the selected tz and
  // surface the next fire instant. Only for cron triggers; interval/manual
  // clear it. The cancel flag drops stale responses if cron/tz change again
  // before the request returns.
  useEffect(() => {
    let cancelled = false;
    // All state writes live inside the debounce callback (async), so they
    // don't trip react-hooks/set-state-in-effect and don't cascade renders.
    const handle = setTimeout(() => {
      if (cancelled) return;
      if (triggerKind !== "cron" || !cron.trim()) {
        setNextRunMs(null);
        return;
      }
      automationsApi
        .validateCron(cron, timezone)
        .then((res) => {
          if (cancelled) return;
          const first =
            res.valid && res.next_runs.length > 0
              ? Number(res.next_runs[0])
              : null;
          setNextRunMs(first != null && Number.isFinite(first) ? first : null);
        })
        .catch(() => {
          if (!cancelled) setNextRunMs(null);
        });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [triggerKind, cron, timezone]);

  const buildTrigger = (): Trigger => {
    if (triggerKind === "cron") {
      return {
        kind: "cron",
        cron_expr: cron || "0 9 * * *",
        // Always explicit — the user's selected (browser-defaulted) zone,
        // never null/UTC.
        timezone: timezone || browserTimezone(),
      };
    }
    return {
      kind: "interval",
      seconds: Math.max(intervalSeconds, MIN_INTERVAL_SECONDS),
    };
  };

  const submitDisabled =
    !agentSlug ||
    !prompt.trim() ||
    (triggerKind === "interval" && intervalSeconds < MIN_INTERVAL_SECONDS);

  const handleSubmit = async () => {
    if (submitDisabled) return;
    await onSubmit({
      name: name.trim() || t("cron.untitled" as Parameters<typeof t>[0]),
      prompt_template: prompt.trim(),
      agent_slug: agentSlug,
      trigger: buildTrigger(),
      // Defence-in-depth: if the workspace doesn't permit task mode, the
      // submit always coerces to chat regardless of the local toggle.
      action_kind: allowTaskMode ? actionKind : "chat",
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <FormField label={t("cron.taskName" as Parameters<typeof t>[0])}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t(
                "cron.taskNamePlaceholder" as Parameters<typeof t>[0],
              )}
            />
          </FormField>

          {/* Execution mode — Chat (single agent run) vs Task (kick off
              project task with this agent as Lead). Placed above the
              instruction so the user picks the mode before writing the
              prompt — the same prompt reads differently depending on
              whether it's a single turn or a task goal. Task mode is
              only valid on project workspaces; on chat we render the
              toggle but disable the Task pill with a hint. */}
          <FormField
            label={t("automation.actionKindLabel" as Parameters<typeof t>[0])}
          >
            <div className="flex items-stretch gap-2">
              {(
                [
                  {
                    value: "chat" as const,
                    label: t(
                      "automation.actionKindChat" as Parameters<typeof t>[0],
                    ),
                    hint: t(
                      "automation.actionKindChatHint" as Parameters<
                        typeof t
                      >[0],
                    ),
                    disabled: false,
                  },
                  {
                    value: "task" as const,
                    label: t(
                      "automation.actionKindTask" as Parameters<typeof t>[0],
                    ),
                    hint: allowTaskMode
                      ? t(
                          "automation.actionKindTaskHint" as Parameters<
                            typeof t
                          >[0],
                        )
                      : t(
                          "automation.actionKindTaskDisabledHint" as Parameters<
                            typeof t
                          >[0],
                        ),
                    disabled: !allowTaskMode,
                  },
                ] as const
              ).map((opt) => {
                const active = actionKind === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={opt.disabled}
                    onClick={() => !opt.disabled && setActionKind(opt.value)}
                    className={
                      "flex-1 rounded-lg border px-3 py-2 text-left text-xs transition-colors " +
                      (opt.disabled
                        ? "cursor-not-allowed border-surface-border bg-surface-soft text-ink-meta opacity-60"
                        : active
                          ? "border-brand bg-brand/5 text-ink-heading"
                          : "border-surface-border bg-card text-ink-body hover:border-brand/40")
                    }
                  >
                    <div className="font-medium">{opt.label}</div>
                    <div className="mt-0.5 text-[11px] leading-4 text-ink-meta">
                      {opt.hint}
                    </div>
                  </button>
                );
              })}
            </div>
          </FormField>

          <FormField label={t("cron.instruction" as Parameters<typeof t>[0])}>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={t(
                "cron.instructionPlaceholder" as Parameters<typeof t>[0],
              )}
              rows={4}
            />
          </FormField>

          {/* Trigger — Cron / Interval tabs. */}
          <FormField label={t("cron.period" as Parameters<typeof t>[0])}>
            <Tabs
              value={triggerKind}
              onValueChange={(v) => setTriggerKind(v as "cron" | "interval")}
            >
              <TabsList>
                <TabsTrigger value="cron">
                  {t("automation.triggerCron" as Parameters<typeof t>[0])}
                </TabsTrigger>
                <TabsTrigger value="interval">
                  {t("automation.triggerInterval" as Parameters<typeof t>[0])}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="cron" className="pt-3 space-y-2">
                {/* Timezone rides the same row as frequency/hour/minute via
                    CronInput's slot (forced selection, browser-tz default,
                    "City (GMT±N)" label). */}
                <CronInput
                  value={cron}
                  onChange={setCron}
                  timezoneSlot={
                    <div className="min-w-[150px] flex-1">
                      <label className="mb-1 block text-xs font-medium text-ink-heading">
                        {t(
                          "automation.timezoneLabel" as Parameters<typeof t>[0],
                        )}
                      </label>
                      <Select
                        value={timezone}
                        onValueChange={(v) => v && setTimezone(v)}
                      >
                        <SelectTrigger className="w-full text-xs">
                          <SelectValue>{timezoneLabel(timezone)}</SelectValue>
                        </SelectTrigger>
                        <SelectContent className="max-h-72">
                          {timezoneOptions(timezone).map((tz) => (
                            <SelectItem key={tz} value={tz}>
                              {timezoneLabel(tz)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  }
                />
                {nextRunMs != null && (
                  <p className="text-xs text-ink-meta">
                    {t(
                      "automation.nextRunPreview" as Parameters<typeof t>[0],
                    )}
                    {" · "}
                    {formatNextRun(nextRunMs, timezone)}
                  </p>
                )}
              </TabsContent>
              <TabsContent value="interval" className="pt-3 space-y-2">
                {/* Number input + unit Select on one row. Min on the input
                    is unit-relative (1 for non-second units; 30 for
                    seconds) — server-side floor stays 30s; the unit
                    constraint just keeps the input from accepting 0 or
                    negatives. The hint line below restates the
                    resolved seconds + floor explicitly. */}
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    min={intervalUnit === "seconds" ? MIN_INTERVAL_SECONDS : 1}
                    value={intervalValue}
                    onChange={(e) => {
                      const v = Number.parseInt(e.target.value, 10);
                      if (Number.isFinite(v)) setIntervalValue(v);
                    }}
                    className="flex-1"
                  />
                  <Select
                    value={intervalUnit}
                    onValueChange={(v) => setIntervalUnit(v as IntervalUnit)}
                  >
                    <SelectTrigger className="w-[110px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="seconds">
                        {t(
                          "automation.intervalUnitSeconds" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </SelectItem>
                      <SelectItem value="minutes">
                        {t(
                          "automation.intervalUnitMinutes" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </SelectItem>
                      <SelectItem value="hours">
                        {t(
                          "automation.intervalUnitHours" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </SelectItem>
                      <SelectItem value="days">
                        {t(
                          "automation.intervalUnitDays" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <p className="text-xs text-ink-meta">
                  {intervalSeconds < MIN_INTERVAL_SECONDS
                    ? t(
                        "automation.intervalBelowFloor" as Parameters<
                          typeof t
                        >[0],
                        { min: MIN_INTERVAL_SECONDS },
                      )
                    : t("automation.intervalEvery" as Parameters<typeof t>[0], {
                        seconds: intervalSeconds,
                        min: MIN_INTERVAL_SECONDS,
                      })}
                </p>
              </TabsContent>
            </Tabs>
          </FormField>

          <FormField
            label={t("automation.agentLabel" as Parameters<typeof t>[0])}
          >
            <Select
              value={agentSlug}
              onValueChange={setAgentSlug}
              disabled={agents.length === 0}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    agents.length === 0
                      ? t(
                          "automation.agentPlaceholderEmpty" as Parameters<
                            typeof t
                          >[0],
                        )
                      : t(
                          "automation.agentPlaceholderPick" as Parameters<
                            typeof t
                          >[0],
                        )
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {agents.map((a) => (
                  <SelectItem key={a.slug} value={a.slug}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-1 text-xs text-ink-meta">
              {t("automation.agentHint" as Parameters<typeof t>[0])}
            </p>
          </FormField>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common.cancel" as Parameters<typeof t>[0])}
          </Button>
          <Button onClick={handleSubmit} disabled={submitDisabled}>
            {t("common.save" as Parameters<typeof t>[0])}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
