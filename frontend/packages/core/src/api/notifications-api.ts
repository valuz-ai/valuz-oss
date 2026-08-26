/**
 * Notification API client (docs/design/notifications.md).
 *
 * The unified attention ledger — questions + task failures. Wraps:
 * - ``GET  /v1/notifications``          — REST snapshot (entries + unread)
 * - ``GET  /v1/notifications/history``  — resolved entries, cursor-paginated
 * - ``GET  /v1/notifications/stream``   — SSE (snapshot/added/updated/resolved)
 * - ``POST /v1/notifications/{id}:read`` / ``:read-all`` / ``:dismiss`` /
 *   ``:dismiss-all``
 *
 * Consumed by ``useNotifications`` (singleton) → ``notification-store``.
 * Supersedes decisions-api + the interim tasksApi.listAttention.
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setNotificationsApiBase = (url: string): void => {
  _apiBase = url;
};

/** Wire shape mirroring backend
 * ``valuz_agent.modules.notifications.schemas.NotificationEntry`` (snake_case). */
export interface NotificationEntry {
  id: string;
  /** question | task_failed | … */
  kind: string;
  /** Data snapshot (agent_slug for question, task_title for failure) — the
   *  frontend composes the localized display line from ``kind`` + this. */
  title: string;
  body: string;
  route: string | null;
  /** answer | resume | none */
  action: string;
  /** actionable | info */
  urgency: string;
  task_id: string | null;
  project_id: string | null;
  session_id: string | null;
  pending_id: string | null;
  /** Kind-specific: for ``question`` carries ``question_payload`` so the
   *  answer card renders verbatim. */
  payload: Record<string, unknown>;
  created_at: number;
  read_at: number | null;
  resolved_at: number | null;
}

export interface NotificationListResponse {
  entries: NotificationEntry[];
  unread: number;
}

export interface NotificationHistoryResponse {
  entries: NotificationEntry[];
  /** True when another page exists below the returned entries. */
  has_more?: boolean;
}

const fetchJson = createFetchJson(() => _apiBase);

export const notificationsApi = {
  fetchOpen(): Promise<NotificationListResponse> {
    return fetchJson(`/v1/notifications`);
  },

  /** Resolved entries, newest first. Page by passing the last entry's
   *  ``created_at`` as ``before``. */
  fetchHistory(
    params: { limit?: number; before?: number } = {},
  ): Promise<NotificationHistoryResponse> {
    const q = new URLSearchParams();
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.before != null) q.set("before", String(params.before));
    const qs = q.toString();
    return fetchJson(`/v1/notifications/history${qs ? `?${qs}` : ""}`);
  },

  streamUrl(): string {
    return `${_apiBase}/v1/notifications/stream`;
  },

  markRead(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/notifications/${encodeURIComponent(id)}:read`, {
      method: "POST",
    });
  },

  markAllRead(): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/notifications:read-all`, { method: "POST" });
  },

  dismiss(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/notifications/${encodeURIComponent(id)}:dismiss`, {
      method: "POST",
    });
  },

  dismissAll(): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/notifications:dismiss-all`, { method: "POST" });
  },
};
