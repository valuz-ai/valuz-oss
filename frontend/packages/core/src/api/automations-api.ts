/**
 * Automation API client.
 *
 * Replaces `schedules-api.ts` per ADR-021. Same overall shape (list /
 * detail / create / update / pause / resume / run-now / runs +
 * project-targets and trigger validation) with two visible differences:
 *
 * 1. `Trigger` is a discriminated union — cron / interval / manual
 *    rather than a flat `cron_expr` field.
 * 2. Execution identity comes from a bound agent (`agent_kind` +
 *    `agent_slug`), so the legacy `(model_id, provider_id, runtime_id)`
 *    triple is gone.
 */

import { createFetchJson } from "./fetch-json";
import { resolveApiBase } from "./base-resolver";
import { fanOutTargets, getListFanOutTargets } from "../edition/list-fanout";
import { recordEntityOrigins } from "../edition/entity-origin";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setAutomationsApiBase = (url: string): void => {
  _apiBase = url;
};

// ── Trigger discriminated union ────────────────────────────────────

export interface CronTrigger {
  kind: "cron";
  /** Standard 5-field POSIX cron. */
  cron_expr: string;
  /** IANA name; null = follow user default. */
  timezone: string | null;
}

export interface IntervalTrigger {
  kind: "interval";
  /** Seconds. Minimum 30 (tick interval). */
  seconds: number;
}

export interface ManualTrigger {
  kind: "manual";
}

export type Trigger = CronTrigger | IntervalTrigger | ManualTrigger;

/**
 * Agent reference kind. `project_member` references a member already
 * instantiated into a project project. `library_agent` references an
 * agent in the global LIBRARY/Agents — the backend instantiates it into
 * the bound chat project at create time and stores the row as
 * project_member; the kind is preserved on the row for display.
 */
export type AgentKind = "project_member" | "library_agent";

/**
 * Execution mode at fire time.
 *
 * - ``chat`` — single agent run. The rendered prompt drives one session
 *   + send. Original schedule semantic; valid on any project.
 * - ``task`` — kick off a project task with the bound agent as Lead.
 *   The rendered prompt becomes the task goal; the lead plans + dispatches
 *   sub-members. Only valid on project projects (backend rejects
 *   ``task`` on chat projects).
 */
export type ActionKind = "chat" | "task";

// ── List + detail ──────────────────────────────────────────────────

export interface AutomationItem {
  automation_id: string;
  project_id: string;
  project_name: string;
  project_kind: "chat" | "project";

  name: string;
  agent_kind: AgentKind;
  agent_slug: string;
  /** Resolved name of the bound agent; null when upstream agent deleted. */
  agent_name: string | null;
  /** Execution mode — drives the UI badge + edit-dialog default Tab. */
  action_kind: ActionKind;
  /** Worktree isolation flag (both action kinds; git-repo projects only). */
  worktree: boolean;
  /** Optional immutable Playbook contract executed by each fire. */
  playbook_definition_id: string | null;
  playbook_version: number | null;

  trigger: Trigger;
  /** Localized "every day at 9" / "every 5 minutes". */
  trigger_human_readable: string;

  status: "enabled" | "paused";
  next_run_at: number | null;
  last_run_at: number | null;
  last_run_status: string | null;
  /** CLIENT-side tag on multi-target editions: which execution target
   * answered the list row (e.g. "local"/"cloud"). Never sent by the server;
   * absent on single-backend builds. */
  exec_origin?: string;
}

export interface AutomationGroup {
  project_id: string;
  project_name: string;
  project_kind: "chat" | "project";
  automations: AutomationItem[];
}

export interface AutomationDetail extends AutomationItem {
  prompt_template: string;
  total_runs: number;
  recent_failures: number;
  created_at: number;
  updated_at: number;
}

