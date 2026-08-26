import { resolveApiBase } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setTasksApiBase = (url: string): void => {
  _apiBase = url;
};

/** Resolved "who/what spawned this task" — drives the task-list "由 … 触发" line.
 * The source_* ids let the UI deep-link to the parent task / automation /
 * conversation; the resolved names spare a second lookup. */
export interface TaskTrigger {
  /** user | chat | agent | automation */
  type: string;
  source_task_id?: string | null;
  source_task_title?: string | null;
  source_agent_slug?: string | null;
  source_automation_id?: string | null;
  source_automation_name?: string | null;
  source_session_id?: string | null;
}

/**
 * The task lifecycle states, mirroring the backend's `task_state.TASK_STATUSES`.
 *
 * Safe to state as a closed union because the backend ENFORCES it:
 * `TaskDatastore.update_task_status` raises `TaskStateError` rather than
 * persist a value outside the enum, and the legal transitions between them are
 * a state machine there. So `status` is one of these or the write never
 * happened.
 */
export type TaskStatus =
  | "draft"
  | "active"
  | "paused"
  | "stopped"
  | "completed"
  | "blocked"
  | "abandoned"
  /**
   * LEGACY, read-only. Task-level failure was folded into `blocked` before
   * this enum existed; `update_task_status` now refuses to write `failed`, but
   * rows created earlier still carry it and the backend keeps handling them
   * (`resume_task` accepts it as a resumable prior status). So it can arrive
   * on a read even though nothing produces it any more — the union describes
   * what the server can SEND.
   */
  | "failed";

/**
 * Task timeline event types.
 *
 * Deliberately NOT a closed union on the wire — see `TaskEvent.type`. The
 * backend column is a plain string with no enum behind it, and the vocabulary
 * grows (`subtask_reported`, `awaiting_user` and `user_answered` were all added
 * recently). This union names the ones a client handles today so a `switch`
 * gets autocomplete and a typo is caught; it is not a claim about what the
 * server can send.
 */
export type TaskEventType =
  | "kickoff"
  | "kickoff_failed"
  | "task_drafted"
  | "task_planned"
  | "plan_revised"
  | "task_plan_update"
  | "committed"
  | "abandoned"
  | "subtask_spawned"
  | "subtask_completed"
  | "subtask_failed"
  | "subtask_stopped"
  | "subtask_reviewed"
  | "subtask_message"
  | "subtask_reported"
  | "user_note"
  | "user_inject"
  | "user_inject_dropped"
  | "goal_revised"
  | "awaiting_user"
  | "user_answered"
  | "paused"
  | "resumed"
  | "stopped"
  | "task_completed"
  | "task_stopped"
  | "task_blocked"
  | "deliverable_updated";

/** Durable header for a lead-dispatch task. */
export interface Task {
  id: string;
  project_id: string;
  title: string;
  goal: string;
  status: TaskStatus;
  created_by: string;
  lead_agent_slug: string;
  current_holder: string;
  file_path: string;
  /** Unix epoch milliseconds (UTC); format via ``new Date(ms)``.
   * ``updated_at`` powers the sidebar TASKS section ordering
   * ("active just now" vs "completed yesterday"). */
  created_at: number;
  updated_at: number;
  /** Trigger provenance, resolved server-side. ``null`` for legacy tasks. */
  trigger?: TaskTrigger | null;
}

/** One kernel session that belongs to a task (lead or dispatched subtask). */
export interface TaskRun {
  id: string;
  session_id: string;
  agent_slug: string;
  sequence: number;
  /** lead | subtask */
  kind: string;
  status: string;
  label: string | null;
  goal: string | null;
  dispatched_by: string | null;
  project_mode: string;
  run_dir: string | null;
  /** {summary, artifacts, status} — populated when the run completes. */
  result_manifest: Record<string, unknown> | null;
}

/** One entry in the task's append-only event log. */
export interface TaskEvent {
  id: string;
  sequence: number;
  /**
   * Known types get autocomplete; unknown ones still parse.
   *
   * `(string & {})` keeps the field assignable from any server string while
   * preserving the union's suggestions — narrowing it outright would assert an
   * enum the backend does not have, so a newly added event type would become a
   * compile error on data that is perfectly valid.
   */
  type: TaskEventType | (string & {});
  /** user | <agent_slug> | system */
  actor: string;
  session_id: string | null;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface TaskDetail {
  task: Task;
  runs: TaskRun[];
  events: TaskEvent[];
}

export interface TaskRunTokenUsage {
  session_id: string;
  agent_slug: string;
  kind: string;
  sequence: number;
  label: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
}

export interface TaskTokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
  runs: TaskRunTokenUsage[];
}

export interface KickoffTaskPayload {
  goal: string;
  lead_agent_slug: string;
  refs?: string[] | null;
  title?: string | null;
  /**
   * Task-level worktree isolation: the whole task (lead + every member)
   * runs in ONE git worktree of the project repo. A clean worktree is
   * removed when the task finishes; one with work left surfaces in the
   * project's worktrees panel. Requires a git-repo project (400 otherwise).
   */
  worktree?: boolean;
}

export interface IntervenePayload {
  action: "note" | "revise_goal" | "pause" | "resume" | "stop";
  text?: string | null;
  goal?: string | null;
}

// ---- VALUZ-CHATPLAN S3 — draft / commit / abandon / inject / plan -----

/** One subtask as rendered by the Plan Card and Todo panel — matches
 * ``TaskPlan.to_panel()`` on the backend. */
export interface PlanSubtask {
  key: string;
  label: string;
  agent: string;
  /** Panel vocabulary — what ``to_panel()`` puts on the wire:
   * pending | active | completed | failed | paused. NOT the internal node
   * status (planned/in_progress/in_review/rework/done). */
  status: string;
  depends_on: string[];
  parallel_group: string | null;
  goal: string | null;
  attempts: number;
  review_criteria: string | null;
  review_feedback: string | null;
}

