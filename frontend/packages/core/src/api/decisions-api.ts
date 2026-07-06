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
  /** Where the question came from — drives the card's context line.
   *  ``task`` renders the task chain; ``chat`` / ``project_chat`` render
   *  the session title. */
  source_kind: "task" | "chat" | "project_chat";
  task_id: string | null;
  project_id: string | null;
  subtask_key: string | null;
  agent_slug: string;
  project_title: string | null;
  project_emoji: string | null;
  task_title: string | null;
  subtask_label: string | null;
  /** Kernel session title — context for non-task entries; falls back to
   *  the question text when null. */
  session_title: string | null;
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
   *  ``fetchEventSource(() => decisionsApi.streamUrl(), …)`` and handle named
   *  events: ``snapshot`` / ``added`` / ``resolved``. */
  streamUrl(): string {
    return `${_apiBase}/v1/decisions/stream`;
  },
};
