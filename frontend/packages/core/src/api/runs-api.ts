import { createFetchJson } from "./fetch-json";
import { fanOutTargets, getListFanOutTargets } from "../edition/list-fanout";
import { recordEntityOrigins } from "../edition/entity-origin";

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
  /** user | automation | task — who created the session. */
  origin: string;
  project_id: string;
  project_name: string | null;
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
  /** True when the session carries a live background task (run_in_background
   * shell command). Such sessions surface in the running view even while no
   * turn is streaming. */
  background?: boolean;
  /** CLIENT-side tag on multi-target editions: which execution target
   * answered the row (e.g. "local"/"cloud"). Never sent by the server. */
  exec_origin?: string;
}

const fetchJson = createFetchJson(() => _apiBase);

type RunsResponse = { runs: RunSummary[] };

// The shell, activity view, and lifecycle stream can all request the same
// snapshot during startup/backfill. Share one request per target-set/status so
// a historical run.finished burst cannot fan out into dozens of identical
// local DB reads and falsely mark the local execution target unreachable.
const inFlightLists = new Map<string, Promise<RunsResponse>>();

export interface RunsListParams {
  status?: "running" | "finished";
  // Scope the recency window to one project. The unscoped window is global and
  // shared with quick chats, so a project whose conversations are older than
  // its tail otherwise reports zero runs — see the sidebar accordion.
  projectId?: string;
  limit?: number;
}

const listRuns = async (params?: RunsListParams): Promise<RunsResponse> => {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.projectId) qs.set("project_id", params.projectId);
  if (params?.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs}` : "";
  // Multi-target editions: fan out + tag ``exec_origin`` + feed the origin
  // index (session / task / project ids all ride on a run row). Zero targets
  // (OSS) keeps the single-backend path unchanged.
  if (getListFanOutTargets().length === 0) {
    return fetchJson(`/v1/runs${suffix}`);
  }
  const outcome = await fanOutTargets((target, signal) =>
    fetchJson<RunsResponse>(`/v1/runs${suffix}`, {
      baseUrl: target.baseUrl,
      signal,
    }),
  );
  const seen = new Set<string>();
  const merged: RunSummary[] = [];
  for (const { target, value } of outcome.values) {
    const entries: Array<[string, string]> = [];
    for (const run of value.runs) {
      entries.push([run.session_id, target.id]);
      if (run.task_id) entries.push([run.task_id, target.id]);
      if (run.project_id) entries.push([run.project_id, target.id]);
    }
    recordEntityOrigins(entries);
    for (const run of value.runs) {
      if (seen.has(run.session_id)) continue;
      seen.add(run.session_id);
      merged.push({ ...run, exec_origin: target.id });
    }
  }
  // Interleave both backends by recency instead of target order.
  merged.sort((a, b) => b.updated_at - a.updated_at);
  return { runs: merged };
};

export const runsApi = {
  list(params?: RunsListParams): Promise<RunsResponse> {
    const targetsKey = getListFanOutTargets()
      .map((target) => `${target.id}:${target.baseUrl}`)
      .join("|");
    // Every query dimension is part of the key — a project-scoped request must
    // never be served the global snapshot (or another project's).
    const key = [
      _apiBase,
      targetsKey,
      params?.status ?? "running",
      params?.projectId ?? "",
      params?.limit ?? "",
    ].join("|");
    const current = inFlightLists.get(key);
    if (current) return current;

    const request = listRuns(params);
    inFlightLists.set(key, request);
    const clear = (): void => {
      if (inFlightLists.get(key) === request) inFlightLists.delete(key);
    };
    request.then(clear, clear);
    return request;
  },
};