export interface AutomationRunItem {
  run_id: string;
  automation_id: string;
  project_id: string;
  trigger_type: "cron" | "interval" | "manual" | "agent" | "recovered_skip";
  status:
    | "queued"
    | "running"
    | "success"
    | "failed"
    | "skipped"
    | "interrupted_by_shutdown";
  triggered_at: number;
  started_at: number | null;
  completed_at: number | null;
  duration_ms: number | null;
  result_summary: string | null;
  error_code: string | null;
  error_message_key: string | null;
  error_message: string | null;
  session_id: string | null;
  created_files: string[];
  /** Canonical PlaybookRun created for this fire, when a Playbook is pinned. */
  playbook_run_id: string | null;
  // The task this run kicked off (task automations only) — id + title deep-link
  // to it ("→ 任务《title》"). `null` for non-task runs.
  task_id: string | null;
  task_title: string | null;
  // Live status of that task. `status` freezes to `success` at kickoff; prefer
  // this for the badge when present. Resolved from the lead session's task at
  // read time. `null` for non-task runs.
  task_status: "active" | "completed" | "failed" | "paused" | null;
}

// ── Trigger validation helpers ─────────────────────────────────────

/**
 * Same shape as the schedule-era `CronValidationResult` — exported with a
 * distinct name so the star-export in `index.ts` doesn't conflict during
 * the S5 transition where both API clients coexist.
 */
export interface AutomationCronValidationResult {
  valid: boolean;
  human_readable: string | null;
  next_runs: string[];
  error_message: string | null;
}

export interface IntervalValidationResult {
  valid: boolean;
  human_readable: string | null;
  error_message: string | null;
}

// ── Create / Update ────────────────────────────────────────────────

export interface AutomationCreatePayload {
  name: string;
  /**
   * Project routing:
   *
   * - `project_kind="chat"` + `project_id=null` → backend
   *   lazy-creates a fresh chat project named after the automation.
   *   Automation page "Chat" picker submits this shape.
   * - `project_kind="chat"` + `project_id=<id>` → bind to that
   *   chat project. MCP-from-chat path.
   * - `project_kind="project"` + `project_id=<id>` → bind to the
   *   project. `project_id` is required for project payloads.
   */
  project_kind: "chat" | "project";
  project_id: string | null;

  agent_kind: AgentKind;
  agent_slug: string;

  prompt_template: string;

  trigger: Trigger;

  /**
   * Execution mode. Optional — backend defaults to `"chat"` if omitted.
   * `"task"` is only valid for project projects; the backend rejects
   * (422 ``AutomationTaskOnlyOnProject``) the combination on chat.
   */
  action_kind?: ActionKind;
  /**
   * Worktree isolation (both action kinds; requires a git-repo project):
   * `"chat"` runs each fire in its own git worktree, `"task"` runs lead +
   * every member in one worktree. Silently dropped for chat-only projects.
   */
  worktree?: boolean;
  /** Definition + exact immutable version. Omitting the version asks the
   * backend to resolve and persist the Definition's current version once. */
  playbook_definition_id?: string | null;
  playbook_version?: number | null;
}

export interface AutomationUpdatePayload {
  name?: string | null;
  prompt_template?: string | null;
  trigger?: Trigger | null;
  agent_slug?: string | null;
  action_kind?: ActionKind | null;
  worktree?: boolean | null;
  playbook_definition_id?: string | null;
  playbook_version?: number | null;
}

/** Minimal Definition projection needed by the Automation contract picker. */
export interface AutomationPlaybookChoice {
  id: string;
  project_id: string;
  name: string;
  status: "draft" | "active" | "retired";
  current_version: number;
  revision: number;
}

export interface AutomationRunAccepted {
  run_id: string;
  automation_id: string;
  status: "queued" | "running";
}

/**
 * One option in the project picker on the automation-create form. The
 * list is fully composed on the backend (Chat sentinel + project rows
 * only) — see `AutomationService.list_project_targets`. The frontend
 * renders it verbatim.
 *
 * `id` is the stable identifier used as the React key (`chat-default`
 * for the sentinel; project_id for projects). `project_id` is what
 * goes into the create payload — `null` for the Chat sentinel triggers
 * backend lazy-create; the real id for projects.
 */
