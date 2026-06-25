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
 * 3. **Project target** is only shown when the dialog is opened from
 *    the global automation page. When opened inside a project, the
 *    project is fixed and hidden.
 */

import { useEffect, useRef, useState } from "react";
import type { ActionKind, AutomationProjectTarget, Trigger } from "@valuz/core";
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
   * Edit mode only: whether the locked project can host project-task
   * automations (``project_kind === "project"``). In CREATE mode this is
   * ignored — Task availability is derived from ``targets`` instead (any
   * project target present ⇒ Task selectable). When false, the Task pill
   * is disabled with a hint — matches the backend
   * ``AutomationTaskOnlyOnProject`` constraint.
   */
  allowTaskMode?: boolean;
  /**
   * Target choices for the CREATE flow — the Chat sentinel plus every
   * project (``automationsApi.listProjectTargets``). When present (and not
   * edit mode) the dialog renders a target selector wired to
   * ``onSelectTarget``, linked to the agent picker: picking a project
   * swaps the candidate agents to that project's members. Omitted in edit
   * mode, where the target is fixed to the row's project.
   */
  targets?: AutomationProjectTarget[];
  /** Currently-selected target id (owned by the parent so the agent list
   *  can be recomputed from it). */
  selectedTargetId?: string | null;
  /** Notify the parent the user picked a different target. */
  onSelectTarget?: (id: string) => void;
  /**
   * Display name of the locked project for flows where the target is fixed
   * and no selector is shown (the project detail page — the automation is
   * always bound to the current project). When set and the selector is
   * hidden, the dialog renders a read-only, lock-badged project field so the
   * user can confirm *where* the automation is being created without being
   * able to change it. Omitted on the global automation page (the selector
   * already shows the choice).
   */
  fixedTargetName?: string;
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
  targets,
  selectedTargetId,
  onSelectTarget,
  fixedTargetName,
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

  // Target wiring (create flow only). The selector lets the user point the
  // automation at the Chat sentinel or any project; the chosen target both
  // decides ``project_kind`` at submit and drives which agents are offered.
  const targetList = targets ?? [];
  const projectTargets = targetList.filter((tg) => tg.kind === "project");
  const showTargetSelector = !isEdit && targetList.length > 0;
  const selectedTarget =
    targetList.find((tg) => tg.id === selectedTargetId) ?? null;
  // Task mode availability. When the target selector is shown (global
  // automation page) Task needs at least one project to host it. When the
  // selector is hidden the project is fixed by the caller — edit mode (the
  // row's project) or the project detail page (the current project) — so the
  // caller's ``allowTaskMode`` decides. The old ``!isEdit ⇒ projectTargets``
  // form wrongly disabled Task on the project page, which passes
  // ``allowTaskMode`` but no ``targets`` (the project is implicit).
  const taskModeAllowed = showTargetSelector
    ? projectTargets.length > 0
    : allowTaskMode;

  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");

  // Trigger state — discriminated union driven by the tab.
  const [triggerKind, setTriggerKind] = useState<"cron" | "interval" | "manual">(
    "cron",
  );
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
  // false (chat projects) the Task radio is disabled and we coerce
  // ``task`` back to ``chat`` at submit time as a defence-in-depth.
  const [actionKind, setActionKind] = useState<ActionKind>("chat");

  // The agent picker is linked to the target: switching projects swaps the
  // candidate list out from under the stored ``agentSlug``. Rather than chase
  // it with an effect (the parent hands a fresh ``agents`` array every render),
  // derive the effective selection — keep the user's pick when it's still
  // valid, otherwise fall back to the first candidate. No setState, no churn.
  const effectiveAgentSlug =
    agentSlug && agents.some((a) => a.slug === agentSlug)
      ? agentSlug
      : (agents[0]?.slug ?? "");

  // Picking Task while pointed at Chat (or nothing) auto-jumps to the first
  // project — Task can't run without one, and this saves a second click.
  const handlePickMode = (value: ActionKind) => {
    setActionKind(value);
    if (
      value === "task" &&
      !isEdit &&
      selectedTarget?.kind !== "project" &&
      projectTargets[0]
    ) {
      onSelectTarget?.(projectTargets[0].id);
    }
  };

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
      // but the project no longer permits it (e.g. moved to chat by an
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
        // ``manual`` triggers: preserve as a read-only state so editing other
        // fields (name / prompt / agent) round-trips the trigger UNCHANGED.
        // The old code fell through to a default cron seed, and ``buildTrigger``
        // could only emit cron/interval — so one edit silently rewrote a manual
        // automation to a daily cron (data loss). Now ``buildTrigger`` returns
        // ``{ kind: "manual" }`` for this state. Cron/interval defaults are kept
        // only so a deliberate tab switch lands on a sane value.
        setTriggerKind("manual");
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
    if (triggerKind === "manual") {
      // Round-trip the manual trigger unchanged (see seeding note above).
      return { kind: "manual" };
    }
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

  // Task mode needs a concrete project target only when the selector is
  // shown; block submit until one is picked (the selector only offers
  // projects in Task mode, so this only bites on the brief window before the
  // auto-jump lands). With the selector hidden the project is fixed by the
  // caller, so there's nothing to wait for.
  const taskNeedsProject =
    showTargetSelector &&
    actionKind === "task" &&
    selectedTarget?.kind !== "project";

  const submitDisabled =
    !effectiveAgentSlug ||
    !prompt.trim() ||
    taskNeedsProject ||
    (triggerKind === "interval" && intervalSeconds < MIN_INTERVAL_SECONDS);

  const handleSubmit = async () => {
    if (submitDisabled) return;
    await onSubmit({
      name: name.trim() || t("cron.untitled" as Parameters<typeof t>[0]),
      prompt_template: prompt.trim(),
      agent_slug: effectiveAgentSlug,
      trigger: buildTrigger(),
      // Defence-in-depth: if task mode isn't available, the submit always
      // coerces to chat regardless of the local toggle.
      action_kind: taskModeAllowed ? actionKind : "chat",
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
              only valid on project projects; on chat we render the
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
                    hint: taskModeAllowed
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
                    disabled: !taskModeAllowed,
                  },
                ] as const
              ).map((opt) => {
                const active = actionKind === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={opt.disabled}
                    onClick={() => !opt.disabled && handlePickMode(opt.value)}
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

          {/* "所属项目" — where this automation lives, picked before who runs
              it. Two presentations of the SAME control so the global and
              in-project dialogs read as one component:
              - Global automation page: an editable Select over the Chat
                sentinel (localised "快速对话") + every project. In Task mode
                only projects are offered. Drives ``project_kind`` at submit
                and the candidate agents below.
              - Project detail page: the project is fixed by context, so the
                same Select is rendered ``disabled`` showing the bound
                project — visible (no "did I create this in the right place?"
                doubt) but unchangeable.
              The label is constant ("所属项目") across both modes — it never
              swaps mid-interaction. */}
          {/* 所属项目 + 智能体 share one row. The project field is dropped when
              there's neither a selectable target nor a fixed project, so the
              agent field then spans the full width. */}
          <div className="flex items-start gap-3">
            {(showTargetSelector || fixedTargetName) && (
              <div className="min-w-0 flex-1">
                {showTargetSelector ? (
                  <FormField
                    label={t(
                      "automation.targetLabelTask" as Parameters<typeof t>[0],
                    )}
                  >
                    <Select
                      value={selectedTargetId ?? ""}
                      onValueChange={(v) => v && onSelectTarget?.(v)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(actionKind === "task"
                          ? projectTargets
                          : targetList
                        ).map((tg) => (
                          <SelectItem key={tg.id} value={tg.id}>
                            {tg.kind === "chat"
                              ? t(
                                  "automation.targetChat" as Parameters<
                                    typeof t
                                  >[0],
                                )
                              : tg.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormField>
                ) : (
                  <FormField
                    label={t(
                      "automation.targetLabelTask" as Parameters<typeof t>[0],
                    )}
                  >
                    {/* Disabled Select = the same control, locked: the dimmed
                        trigger + non-interactive chevron read as "fixed". */}
                    <Select value="__fixed__" disabled>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__fixed__">
                          {fixedTargetName}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </FormField>
                )}
              </div>
            )}

            <div className="min-w-0 flex-1">
              <FormField
                label={t("automation.agentLabel" as Parameters<typeof t>[0])}
              >
                <Select
                  value={effectiveAgentSlug}
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
              </FormField>
            </div>
          </div>

          <FormField label={t("cron.instruction" as Parameters<typeof t>[0])}>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={t(
                "cron.instructionPlaceholder" as Parameters<typeof t>[0],
              )}
              rows={4}
              // Fixed height with its own scroll — ``field-sizing-fixed``
              // overrides the Textarea's default content-sizing auto-grow so a
              // long instruction can't grow the dialog past the viewport.
              className="field-sizing-fixed h-32 resize-none"
            />
          </FormField>

          {/* Trigger — Cron / Interval tabs. Manual automations (created via
              MCP / proposal, not this form) render a read-only notice instead,
              so editing other fields preserves the manual trigger. */}
          <FormField label={t("cron.period" as Parameters<typeof t>[0])}>
            {triggerKind === "manual" ? (
              <div className="rounded-md border border-surface-border bg-surface-soft px-3 py-2.5 text-xs text-ink-body">
                {t("automation.triggerManualNotice" as Parameters<typeof t>[0])}
              </div>
            ) : (
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
            )}
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
