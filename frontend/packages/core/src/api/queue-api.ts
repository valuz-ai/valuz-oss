/**
 * Client for /v1/sessions/{id}/queue/* endpoints — the session input queue
 * (docs/design/session-input-queue.md). Submit follow-up inputs while a turn is
 * running; they drain FIFO after the active turn. Hand-written like the other
 * ``*-api`` modules (no OpenAPI codegen is wired today).
 */

import { resolveApiBase } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setQueueApiBase = (url: string): void => {
  _apiBase = url;
};

export interface QueuedInput {
  id: string;
  /**
   * ``queued`` (waiting) | ``blocked`` (could not run, see error_message) in
   * ``items``; ``dispatched`` appears only as the list's ``dispatching`` item.
   */
  status: string;
  position: number;
  text: string;
  attachment_count: number;
  provider_id: string | null;
  model_id: string | null;
  error_message: string | null;
  created_at: number;
  updated_at: number | null;
}

export interface QueuedInputList {
  session_id: string;
  items: QueuedInput[];
  /** True when an interrupt soft-paused auto-drain; awaiting explicit resume. */
  paused: boolean;
  /**
   * True while a host drain chain is in flight. A dispatched (in-flight) item
   * is invisible in ``items`` (only queued/blocked listed), so per-turn
   * re-subscribers keep following until the last item finishes.
   */
  draining?: boolean;
  /**
   * The item the drain is executing right now (status ``dispatched``): already
   * out of ``items``, but its turn may not have landed a durable user message
   * yet. Clients keep its bubble visible until it shows up in the transcript
   * instead of dropping it one refetch too early.
   */
  dispatching?: QueuedInput | null;
}

const fetchJson = createFetchJson(() => _apiBase);

const sessionBase = (sessionId: string): string =>
  resolveApiBase({ sessionId }, _apiBase);

const enc = encodeURIComponent;
const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});

export const queueApi = {
  list(sessionId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(`/v1/sessions/${enc(sessionId)}/queue`, {
      baseUrl: sessionBase(sessionId),
    });
  },

  enqueue(
    sessionId: string,
    prompt: string,
    opts: { providerId?: string | null; modelId?: string | null } = {},
  ): Promise<QueuedInputList> {
    const body: { prompt: string; provider_id?: string; model_id?: string } = {
      prompt,
    };
    if (opts.providerId) body.provider_id = opts.providerId;
    if (opts.modelId) body.model_id = opts.modelId;
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue`,
      { ...jsonInit("POST", body), baseUrl: sessionBase(sessionId) },
    );
  },

  edit(
    sessionId: string,
    queueId: string,
    prompt: string,
  ): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/${enc(queueId)}`,
      { ...jsonInit("PATCH", { prompt }), baseUrl: sessionBase(sessionId) },
    );
  },

  remove(sessionId: string, queueId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/${enc(queueId)}`,
      { ...jsonInit("DELETE"), baseUrl: sessionBase(sessionId) },
    );
  },

  resume(sessionId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/resume`,
      { ...jsonInit("POST"), baseUrl: sessionBase(sessionId) },
    );
  },

  /** Steer — send this queued item now, silently interrupting the active turn. */
  steer(sessionId: string, queueId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/${enc(queueId)}/steer`,
      { ...jsonInit("POST"), baseUrl: sessionBase(sessionId) },
    );
  },
};
