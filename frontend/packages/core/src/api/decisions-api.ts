/**
 * Decision Inbox API client (ADR-022).
 *
 * Wraps the backend's two endpoints:
 * - ``GET /v1/decisions/pending`` — REST snapshot
 * - ``GET /v1/decisions/stream`` — SSE incremental stream
 *
 * The SSE stream is consumed by ``useDecisionInbox`` (a singleton hook
 * mounted once at the AppShell layer); components never instantiate
 * EventSource themselves — they read from the Zustand decision-store.
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setDecisionsApiBase = (url: string): void => {
  _apiBase = url;
};

/** Wire shape mirroring backend
 * ``valuz_agent.modules.decisions.schemas.DecisionEntry``. Field names
 * MUST match exactly (snake_case) — pydantic doesn't auto-camelize and
 * we don't transform on receipt. */
export interface DecisionEntry {
  pending_id: string;
  session_id: string;
  task_id: string;
  project_id: string | null;
  subtask_key: string | null;
  agent_slug: string;
  project_title: string | null;
  project_emoji: string | null;
  task_title: string;
  subtask_label: string | null;
  /** Raw AskUserQuestion payload — ``{questions: [{question, options[]}, ...]}``.
   *  Drawer renders this through the same ``AskUserQuestionCard`` used inline. */
  question_payload: Record<string, unknown>;
  /** Unix epoch milliseconds (UTC); format via ``new Date(ms)``. */
  raised_at: number;
}

export interface DecisionPendingResponse {
  entries: DecisionEntry[];
}

const fetchJson = createFetchJson(() => _apiBase);

export const decisionsApi = {
  /** Fetch the current snapshot. Used by ``useDecisionInbox`` on first
   *  mount + on SSE reconnect (browser ``EventSource`` reconnects lose
   *  the cursor; we re-snapshot to recover). */
  fetchPending(): Promise<DecisionPendingResponse> {
    return fetchJson(`/v1/decisions/pending`);
  },

  /** SSE endpoint URL. Subscribers open with
   *  ``new EventSource(decisionsApi.streamUrl())``. Listen to named
   *  events: ``snapshot`` / ``added`` / ``resolved``. */
  streamUrl(): string {
    return `${_apiBase}/v1/decisions/stream`;
  },
};