export interface AutomationProjectTarget {
  id: string;
  name: string;
  kind: "chat" | "project";
  project_id: string | null;
}

// ── Proposal (create → confirm card) ───────────────────────────────

/**
 * Resolved-but-unsaved automation echoed by the ``automation create`` tool
 * (``AutomationToolResult.proposal``). The confirmation card renders this and
 * replays the confirmable fields to ``confirmProposal``.
 */
export interface AutomationProposalSpec {
  name: string;
  prompt_template: string;
  trigger: Trigger;
  agent_slug: string;
  agent_kind: AgentKind;
  agent_name: string | null;
  action_kind: ActionKind;
  /** Worktree isolation (both action kinds; git-repo projects only). */
  worktree: boolean;
  playbook_definition_id: string | null;
  playbook_version: number | null;
  trigger_human_readable: string;
  next_run_at: number | null;
}

/** Body for confirming an automation proposal. ``tool_call_id`` is the kernel
 *  ``tool_use`` id of the proposing call (persisted for re-entry detection). */
export interface AutomationProposalConfirmPayload {
  tool_call_id: string;
  name: string;
  prompt_template: string;
  trigger: Trigger;
  agent_slug?: string | null;
  action_kind?: ActionKind;
  worktree?: boolean;
  playbook_definition_id?: string | null;
  playbook_version?: number | null;
}

export interface AutomationProposalStatusResult {
  confirmed: Record<string, { automation_id: string }>;
}

const fetchJson = createFetchJson(() => _apiBase);
const automationBase = (automationId: string): string | undefined =>
  resolveApiBase({ automationId }, "") || undefined;
const sessionBase = (sessionId: string): string | undefined =>
  resolveApiBase({ sessionId }, "") || undefined;