export interface PlanResponse {
  subtasks: PlanSubtask[];
  ready: string[];
  counts?: Record<string, number>;
  all_done?: boolean;
  current_version: number;
}

export interface DraftTaskPayload {
  goal: string;
  lead_agent_slug: string;
  originating_session_id: string;
  refs?: string[] | null;
  title?: string | null;
}

export interface DraftTaskResponse {
  task_id: string;
  status: string;
  plan_version: number;
  title: string;
  lead_agent_slug: string;
}

export interface CommitTaskPayload {
  caller_session_id: string;
  lead_agent_slug?: string | null;
}

export interface CommitTaskResponse {
  task_id: string;
  lead_session_id: string;
  status: string;
  committed_at: number;
}

export interface AbandonTaskPayload {
  caller_session_id: string;
  reason?: string | null;
}

export interface InjectTaskPayload {
  text: string;
  from_session_id: string;
}

export interface InjectTaskResponse {
  delivered: boolean;
  lead_session_id?: string | null;
  reason?: string | null;
}

export interface PlanWritePayload {
  /** Initial plan creation (POST /plan). */
  subtasks?: Array<Record<string, unknown>>;
  /** Patch operations (PATCH /plan). */
  add?: Array<Record<string, unknown>>;
  update?: Array<Record<string, unknown>>;
  /** CAS token from the last ``current_version`` read. Required for
   * multi-writer (chat) callers; optional for single-writer (lead). */
  expected_version?: number;
}

const fetchJson = createFetchJson(() => _apiBase);

const projectBase = (projectId: string): string =>
  resolveApiBase({ projectId }, _apiBase);
const taskBase = (taskId: string): string =>
  resolveApiBase({ taskId }, _apiBase);

export const tasksApi = {
  kickoff(projectId: string, payload: KickoffTaskPayload): Promise<Task> {
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: projectBase(projectId),
    });
  },

  listTasks(
    projectId: string,
    init?: { signal?: AbortSignal },
  ): Promise<{ tasks: Task[] }> {
    // ``init`` (e.g. an ``AbortSignal`` for the project-detail auto-refresh
    // poller) is forwarded to ``fetchJson`` → ``fetch``. Existing callers pass
    // nothing, so their behaviour is unchanged.
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}/tasks`, {
      ...init,
      baseUrl: projectBase(projectId),
    });
  },

  /** Global cross-project task list, newest activity first. Backs the
   * sidebar TASKS section so users see what's running regardless of
   * which project page they're on. */
  listAllTasks(limit = 50): Promise<{ tasks: Task[] }> {
    return fetchJson(`/v1/tasks?limit=${encodeURIComponent(limit)}`);
  },

  getTask(taskId: string): Promise<TaskDetail> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}`, {
      baseUrl: taskBase(taskId),
    });
  },

  getTaskUsage(taskId: string): Promise<TaskTokenUsage> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/usage`, {
      baseUrl: taskBase(taskId),
    });
  },

  listEvents(taskId: string): Promise<{ events: TaskEvent[] }> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/events`, {
      baseUrl: taskBase(taskId),
    });
  },

  /** SSE endpoint URL for a task's event timeline. Subscribers connect with
   * ``fetchEventSource(() => eventsStreamUrl(id, lastSeq), …)`` and remember the
   * last received ``sequence`` so a reconnect resumes from the cursor (cursor
   * is monotonic per task — no gaps possible). ``keepAlive`` asks the server
   * not to terminal-close the stream of a finished task (``stream_end``). */
  eventsStreamUrl(taskId: string, afterSeq = 0, keepAlive = false): string {
    const params = new URLSearchParams();
    if (afterSeq > 0) params.set("after_seq", String(afterSeq));
    if (keepAlive) params.set("keep_alive", "1");
    const qs = params.toString();
    return `${taskBase(taskId)}/v1/tasks/${encodeURIComponent(taskId)}/events/stream${qs ? `?${qs}` : ""}`;
  },

  intervene(taskId: string, payload: IntervenePayload): Promise<Task> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:intervene`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  // ---- VALUZ-CHATPLAN S3 ----

  /** Open a draft task (status=draft, plan_version=0). No lead session is
   * created; the originating chat session becomes the plan writer. */
  draft(
    projectId: string,
    payload: DraftTaskPayload,
  ): Promise<DraftTaskResponse> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/tasks:draft`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: projectBase(projectId),
      },
    );
  },

  /** Promote a draft task to active by spawning its lead session. */
  commit(
    taskId: string,
    payload: CommitTaskPayload,
  ): Promise<CommitTaskResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Discard a draft task (terminal — cannot be resurrected). */
  abandon(
    taskId: string,
    payload: AbandonTaskPayload,
  ): Promise<{ task_id: string; status: string }> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:abandon`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Push a user instruction into the running lead's mailbox. The lead
   * reads it at the next turn boundary. */
  inject(
    taskId: string,
    payload: InjectTaskPayload,
  ): Promise<InjectTaskResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:inject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Lay down the initial subtask plan. Fails when a plan with progress
   * already exists — call ``modifyPlan`` instead. */
  plan(taskId: string, payload: PlanWritePayload): Promise<PlanResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Patch the plan: add nodes / update existing nodes. CAS-protected via
   * ``expected_version`` — the server returns 409 on conflict. */
  modifyPlan(taskId: string, payload: PlanWritePayload): Promise<PlanResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Read the plan snapshot + ready keys + counts + current_version. */
  getPlan(taskId: string): Promise<PlanResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan`, {
      baseUrl: taskBase(taskId),
    });
  },
};
