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
import { Maximize2 } from "lucide-react";
import type {
  ActionKind,
  AutomationPlaybookChoice,
  AutomationProjectTarget,
  Trigger,
} from "@valuz/core";
import {
  automationsApi,
  getDefaultExecutionTarget,
  getExecutionTargets,
  resolveApiBase,
  useExecutionTargets,
} from "@valuz/core";
import { browserTimezone, timezoneLabel, timezoneOptions } from "@valuz/shared";
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
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
} from "@valuz/ui";
import { useI18n } from "@valuz/ui";
import {
  ExecutionLocationPicker,
  OriginBadge,
} from "./ExecutionLocationPicker";

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
  worktree?: boolean;
  playbook_definition_id?: string | null;
  playbook_version?: number | null;
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
    worktree: boolean;
    playbook_definition_id: string | null;
    playbook_version: number | null;
    /** Execution-location target id (``"local"``/``"cloud"``) for a
     * Chat-standalone automation on multi-target editions; ``undefined`` for
     * project-bound targets (they inherit the project's origin), in edit
     * mode, and on single-backend builds. The parent resolves it to a
     * ``baseUrl`` for the create call. */
    exec_location?: string;
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
  /** Currently-selected execution location for a Chat-standalone target
   *  (``"local"``/``"cloud"``), owned by the parent so it can re-source the
   *  Chat agent list from the matching backend. ``undefined`` on
   *  single-target builds and for project-bound targets (those inherit the
   *  project's origin). */
  selectedExecLocation?: string | null;
  /** Notify the parent the user changed the Chat-standalone location. */
  onSelectExecLocation?: (id: string) => void;
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
  /** Project identity used only to route the Playbook picker when the target
   * is locked (project page / detail edit). UI copy remains "工作区" in the
   * Finance edition; the persisted identity is still project_id. */
  fixedProjectId?: string;
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
  selectedExecLocation,
  onSelectExecLocation,
  fixedTargetName,
  fixedProjectId,
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

  // Execution location (multi-target editions only). An automation fires a
  // session in its target project's cwd on that project's backend, so a
  // project-bound target INHERITS the project's origin (read-only badge);
  // only a Chat-standalone target (no project_id — backend lazy-creates a
  // chat project) needs a picker, and the chosen backend is where that chat
  // project lands. Hidden on single-target builds (picker renders null) and
  // in edit mode (``showTargetSelector`` is false).
  const execTargets = useExecutionTargets();
  const isChatStandaloneTarget =
    showTargetSelector && selectedTarget?.kind !== "project";

  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  // When true the whole dialog turns into a large, focused instruction editor.
  const [promptFullscreen, setPromptFullscreen] = useState(false);
  // Snapshot of the instruction taken when the fullscreen editor opened, so
  // Cancel can discard the in-editor edits and restore the form's prior value.
  const promptBeforeFullscreenRef = useRef("");

  // Trigger state — discriminated union driven by the tab.
  const [triggerKind, setTriggerKind] = useState<
    "cron" | "interval" | "manual"
  >("cron");
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
  // Optional process contract. Automation remains useful as a lightweight
  // Trigger × Agent instruction; selecting a Playbook upgrades each fire into
  // a persisted PlaybookRun pinned to one immutable Definition version.
  const [playbooks, setPlaybooks] = useState<AutomationPlaybookChoice[]>([]);
  const [playbookDefinitionId, setPlaybookDefinitionId] = useState("");
  // Worktree isolation (design §5) — valid for BOTH action kinds, shown
  // whenever a real (git-repo) project is bound. ``chat`` runs each fire in its
  // own worktree; ``task`` runs lead + every member in one worktree.
  const [worktree, setWorktree] = useState(false);

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
    // Task kickoff is fire-and-forget today, so its terminal lifecycle cannot
    // yet close a PlaybookRun truthfully. Keep the UI aligned with the backend
    // fail-closed rule by clearing the pin when Task is selected.
    if (value === "task") setPlaybookDefinitionId("");
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
    setPromptFullscreen(false);
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
      setPlaybookDefinitionId(initial.playbook_definition_id ?? "");
      setWorktree(Boolean(initial.worktree));
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
        // Manual: no schedule — the automation only runs on demand
        // (``run_now``). Keep the other tabs' fields at sensible defaults so
        // switching away lands on a valid value.
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
    setPlaybookDefinitionId("");
    setWorktree(false);
  }, [open, initial, defaultAgentSlug, agents, allowTaskMode]);

  // Definitions are listed from the same backend that will own the
  // Automation. Their owner Project does not constrain where they execute:
  // Definition Project and PlaybookRun Project are intentionally independent.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const routedProjectId = selectedTarget?.project_id ?? fixedProjectId;
    let baseUrl = routedProjectId
      ? resolveApiBase({ projectId: routedProjectId }, "") || undefined
      : undefined;
    if (isChatStandaloneTarget && selectedExecLocation) {
      baseUrl = getExecutionTargets().find(
        (target) => target.id === selectedExecLocation,
      )?.baseUrl;
    }
    automationsApi
      .listPlaybooks(baseUrl ? { baseUrl } : undefined)
      .then((items) => {
        if (!cancelled) setPlaybooks(items);
      })
      .catch(() => {
        if (!cancelled) setPlaybooks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [
    open,
    selectedTarget?.project_id,
    fixedProjectId,
    isChatStandaloneTarget,
    selectedExecLocation,
  ]);

  // Debounced next-run preview: re-validate the cron in the selected tz and
  // surface the next fire instant. Only for cron triggers; interval/manual
  // clear it. The cancel flag drops stale responses if cron/tz change again
  // before the request returns.
  useEffect(() => {
    // Don't hit the network while the dialog is closed. The project home mounts
    // this dialog permanently (``open={createDialogOpen}``), so an ungated cron
    // preview fired a ``validate-cron`` POST on every project-home load.
    if (!open) return;
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
  }, [open, triggerKind, cron, timezone]);

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
    if (triggerKind === "manual") {
      // No schedule — fires only via ``run_now`` (or a future webhook).
      return { kind: "manual" };
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
    (!prompt.trim() && !playbookDefinitionId) ||
    taskNeedsProject ||
    (triggerKind === "interval" && intervalSeconds < MIN_INTERVAL_SECONDS);

  const handleSubmit = async () => {
    if (submitDisabled) return;
    const selectedPlaybook = playbooks.find(
      (playbook) => playbook.id === playbookDefinitionId,
    );
    const pinnedVersion =
      playbookDefinitionId === initial?.playbook_definition_id &&
      initial?.playbook_version
        ? initial.playbook_version
        : (selectedPlaybook?.current_version ?? null);
    await onSubmit({
      name: name.trim() || t("cron.untitled" as Parameters<typeof t>[0]),
      prompt_template: prompt.trim(),
      agent_slug: effectiveAgentSlug,
      trigger: buildTrigger(),
      // Defence-in-depth: if task mode isn't available, the submit always
      // coerces to chat regardless of the local toggle.
      action_kind: taskModeAllowed ? actionKind : "chat",
      // Worktree applies to both chat and task, gated on a real (git-repo)
      // project being bound — the same condition as ``taskModeAllowed``.
      worktree: taskModeAllowed ? worktree : false,
      playbook_definition_id: playbookDefinitionId || null,
      playbook_version: playbookDefinitionId ? pinnedVersion : null,
      // Only a Chat-standalone target carries a location choice; project-bound
      // targets inherit the project's origin and the parent routes via the
      // project id.
      exec_location:
        isChatStandaloneTarget && execTargets.length >= 2
          ? (selectedExecLocation ?? getDefaultExecutionTarget()?.id)
          : undefined,
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[min(640px,85vh)] max-w-xl gap-0 overflow-hidden p-0">
        {promptFullscreen ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DialogHeader className="px-[18px] pt-[18px] pb-2">
              <DialogTitle className="text-sm leading-5">
                {t("cron.instruction" as Parameters<typeof t>[0])}
              </DialogTitle>
              <DialogDescription className="sr-only">
                {t("cron.instruction" as Parameters<typeof t>[0])}
              </DialogDescription>
            </DialogHeader>
            {/* Padded wrapper so the ``w-full`` Textarea doesn't overflow the
                dialog (a horizontal margin on a w-full field clips the right
                edge). */}
            <div className="flex min-h-0 flex-1 flex-col px-[18px]">
              <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={t(
                  "cron.instructionPlaceholder" as Parameters<typeof t>[0],
                )}
                autoFocus
                // Override the Textarea's content-sizing defaults so it fills
                // the dialog instead of shrinking to its text / the 40vh cap.
                className="min-h-0 max-h-none flex-1 resize-none field-sizing-fixed"
              />
            </div>
            <DialogFooter className="px-[18px] pt-3 pb-4">
              <Button
                variant="outline"
                onClick={() => {
                  // Discard the in-editor changes; restore the form's value.
                  setPrompt(promptBeforeFullscreenRef.current);
                  setPromptFullscreen(false);
                }}
              >
                {t("common.cancel" as Parameters<typeof t>[0])}
              </Button>
              <Button onClick={() => setPromptFullscreen(false)}>
                {t("common.done" as Parameters<typeof t>[0])}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <DialogHeader className="px-[18px] pt-[18px] pb-1">
              <DialogTitle className="text-sm leading-5">{title}</DialogTitle>
              <DialogDescription>{description}</DialogDescription>
            </DialogHeader>

            <div className="flex min-h-0 flex-1 flex-col gap-[14px] overflow-y-auto px-[18px] py-[14px]">
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
                label={t(
                  "automation.actionKindLabel" as Parameters<typeof t>[0],
                )}
              >
                <div className="flex items-stretch gap-2">
                  {(
                    [
                      {
                        value: "chat" as const,
                        label: t(
                          "automation.actionKindChat" as Parameters<
                            typeof t
                          >[0],
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
                          "automation.actionKindTask" as Parameters<
                            typeof t
                          >[0],
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
                        onClick={() =>
                          !opt.disabled && handlePickMode(opt.value)
                        }
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
                        <div className="mt-0.5 text-2xs leading-4 text-ink-meta">
                          {opt.hint}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </FormField>

              {actionKind === "chat" ? (
                <FormField
                  label={t(
                    "automation.playbookLabel" as Parameters<typeof t>[0],
                  )}
                >
                  <Select
                    value={playbookDefinitionId || "__none__"}
                    onValueChange={(value) =>
                      setPlaybookDefinitionId(
                        value === "__none__" ? "" : value,
                      )
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">
                        {t(
                          "automation.playbookNone" as Parameters<typeof t>[0],
                        )}
                      </SelectItem>
                      {playbooks.map((playbook) => {
                        const version =
                          playbook.id === initial?.playbook_definition_id
                            ? (initial.playbook_version ??
                              playbook.current_version)
                            : playbook.current_version;
                        return (
                          <SelectItem key={playbook.id} value={playbook.id}>
                            {playbook.name} · v{version}
                            {playbook.status === "retired"
                              ? ` · ${t(
                                  "automation.playbookRetired" as Parameters<
                                    typeof t
                                  >[0],
                                )}`
                              : ""}
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                  <p className="mt-1 text-2xs leading-4 text-ink-meta">
                    {t(
                      "automation.playbookHint" as Parameters<typeof t>[0],
                    )}
                  </p>
                </FormField>
              ) : null}

              {/* Worktree toggle — shown whenever a real (git-repo) project is
              bound, for BOTH chat and task actions. Off = the fire works in the
              project directory; on = it runs in an isolated git worktree (a
              chat fire gets its own session worktree; a task shares one across
              lead + members) whose branch merges back / is discarded at end. */}
              {taskModeAllowed && (
                <FormField
                  label={t(
                    "automation.worktreeLabel" as Parameters<typeof t>[0],
                  )}
                >
                  <div className="flex items-center justify-between gap-3 rounded-lg border border-surface-border bg-card px-3 py-2">
                    <p className="text-2xs leading-4 text-ink-meta">
                      {t("automation.worktreeHint" as Parameters<typeof t>[0])}
                    </p>
                    <Switch checked={worktree} onCheckedChange={setWorktree} />
                  </div>
                </FormField>
              )}

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
              <div className="grid grid-cols-2 items-start gap-2">
                {(showTargetSelector || fixedTargetName) && (
                  <div className="min-w-0">
                    {showTargetSelector ? (
                      <FormField
                        label={t(
                          "automation.targetLabelTask" as Parameters<
                            typeof t
                          >[0],
                        )}
                      >
                        <Select
                          value={selectedTargetId ?? ""}
                          onValueChange={(v) => v && onSelectTarget?.(v)}
                        >
                          <SelectTrigger className="w-full">
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
                          "automation.targetLabelTask" as Parameters<
                            typeof t
                          >[0],
                        )}
                      >
                        {/* Disabled Select = the same control, locked: the dimmed
                        trigger + non-interactive chevron read as "fixed". */}
                        <Select value="__fixed__" disabled>
                          <SelectTrigger className="w-full">
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
                    {showTargetSelector && execTargets.length >= 2 ? (
                      <div className="mt-2">
                        <FormField
                          label={t(
                            "project.execLocation" as Parameters<typeof t>[0],
                          )}
                        >
                          {isChatStandaloneTarget ? (
                            <ExecutionLocationPicker
                              value={selectedExecLocation ?? null}
                              onChange={(id) => onSelectExecLocation?.(id)}
                            />
                          ) : (
                            // Project-bound: the automation inherits the
                            // target project's execution origin — read-only.
                            <OriginBadge
                              entityId={selectedTarget?.project_id ?? null}
                              kind="project"
                            />
                          )}
                        </FormField>
                      </div>
                    ) : null}
                  </div>
                )}

                <div
                  className={
                    showTargetSelector || fixedTargetName
                      ? "min-w-0"
                      : "col-span-2 min-w-0"
                  }
                >
                  <FormField
                    label={t(
                      "automation.agentLabel" as Parameters<typeof t>[0],
                    )}
                  >
                    <Select
                      value={effectiveAgentSlug}
                      onValueChange={setAgentSlug}
                      disabled={agents.length === 0}
                    >
                      <SelectTrigger className="w-full">
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

              <FormField
                label={t("cron.instruction" as Parameters<typeof t>[0])}
                labelAction={
                  <button
                    type="button"
                    onClick={() => {
                      promptBeforeFullscreenRef.current = prompt;
                      setPromptFullscreen(true);
                    }}
                    className="flex h-5 w-5 items-center justify-center rounded text-ink-meta transition-colors hover:bg-surface-muted hover:text-ink-body"
                    title={t(
                      "cron.instructionExpand" as Parameters<typeof t>[0],
                    )}
                    aria-label={t(
                      "cron.instructionExpand" as Parameters<typeof t>[0],
                    )}
                  >
                    <Maximize2 className="h-3.5 w-3.5" />
                  </button>
                }
              >
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

              {/* Trigger — Cron / Interval tabs. */}
              <FormField label={t("cron.period" as Parameters<typeof t>[0])}>
                <Tabs
                  value={triggerKind}
                  onValueChange={(v) =>
                    setTriggerKind(v as "cron" | "interval" | "manual")
                  }
                >
                  <TabsList>
                    <TabsTrigger value="cron">
                      {t("automation.triggerCron" as Parameters<typeof t>[0])}
                    </TabsTrigger>
                    <TabsTrigger value="interval">
                      {t(
                        "automation.triggerInterval" as Parameters<typeof t>[0],
                      )}
                    </TabsTrigger>
                    <TabsTrigger value="manual">
                      {t("automation.triggerManual" as Parameters<typeof t>[0])}
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
                              "automation.timezoneLabel" as Parameters<
                                typeof t
                              >[0],
                            )}
                          </label>
                          <Select
                            value={timezone}
                            onValueChange={(v) => v && setTimezone(v)}
                          >
                            <SelectTrigger className="w-full text-xs">
                              <SelectValue>
                                {timezoneLabel(timezone)}
                              </SelectValue>
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
                          "automation.nextRunPreview" as Parameters<
                            typeof t
                          >[0],
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
                        min={
                          intervalUnit === "seconds" ? MIN_INTERVAL_SECONDS : 1
                        }
                        value={intervalValue}
                        onChange={(e) => {
                          const v = Number.parseInt(e.target.value, 10);
                          if (Number.isFinite(v)) setIntervalValue(v);
                        }}
                        className="flex-1"
                      />
                      <Select
                        value={intervalUnit}
                        onValueChange={(v) =>
                          setIntervalUnit(v as IntervalUnit)
                        }
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
                        : t(
                            "automation.intervalEvery" as Parameters<
                              typeof t
                            >[0],
                            {
                              seconds: intervalSeconds,
                              min: MIN_INTERVAL_SECONDS,
                            },
                          )}
                    </p>
                  </TabsContent>
                  <TabsContent value="manual" className="pt-3">
                    <p className="text-xs leading-5 text-ink-meta">
                      {t("automation.manualHint" as Parameters<typeof t>[0])}
                    </p>
                  </TabsContent>
                </Tabs>
              </FormField>
            </div>

            <DialogFooter className="px-[18px] pt-1 pb-4">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("common.cancel" as Parameters<typeof t>[0])}
              </Button>
              <Button onClick={handleSubmit} disabled={submitDisabled}>
                {t("common.save" as Parameters<typeof t>[0])}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
};
