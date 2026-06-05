import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setRunsApiBase = (url: string): void => {
  _apiBase = url;
};

export type RunSourceKind = "assistant" | "project_chat" | "task";

export interface RunTodoSnapshot {
  content: string;
  status: string;
  activeForm: string | null;
}

/** One run in the activity overview = a kernel session classified by source.
 *  Tasks are represented by their lead session; member subtasks don't appear. */
export interface RunSummary {
  session_id: string;
  source_kind: RunSourceKind;
  workspace_id: string;
  workspace_name: string | null;
  task_id: string | null;
  title: string;
  status: string;
  current_todo: RunTodoSnapshot | null;
  last_message: string | null;
  /** Chats: last round's assistant output (truncated) — the run's description. */
  last_output: string | null;
  /** Tasks: latest timeline event; rendered like the task-detail timeline. */
  last_event: { type: string; payload: Record<string, unknown> } | null;
  model: string | null;
  runtime: string | null;
  updated_at: number;
}

const fetchJson = createFetchJson(() => _apiBase);

export const runsApi = {
  list(params?: {
    status?: "running" | "finished";
  }): Promise<{ runs: RunSummary[] }> {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(`/v1/runs${suffix}`);
  },
};
