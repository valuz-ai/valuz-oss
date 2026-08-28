import type { ReactElement } from "react";
import type {
  SessionMessageHostRef,
  Trigger,
  useTranslation,
} from "@valuz/core";
import type { PlanSubtask } from "@valuz/core";
import type { I18nKey } from "@valuz/shared";
import { extractToolOutputJson } from "../conversation-plan-anchors";

/**
 * Which product host a ``generate_ui`` run belongs to — or ``null`` for a
 * plain in-conversation visual.
 *
 * This mirrors the server's ``modules/genui/tools.py::_parse_target_host``
 * EXACTLY, and the order is the whole point: the ``target_host`` tool argument
 * is an OVERRIDE, not the source of truth. Asking the model to copy the host
 * out of its context into an argument is probabilistic — it forgets. The
 * server then still binds the result to the turn's ``host_ref``, so a client
 * that trusted the argument alone would paint the page inline for a user who
 * is looking at that very workbench, and the edition's mirror slot would never
 * fire. ``hostRef`` — this panel IS the host — is the deterministic floor.
 */
export function resolveGenUiHost(
  input: string | undefined,
  hostRef: SessionMessageHostRef | null | undefined,
): SessionMessageHostRef | null {
  if (input) {
    try {
      const parsed: unknown = JSON.parse(input);
      if (parsed && typeof parsed === "object") {
        const target = (parsed as Record<string, unknown>).target_host;
        if (target && typeof target === "object") {
          const record = target as Record<string, unknown>;
          const hostType =
            typeof record.host_type === "string" ? record.host_type : "";
          const hostId =
            typeof record.host_id === "string" ? record.host_id : "";
          // A half-formed argument is not an override — fall through to the
          // panel rather than mirroring into a host that cannot be addressed.
          if (hostType && hostId) {
            return {
              host_type: hostType,
              host_id: hostId,
              slot:
                typeof record.slot === "string" && record.slot
                  ? record.slot
                  : "main",
            };
          }
        }
      }
    } catch {
      // Streaming input is not valid JSON until it closes — fall through.
    }
  }
  if (!hostRef?.host_type || !hostRef.host_id) return null;
  return {
    host_type: hostRef.host_type,
    host_id: hostRef.host_id,
    slot: hostRef.slot || "main",
  };
}

/** True while a workflow snapshot's status denotes an in-flight run (vs a
 *  terminal ``completed`` / ``killed`` / ``failed`` verb). Used to decide
 *  whether the turn-end safety net should coerce a card to ``completed``. */
export const isWorkflowRunning = (status: string): boolean =>
  status === "running" ||
  status === "active" ||
  status === "queued" ||
  status === "pending";

/**
 * True when a tool title refers to *tool* regardless of how the runtime
 * namespaces MCP tools: bare ("automation"), Claude-style
 * ("mcp__valuz_automations__automation"), or slash-style
 * ("valuz_automations/automation" — the codex runtime; verified live).
 * The old `__`-suffix-only checks silently dropped every special card
 * (automation proposal, create_task, AskUserQuestion, …) back to the
 * generic tool renderer on slash-namespacing runtimes.
 */
export function isToolNamed(title: unknown, tool: string): boolean {
  if (typeof title !== "string" || !title) return false;
  return (
    title === tool || title.endsWith(`__${tool}`) || title.endsWith(`/${tool}`)
  );
}

// ── VALUZ-CHATPLAN S3 helpers ────────────────────────────────────────────

/** Compact one-line status pill for chatplan tool results. Each pill is a
 *  pure timeline anchor — the canonical "current state" view lives in the
 *  ``LiveTaskCard`` mounted at the task's first reference, which mutates
 *  in place via SSE. Handles draft_task / plan_task / modify_plan /
 *  commit_task / abandon_task / inject_into_task. */