export const automationsApi = {
  /** List Playbook Definitions on the same execution target as the future
   * Automation. Definition ownership and execution Project may differ; this
   * intentionally does not filter by project_id. */
  listPlaybooks(opts?: {
    baseUrl?: string;
  }): Promise<AutomationPlaybookChoice[]> {
    return fetchJson("/v1/playbooks", { baseUrl: opts?.baseUrl });
  },

  async listGroups(projectId?: string): Promise<{ groups: AutomationGroup[] }> {
    const qs = new URLSearchParams();
    if (projectId) qs.set("project_id", projectId);
    const suffix = qs.toString() ? `?${qs}` : "";
    // Project-scoped list follows the project's execution origin.
    if (projectId) {
      return fetchJson(`/v1/automations${suffix}`, {
        baseUrl: resolveApiBase({ projectId }, "") || undefined,
      });
    }
    // Global list: fan out to every registered target on multi-target
    // editions, tag each automation's ``exec_origin``, and feed the origin
    // index so automation-scoped calls route to the owning backend. Zero
    // targets (OSS) keeps the single-backend path unchanged. A project lives
    // on exactly one backend, so groups never overlap across targets — a flat
    // concat is the merge.
    if (getListFanOutTargets().length === 0) {
      return fetchJson(`/v1/automations${suffix}`);
    }
    const outcome = await fanOutTargets((target) =>
      fetchJson<{ groups: AutomationGroup[] }>(`/v1/automations`, {
        baseUrl: target.baseUrl,
      }),
    );
    recordEntityOrigins(
      outcome.values.flatMap(({ target, value }) =>
        value.groups.flatMap((g) =>
          g.automations.map(
            (a) => [a.automation_id, target.id] as [string, string],
          ),
        ),
      ),
    );
    const merged: AutomationGroup[] = [];
    for (const { target, value } of outcome.values) {
      for (const g of value.groups) {
        merged.push({
          ...g,
          automations: g.automations.map((a) => ({
            ...a,
            exec_origin: target.id,
          })),
        });
      }
    }
    return { groups: merged };
  },

  async listProjectTargets(): Promise<{ targets: AutomationProjectTarget[] }> {
    // The target picker must show BOTH backends' projects on multi-target
    // editions. The Chat sentinel ("chat-default", project_id=null) is
    // returned by every backend — keep only the first; its backend is chosen
    // by the location picker at create time, not by which backend listed it.
    if (getListFanOutTargets().length === 0) {
      return fetchJson(`/v1/automations/project-targets`);
    }
    const outcome = await fanOutTargets((target) =>
      fetchJson<{ targets: AutomationProjectTarget[] }>(
        `/v1/automations/project-targets`,
        { baseUrl: target.baseUrl },
      ),
    );
    recordEntityOrigins(
      outcome.values.flatMap(({ target, value }) =>
        value.targets
          .filter((t) => t.project_id)
          .map((t) => [t.project_id!, target.id] as [string, string]),
      ),
    );
    const seen = new Set<string>();
    const merged: AutomationProjectTarget[] = [];
    for (const { value } of outcome.values) {
      for (const t of value.targets) {
        if (seen.has(t.id)) continue;
        seen.add(t.id);
        merged.push(t);
      }
    }
    return { targets: merged };
  },

  get(automationId: string): Promise<AutomationDetail> {
    return fetchJson(`/v1/automations/${encodeURIComponent(automationId)}`, {
      baseUrl: automationBase(automationId),
    });
  },

  create(
    payload: AutomationCreatePayload,
    opts?: { baseUrl?: string },
  ): Promise<AutomationDetail> {
    return fetchJson("/v1/automations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: opts?.baseUrl,
    });
  },

  update(
    automationId: string,
    payload: AutomationUpdatePayload,
  ): Promise<AutomationDetail> {
    return fetchJson(`/v1/automations/${encodeURIComponent(automationId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: automationBase(automationId),
    });
  },

  delete(automationId: string): Promise<void> {
    return fetchJson(`/v1/automations/${encodeURIComponent(automationId)}`, {
      method: "DELETE",
      baseUrl: automationBase(automationId),
    });
  },

  pause(automationId: string): Promise<AutomationDetail> {
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/pause`,
      { method: "POST", baseUrl: automationBase(automationId) },
    );
  },

  resume(automationId: string): Promise<AutomationDetail> {
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/resume`,
      { method: "POST", baseUrl: automationBase(automationId) },
    );
  },

  runNow(automationId: string): Promise<AutomationRunAccepted> {
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/run-now`,
      { method: "POST", baseUrl: automationBase(automationId) },
    );
  },

  listRuns(
    automationId: string,
    limit?: number,
    cursor?: string,
  ): Promise<{ runs: AutomationRunItem[] }> {
    const qs = new URLSearchParams();
    if (limit) qs.set("limit", String(limit));
    if (cursor) qs.set("cursor", cursor);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/runs${suffix}`,
      { baseUrl: automationBase(automationId) },
    );
  },

  validateCron(
    expr: string,
    timezone?: string,
  ): Promise<AutomationCronValidationResult> {
    return fetchJson("/v1/automations/validate-cron", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expr, timezone }),
    });
  },

  validateInterval(seconds: number): Promise<IntervalValidationResult> {
    return fetchJson("/v1/automations/validate-interval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds }),
    });
  },

  /** Confirm an automation the ``create`` tool proposed (the user clicked
   *  "Create" on the proposal card). Persists + returns the created row.
   *  Routes to the backend that owns the proposing session. */
  confirmProposal(
    sessionId: string,
    payload: AutomationProposalConfirmPayload,
  ): Promise<AutomationItem> {
    return fetchJson(
      `/v1/automations/proposals/${encodeURIComponent(sessionId)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: sessionBase(sessionId),
      },
    );
  },

  /** Map proposing ``tool_call_id``s → already-created automations, so the page
   *  can seed confirmed proposal cards on session re-entry. Routes to the
   *  backend that owns the proposing session. */
  proposalStatus(
    sessionId: string,
    toolCallIds: string[],
  ): Promise<AutomationProposalStatusResult> {
    return fetchJson(
      `/v1/automations/proposals/${encodeURIComponent(sessionId)}/status`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool_call_ids: toolCallIds }),
        baseUrl: sessionBase(sessionId),
      },
    );
  },
};
