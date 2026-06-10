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

  trigger: Trigger;
  /** Localized "every day at 9" / "every 5 minutes". */
  trigger_human_readable: string;

  status: "enabled" | "paused";
  next_run_at: number | null;
  last_run_at: number | null;
  last_run_status: string | null;
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
  trigger_type: "cron" | "interval" | "manual" | "recovered_skip";
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
  error_message: string | null;
  session_id: string | null;
  created_files: string[];
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
}

export interface AutomationUpdatePayload {
  name?: string | null;
  prompt_template?: string | null;
  trigger?: Trigger | null;
  agent_slug?: string | null;
  action_kind?: ActionKind | null;
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

const fetchJson = createFetchJson(() => _apiBase);

export const automationsApi = {
  listGroups(projectId?: string): Promise<{ groups: AutomationGroup[] }> {
    const qs = new URLSearchParams();
    if (projectId) qs.set("project_id", projectId);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(`/v1/automations${suffix}`);
  },

  listProjectTargets(): Promise<{ targets: AutomationProjectTarget[] }> {
    return fetchJson(`/v1/automations/project-targets`);
  },

  get(automationId: string): Promise<AutomationDetail> {
    return fetchJson(`/v1/automations/${encodeURIComponent(automationId)}`);
  },

  create(payload: AutomationCreatePayload): Promise<AutomationDetail> {
    return fetchJson("/v1/automations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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
    });
  },

  delete(automationId: string): Promise<void> {
    return fetchJson(`/v1/automations/${encodeURIComponent(automationId)}`, {
      method: "DELETE",
    });
  },

  pause(automationId: string): Promise<AutomationDetail> {
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/pause`,
      { method: "POST" },
    );
  },

  resume(automationId: string): Promise<AutomationDetail> {
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/resume`,
      { method: "POST" },
    );
  },

  runNow(automationId: string): Promise<AutomationRunAccepted> {
    return fetchJson(
      `/v1/automations/${encodeURIComponent(automationId)}/run-now`,
      { method: "POST" },
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
};