export function renderChatplanStatusPill(
  name: string,
  tool: { input?: string; output?: string },
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
    params?: Record<string, string | number>,
  ) => string,
  navigate: (path: string) => void,
): ReactElement | null {
  const matches = (k: string) => name === k || name.endsWith(`__${k}`);
  const isDraft = matches("draft_task");
  const isPlan = matches("plan_task");
  const isModify = matches("modify_plan");
  const isCommit = matches("commit_task");
  const isAbandon = matches("abandon_task");
  const isInject = matches("inject_into_task");
  if (!isDraft && !isPlan && !isModify && !isCommit && !isAbandon && !isInject)
    return null;
  if (!tool.output) return null;

  const output = extractToolOutputJson(tool.output) as {
    task_id?: string;
    title?: string;
    status?: string;
    delivered?: boolean;
    reason?: string;
    current_version?: number;
    subtasks?: PlanSubtask[];
    error?: string;
  } | null;
  if (!output) return null;
  if (output.error) return null;
  // ``plan_task`` / ``modify_plan`` responses don't echo task_id (they
  // return only ``{subtasks, ready, current_version}``); the id lives in
  // the tool input. Fall through to a tool-input parse when missing.
  let taskId = output.task_id;
  if (!taskId && tool.input) {
    const inputJson = extractToolOutputJson(tool.input) as {
      task_id?: string;
    } | null;
    taskId = inputJson?.task_id;
  }
  if (!taskId) return null;

  // Per-type accent: a colored dot + matching ring on hover. Keeps the
  // timeline scannable at a glance — green for go, rose for stop, etc.
  let icon = "";
  let label = "";
  let accent: "indigo" | "emerald" | "rose" | "amber" | "slate" = "slate";
  if (isDraft) {
    icon = "📝";
    label = t("conversation.pillDrafted" as I18nKey);
    accent = "indigo";
  } else if (isPlan) {
    icon = "📋";
    label = t("conversation.pillPlanned" as I18nKey, undefined, {
      version: output.current_version ?? 0,
      count: Array.isArray(output.subtasks) ? output.subtasks.length : 0,
    });
    accent = "indigo";
  } else if (isModify) {
    icon = "✏";
    label = t("conversation.pillModified" as I18nKey, undefined, {
      version: output.current_version ?? 0,
      count: Array.isArray(output.subtasks) ? output.subtasks.length : 0,
    });
    accent = "indigo";
  } else if (isCommit) {
    icon = "▶";
    label = t("conversation.pillCommitted" as I18nKey);
    accent = "emerald";
  } else if (isAbandon) {
    icon = "✕";
    label = t("conversation.pillAbandoned" as I18nKey);
    accent = "rose";
  } else if (isInject) {
    if (output.delivered) {
      icon = "💬";
      label = t("conversation.pillInjected" as I18nKey);
      accent = "amber";
    } else {
      icon = "⚠";
      label = t("conversation.pillInjectFailed" as I18nKey, undefined, {
        reason: output.reason ?? "unknown",
      });
      accent = "rose";
    }
  }

  const accentDot: Record<typeof accent, string> = {
    indigo: "bg-brand",
    emerald: "bg-success",
    rose: "bg-rose-500",
    amber: "bg-warning",
    slate: "bg-ink-muted",
  };

  return (
    <div className="group flex items-center gap-3 rounded-lg border border-surface-border bg-surface px-3.5 py-2 text-sm shadow-sm transition-colors hover:border-surface-border-strong hover:bg-surface-soft">
      <span
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-base ${accentDot[accent]}/10`}
        aria-hidden
      >
        <span className="leading-none">{icon}</span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium text-ink-heading">{label}</span>
        {output.title && (
          <span className="truncate text-xs text-ink-muted">
            {output.title}
          </span>
        )}
      </div>
      <button
        type="button"
        className="shrink-0 rounded-md border border-surface-border bg-surface px-2.5 py-1 text-xs text-ink-body transition-colors hover:border-brand/40 hover:bg-brand/5 hover:text-ink-heading"
        onClick={() => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
      >
        {t("conversation.openTask" as I18nKey)}
      </button>
    </div>
  );
}

/**
 * Normalize current and recorded legacy Automation trigger shapes at the
 * conversation boundary. Older proposals used ``{ cron: "..." }``; replaying
 * one directly into the current confirm endpoint produces a discriminator 422
 * because today's contract requires ``kind``.
 */
export function normalizeAutomationTrigger(value: unknown): Trigger | null {
  if (typeof value === "string") {
    try {
      return normalizeAutomationTrigger(JSON.parse(value));
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object") return null;

  const trigger = value as Record<string, unknown>;
  const cronExpr =
    typeof trigger.cron_expr === "string"
      ? trigger.cron_expr.trim()
      : typeof trigger.cron === "string"
        ? trigger.cron.trim()
        : "";
  if ((trigger.kind === "cron" || !trigger.kind) && cronExpr) {
    return {
      kind: "cron",
      cron_expr: cronExpr,
      timezone:
        typeof trigger.timezone === "string" && trigger.timezone.trim()
          ? trigger.timezone
          : null,
    };
  }

  const seconds =
    typeof trigger.seconds === "number"
      ? trigger.seconds
      : typeof trigger.interval_seconds === "number"
        ? trigger.interval_seconds
        : typeof trigger.interval === "number"
          ? trigger.interval
          : null;
  if (
    (trigger.kind === "interval" || !trigger.kind) &&
    seconds !== null &&
    Number.isFinite(seconds)
  ) {
    return { kind: "interval", seconds };
  }

  return trigger.kind === "manual" ? { kind: "manual" } : null;
}

/**
 * Parse an ``automation`` tool call's INPUT into a create spec, or null if it
 * isn't a ``create`` action. ``create`` is the only action that renders a
 * propose→confirm card (others render ``AutomationToolCard``).
 *
 * We render the card from the input (not the tool output) because the output is
 * runtime-dependent: the Valuz/DeepAgents (LangChain) runtime wraps it in a
 * content envelope that isn't bare JSON, so ``parseAutomationToolOutput``
 * returns null there. The input is always clean — same reason ``AgentProposalCard``
 * renders from input. Note ``trigger`` may arrive as a JSON *string* (the model
 * sometimes stringifies it), so we parse it back into the discriminated union.
 */
export function parseAutomationCreateInput(input: unknown): {
  name: string;
  prompt_template: string;
  trigger: Trigger | null;
  agent_slug?: string;
  action_kind?: "chat" | "task";
  worktree?: boolean;
  playbook_definition_id?: string;
  playbook_version?: number;
} | null {
  if (!input) return null;
  let parsed: unknown;
  try {
    parsed = typeof input === "string" ? JSON.parse(input) : input;
  } catch {
    return null;
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    (parsed as { action?: unknown }).action !== "create"
  ) {
    return null;
  }
  const p = parsed as Record<string, unknown>;
  const trigger = normalizeAutomationTrigger(p.trigger ?? null);
  const actionKind =
    p.action_kind === "task"
      ? "task"
      : p.action_kind === "chat"
        ? "chat"
        : undefined;
  // On envelope-wrapping runtimes (codex, DeepAgents) the tool OUTPUT parses to
  // null, so the proposal card renders from this INPUT — it must carry the
  // worktree flag or the chip vanishes and confirm silently drops it. Accept
  // the legacy ``task_worktree`` key too so already-recorded tool calls still
  // resolve after the field rename.
  const worktree =
    typeof p.worktree === "boolean"
      ? p.worktree
      : typeof p.task_worktree === "boolean"
        ? p.task_worktree
        : undefined;
  return {
    name: typeof p.name === "string" ? p.name : "",
    prompt_template:
      typeof p.prompt_template === "string" ? p.prompt_template : "",
    trigger,
    agent_slug: typeof p.agent_slug === "string" ? p.agent_slug : undefined,
    action_kind: actionKind,
    worktree,
    playbook_definition_id:
      typeof p.playbook_definition_id === "string"
        ? p.playbook_definition_id
        : undefined,
    playbook_version:
      typeof p.playbook_version === "number" ? p.playbook_version : undefined,
  };
}

/** Localized schedule summary from a trigger — a fallback for when the server's
 *  ``trigger_human_readable`` isn't available (the tool output wasn't parseable
 *  on this runtime). Mirrors the activity/automation list cadence localization
 *  (每 30 分钟 / Every 30 minutes / 手动 / Manual) via the shared
 *  ``automation.intervalEvery*`` / ``triggerManual`` keys. */
export function automationTriggerSummary(
  trigger: Trigger | null,
  t: ReturnType<typeof useTranslation>["t"],
): string | undefined {
  if (!trigger) return undefined;
  const tk = (key: string) => key as Parameters<typeof t>[0];
  if (trigger.kind === "cron") {
    return trigger.timezone
      ? `${trigger.cron_expr} · ${trigger.timezone}`
      : trigger.cron_expr;
  }
  if (trigger.kind === "interval") {
    const s = trigger.seconds;
    if (s % 3600 === 0)
      return t(tk("automation.intervalEveryHours"), { count: s / 3600 });
    if (s % 60 === 0)
      return t(tk("automation.intervalEveryMinutes"), { count: s / 60 });
    return t(tk("automation.intervalEverySeconds"), { count: s });
  }
  return t(tk("automation.triggerManual"));
}

/**
 * The confirm gate for an ``automation`` create proposal card.
 *
 * A proposal is submittable ONLY when the server validated it (``ok: true``
 * with a proposal). Everything else is not: the tool rejected it (``ok:
 * false`` — rejected, show the tool's message), the call failed at the
 * runtime/API layer (``tool.status === "error"`` — rejected, show the
 * failure), or the result hasn't landed / can't be parsed (still running or
 * unknown shape — not rejected, but nothing to confirm either).
 */
export function automationProposalGate(
  result: { ok: boolean; proposal?: unknown } | null,
  toolStatus: string | undefined,
): { rejected: boolean; submittable: boolean } {
  if (toolStatus === "error") return { rejected: true, submittable: false };
  if (result && !result.ok) return { rejected: true, submittable: false };
  return {
    rejected: false,
    submittable: result?.ok === true && result.proposal != null,
  };
}

const HOST_NAME_UNSAFE = /[^A-Za-z0-9._-]+/g;

/**
 * The stable document file name a host slot's pages are recorded under —
 * mirrors the server's ``_document_file_name`` exactly (parts joined with
 * ".", unsafe runs collapsed to "-", trimmed). A tool call whose input
 * mentions this name is page work on the host the panel is showing, and the
 * conversation mirrors it into the workbench like a generation.
 */
export function hostDocumentFileName(hostRef: SessionMessageHostRef): string {
  const parts = [hostRef.host_type, hostRef.host_id, hostRef.slot || "main"];
  const stem = parts
    .filter(Boolean)
    .join(".")
    .replace(HOST_NAME_UNSAFE, "-")
    .replace(/^-+|-+$/g, "");
  return `${stem}.a2ui.jsonl`;
}
