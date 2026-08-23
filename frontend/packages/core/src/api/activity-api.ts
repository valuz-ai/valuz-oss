import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setActivityApiBase = (url: string): void => {
  _apiBase = url;
};

export type ActivityKind = "chat" | "task" | "playbook";
export type ActivityTab = "all" | "chat" | "task" | "automation" | "playbook";

/** One entry in the unified activity feed — a user chat session or a task
 *  entity (see backend ``modules/activity``). Serves the project-home tabs
 *  (``projectId`` set) and the global 动态 history (``projectId`` omitted). */
export interface ActivityItem {
  kind: ActivityKind;
  /** session id for chats, task id for tasks — drives navigation. */
  id: string;
  title: string;
  /** session status for chats, task status for tasks. */
  status: string;
  is_automation: boolean;
  project_id: string;
  /** null for non-project quick chats. */
  project_name: string | null;
  /** Conversation executing a PlaybookRun, when one exists. */
  linked_session_id: string | null;
  /** Unix epoch ms — interleave key + the value inside the keyset cursor. */
  sort_at: number;
  /** CLIENT-side tag on multi-target editions: which execution target
   * answered the row (e.g. "local"/"cloud"). Never sent by the server. */
  exec_origin?: string;
}

export interface ActivityPage {
  items: ActivityItem[];
  /** Opaque keyset cursor for the next (older) page; null when exhausted. */
  next_cursor: string | null;
}

const fetchJson = createFetchJson(() => _apiBase);

export const activityApi = {
  list(
    params: {
      projectId?: string | null;
      tab?: ActivityTab;
      limit?: number;
      cursor?: string | null;
    },
    opts?: { baseUrl?: string; signal?: AbortSignal },
  ): Promise<ActivityPage> {
    const qs = new URLSearchParams();
    if (params.projectId) qs.set("project_id", params.projectId);
    if (params.tab) qs.set("tab", params.tab);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.cursor) qs.set("cursor", params.cursor);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(`/v1/activity${suffix}`, {
      baseUrl: opts?.baseUrl,
      signal: opts?.signal,
    });
  },
};
