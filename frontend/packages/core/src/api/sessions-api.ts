import type {
  ActionResolvedEvent,
  EffortLevel,
  RequiresActionEvent,
  RequiresActionSubject,
  SessionDetail,
  SessionEventDTO,
  SessionListItem,
  SessionPermissionMode,
  SessionRulePreview,
  TodoItem,
} from "@valuz/shared";

export type {
  ActionResolvedEvent,
  EffortLevel,
  RequiresActionEvent,
  RequiresActionSubject,
  SessionDetail,
  SessionEventDTO,
  SessionListItem,
  SessionPermissionMode,
  SessionRulePreview,
  TodoItem,
};

/** Wire event type the host emits for kernel ``todo_update`` events. */
export const SESSION_TODOS_UPDATE_EVENT = "session.todos.update" as const;

/**
 * Wire event types the host emits for the kernel V5+1aae940 approval
 * contract. ``session.requires_action`` parks the turn waiting for a
 * user decision; ``session.action_resolved`` carries the resolution.
 * The host JSON-stringifies the structured payload + answers blobs to
 * honour the legacy ``Record<string,string>`` SSE shape — the helpers
 * below decode them.
 */
export const SESSION_REQUIRES_ACTION_EVENT = "session.requires_action" as const;
export const SESSION_ACTION_RESOLVED_EVENT = "session.action_resolved" as const;

const _safeJson = <T>(raw: unknown, fallback: T): T => {
  if (typeof raw !== "string" || !raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    return parsed === null || parsed === undefined ? fallback : (parsed as T);
  } catch {
    return fallback;
  }
};

/**
 * Pull a structured ``requires_action`` event out of an SSE frame, or
 * ``null`` if the frame isn't a ``session.requires_action``. The host
 * JSON-stringifies ``payload`` + ``available_decisions`` so we re-parse
 * here; parse failure returns the default (``{}`` / ``[]``) rather than
 * propagating an error — a renderer can still show the pending_id and
 * subject even when the payload shape is unknown.
 */
export function parseRequiresAction(
  event: SessionEventDTO,
): RequiresActionEvent | null {
  if (event.event.event_type !== SESSION_REQUIRES_ACTION_EVENT) return null;
  const payload = event.event.payload;
  // V5+d008b53: ``session_rule_preview`` and ``original_input`` are
  // JSON-stringified by the host SSE adapter to honour the legacy
  // ``Record<string,string>`` wire contract. Decode to the structured
  // shape (or ``null`` when the kernel sent an empty object — every
  // subject that doesn't carry a rule, plus older kernels).
  const rulePreviewRaw = _safeJson<Record<string, unknown>>(
    payload?.session_rule_preview,
    {},
  );
  const sessionRulePreview =
    typeof rulePreviewRaw.kind === "string" &&
    typeof rulePreviewRaw.display === "string"
      ? ({
          kind: rulePreviewRaw.kind,
          display: rulePreviewRaw.display,
          runtime_kind:
            typeof rulePreviewRaw.runtime_kind === "string"
              ? rulePreviewRaw.runtime_kind
              : "exact",
          rule_data:
            typeof rulePreviewRaw.rule_data === "object" &&
            rulePreviewRaw.rule_data !== null
              ? (rulePreviewRaw.rule_data as Record<string, unknown>)
              : {},
        } as SessionRulePreview)
      : null;
  const originalInputRaw = _safeJson<Record<string, unknown>>(
    payload?.original_input,
    {},
  );
  const originalInput =
    Object.keys(originalInputRaw).length > 0 ? originalInputRaw : null;
  return {
    message_id: payload?.message_id ?? "",
    pending_id: payload?.pending_id ?? "",
    subject: (payload?.subject ?? "tool_input") as RequiresActionSubject,
    runtime_provider: payload?.runtime_provider ?? "",
    available_decisions: _safeJson<string[]>(payload?.available_decisions, []),
    payload: _safeJson<Record<string, unknown>>(payload?.payload, {}),
    expires_at: payload?.expires_at ?? "",
    session_rule_preview: sessionRulePreview,
    original_input: originalInput,
  };
}

/**
 * Pull a structured ``action_resolved`` event out of an SSE frame, or
 * ``null`` when the frame is a different type. The kernel emits this
 * for every terminal state — user decision, system-synthesised
 * ``expired`` (host restart), and ``interrupted`` (Stop press).
 *
 * V5+d008b53: also surfaces ``approve_for_session`` /
 * ``approve_with_changes`` user verbs and ``auto_approved`` (kernel
 * cache-hit) with the matching rule-id back-pointers.
 */
export function parseActionResolved(
  event: SessionEventDTO,
): ActionResolvedEvent | null {
  if (event.event.event_type !== SESSION_ACTION_RESOLVED_EVENT) return null;
  const payload = event.event.payload;
  // ``rule_id`` / ``auto_resolved_by_rule_id`` arrive as empty strings
  // when absent (the SSE adapter's ``_stringify(... or "")``); coerce
  // back to ``null`` so the consumer guards on a single shape.
  const ruleId = payload?.rule_id;
  const autoRuleId = payload?.auto_resolved_by_rule_id;
  return {
    message_id: payload?.message_id ?? "",
    pending_id: payload?.pending_id ?? "",
    decision: (payload?.decision ??
      "expired") as ActionResolvedEvent["decision"],
    resolved_by: (payload?.resolved_by ??
      "system") as ActionResolvedEvent["resolved_by"],
    message: payload?.message ?? "",
    answers: _safeJson<Record<string, string | string[]>>(payload?.answers, {}),
    rule_id: typeof ruleId === "string" && ruleId.length > 0 ? ruleId : null,
    auto_resolved_by_rule_id:
      typeof autoRuleId === "string" && autoRuleId.length > 0
        ? autoRuleId
        : null,
  };
}

/**
 * Pull a refreshed TODO list out of a SSE event, or ``null`` if the
 * event isn't a ``session.todos.update`` frame.
 *
 * The host's SSE adapter JSON-stringifies the todos array to keep the
 * legacy ``Record<string,string>`` payload contract; this helper re-
 * parses it. Malformed payloads return ``null`` (silently dropped —
 * the prior snapshot stays on screen).
 */
export function parseTodosUpdate(event: SessionEventDTO): TodoItem[] | null {
  if (event.event.event_type !== SESSION_TODOS_UPDATE_EVENT) return null;
  const raw = event.event.payload?.todos;
  if (!raw || typeof raw !== "string") return null;
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed
      .filter(
        (t): t is { content: unknown; status: unknown; activeForm?: unknown } =>
          typeof t === "object" && t !== null,
      )
      .map((t) => ({
        content: typeof t.content === "string" ? t.content : "",
        status: typeof t.status === "string" ? t.status : "pending",
        activeForm: typeof t.activeForm === "string" ? t.activeForm : undefined,
      }))
      .filter((t) => t.content.length > 0);
  } catch {
    return null;
  }
}

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setSessionsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface SessionEventsResponse {
  session_id: string;
  items: SessionEventDTO[];
}

export interface SessionEventWindowResponse {
  session_id: string;
  /** Events in this window, ordered ASC by seq — prepend straight in. */
  items: SessionEventDTO[];
  /** True iff at least one ``user_message`` row exists strictly older
   * than ``items[0].seq``. Cursor for the next page is ``items[0].seq``. */
  has_more: boolean;
}

export interface SessionRunResponse {
  session: SessionDetail;
  events: SessionEventDTO[];
}

/**
 * Internal runtime enum — matches kernel ``Session.runtime_provider``.
 * Stable wire id; the user-facing label comes from
 * ``RuntimeListItem.display_name`` (Claude Agent / Codex Agent / Valuz
 * Agent).
 */
export type { RuntimeId } from "@valuz/shared";

import type { RuntimeId } from "@valuz/shared";

export interface SessionCreateRequest {
  project_id: string;
  title?: string | null;
  // V5: ``model`` is locked at session creation. Either pass an explicit model
  // id, or pass a provider id and let the backend pick its default model.
  model_id?: string | null;
  provider_id?: string | null;
  /**
   * REP-107: explicit Runtime Agent for this session. ``null`` /
   * undefined lets the backend derive the runtime from the provider
   * (backwards compatible). Unknown values return 422.
   */
  runtime_id?: RuntimeId | null;
  // Slugs of MCP data sources to enable for this session. Resolved by
  // ``adapters.mcp_resolver`` into kernel ``McpServerConfig`` rows. The user
  // selects these via the data-source picker before creating the session.
  mcp_provider_slugs?: string[];
  /**
   * Approval mode for the new session (kernel V5+1aae940; live-
   * reconcile since V5+bba3014). The backend falls back to
   * ``full_access`` when omitted. ``"auto_review"`` is rejected (400)
   * for DeepAgents sessions — only the Claude tier ships the LLM
   * classifier today. Mutable post-create via
   * ``PATCH /v1/sessions/{id}/permission-mode``.
   */
  permission_mode?: SessionPermissionMode | null;
  /**
   * Initial reasoning-effort budget for the new session (kernel
   * V5+bba3014 ``ModelSettings.effort``). ``null`` / undefined lets
   * the runtime fall through to its SDK default. Mutable post-create
   * via ``PATCH /v1/sessions/{id}/effort``.
   */
  effort?: EffortLevel | null;
  /**
   * Agent to bind this conversation to. When set, instructions / skills /
   * connectors come from the agent, and runtime / model / provider / effort
   * default to the agent's brain. An explicit model_id / provider_id /
   * runtime_id / effort above OVERRIDES the agent's default for this one
   * session only — the agent itself is never modified. ``null`` keeps the
   * classic model-picker path.
   */
  agent_slug?: string | null;
}

export interface SessionMessageRequest {
  prompt: string;
  provider_id?: string | null;
  model_id?: string | null;
}

/**
 * Verbs the user submits on ``POST /v1/sessions/{id}/actions``
 * (kernel V5+d008b53). Kernel-only verbs (``auto_approved`` /
 * ``expired`` / ``interrupted``) are intentionally absent — the
 * server-side Pydantic validator rejects them.
 */
export type SessionActionDecision =
  | "approve"
  | "approve_with_changes"
  | "approve_for_session"
  | "reject"
  | "answer";

export interface SessionActionRequest {
  pending_id: string;
  decision: SessionActionDecision;
  /**
   * Optional rejection reason; surfaced back to the agent on
   * ``reject`` (Claude ``PermissionResultDeny.message``,
   * DeepAgents ``RejectDecision.message``; codex logs only).
   */
  message?: string | null;
  /** Required iff ``decision === "answer"``; otherwise must be omitted (422). */
  answers?: Record<string, string | string[]> | null;
  /**
   * V5+d008b53 / A1: replacement tool args. Required iff
   * ``decision === "approve_with_changes"``; otherwise must be
   * omitted (422). Same shape as the matching
   * ``RequiresActionEvent.original_input``.
   */
  modified_input?: Record<string, unknown> | null;
}

export interface SessionActionResponse {
  session_id: string;
  pending_id: string;
  decision: SessionActionDecision;
  accepted_at: number;
  idempotent: boolean;
  /**
   * V5+d008b53: UUID of the just-committed session-scoped rule.
   * Non-null only when ``decision === "approve_for_session"``;
   * idempotent retries surface the *original* rule_id so a WS/SSE
   * reconnect doesn't think it created a new rule.
   */
  rule_id?: string | null;
}

export interface SessionAttachmentItem {
  id: string;
  session_id: string;
  filename: string;
  stored_path: string;
  parsed_path?: string | null;
  parse_status?: string;
  size_bytes: number;
  mime_type: string | null;
  created_at: number;
  /**
   * Origin of the attachment. ``local`` is a multipart upload the host
   * owns under ``~/.valuz/app/attachments/{session_id}/``; ``kb_doc``
   * is a live reference to a global knowledge-base document — the
   * row's ``stored_path``/``parsed_path`` reuse KB-owned paths
   * directly, no copy. Drives the panel icon + source label + delete
   * cleanup path.
   */
  source_kind?: "local" | "kb_doc";
  source_kb_id?: string | null;
  source_kb_doc_id?: string | null;
  /**
   * Per-turn lifecycle marker. ``null`` = pending (staged for the next
   * turn); Unix epoch milliseconds (UTC) = already shipped with a turn.
   * The panel's "uploaded files" section shows every attachment as
   * session history; the composer's staging chips + the upload-cap
   * count filter to the pending (``consumed_at == null``) subset.
   */
  consumed_at?: number | null;
}

const fetchJson = createFetchJson(() => _apiBase);

export type SessionStreamCallback = (event: SessionEventDTO) => void;

export const sessionsApi = {
  list(projectId?: string): Promise<{ sessions: SessionListItem[] }> {
    const qs = new URLSearchParams();
    if (projectId) qs.set("project_id", projectId);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(`/v1/sessions${suffix}`);
  },

  get(sessionId: string): Promise<SessionDetail> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}`);
  },

  create(payload: SessionCreateRequest): Promise<SessionDetail> {
    return fetchJson("/v1/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  listEvents(
    sessionId: string,
    afterSeq?: number,
  ): Promise<SessionEventsResponse> {
    const qs = new URLSearchParams();
    if (afterSeq !== undefined && afterSeq > 0)
      qs.set("after_seq", String(afterSeq));
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/events${suffix}`,
    );
  },

  /**
   * Turn-aligned page of historical events for the conversation scroller.
   *
   * - Initial load: omit ``beforeSeq`` → server returns the most recent
   *   ``turnLimit`` turns.
   * - Scroll-up "load earlier turns": pass the previous response's
   *   ``items[0].seq`` as ``beforeSeq``. Loop until ``has_more`` is false.
   *
   * Each response always starts on a ``user_message`` boundary —
   * never a partial turn — so the renderer can prepend without trim.
   */
  listEventsWindow(
    sessionId: string,
    opts: { beforeSeq?: number | null; turnLimit?: number } = {},
  ): Promise<SessionEventWindowResponse> {
    const qs = new URLSearchParams();
    if (opts.beforeSeq !== undefined && opts.beforeSeq !== null) {
      qs.set("before_seq", String(opts.beforeSeq));
    }
    qs.set("turn_limit", String(opts.turnLimit ?? 20));
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/events/window?${qs}`,
    );
  },

  sendMessage(
    sessionId: string,
    prompt: string,
    providerId?: string | null,
    modelId?: string | null,
  ): Promise<SessionDetail> {
    const body: SessionMessageRequest = { prompt };
    if (providerId) body.provider_id = providerId;
    if (modelId) body.model_id = modelId;
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  subscribeEvents(
    sessionId: string,
    onEvent: SessionStreamCallback,
    afterSeq?: number,
    signal?: AbortSignal,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const qs = new URLSearchParams();
      if (afterSeq !== undefined && afterSeq > 0)
        qs.set("after_seq", String(afterSeq));
      const suffix = qs.toString() ? `?${qs}` : "";
      const url = `${_apiBase}/v1/sessions/${encodeURIComponent(sessionId)}/events/stream${suffix}`;
      fetch(url, { signal })
        .then((res) => {
          if (!res.ok) {
            return res.text().then((text) => {
              reject(new Error(`API ${res.status}: ${text}`));
            });
          }
          const reader = res.body?.getReader();
          if (!reader) {
            reject(new Error("No response body"));
            return;
          }
          const decoder = new TextDecoder();
          let buffer = "";

          function processChunk(): Promise<void> {
            return reader!.read().then(({ done, value }) => {
              if (done) {
                resolve();
                return;
              }
              buffer += decoder.decode(value, { stream: true });
              // SSE frames are delimited by blank lines; lines within a
              // frame each begin with a field name (``data:``, ``event:``).
              // We only need ``data:`` because the JSON envelope already
              // carries ``seq`` + ``event_type``.
              const lines = buffer.split("\n");
              buffer = lines.pop() ?? "";

              for (const line of lines) {
                if (line.startsWith("data:")) {
                  const raw = line.slice(5).trim();
                  if (!raw) continue;
                  try {
                    const parsed = JSON.parse(raw) as {
                      seq?: number;
                      event_type?: string;
                      payload?: Record<string, string>;
                      timestamp?: string;
                    };
                    // Heartbeat frames ({"seq": N}) carry no event_type;
                    // skip them — they only exist to keep the connection
                    // alive over idle periods.
                    if (!parsed.event_type) continue;
                    onEvent({
                      seq: typeof parsed.seq === "number" ? parsed.seq : 0,
                      event: {
                        event_type: parsed.event_type,
                        payload: parsed.payload ?? {},
                      },
                      timestamp:
                        typeof parsed.timestamp === "number"
                          ? parsed.timestamp
                          : undefined,
                    });
                  } catch {
                    // Skip malformed SSE data lines
                  }
                }
              }
              return processChunk();
            });
          }

          processChunk().catch(reject);
        })
        .catch((err) => {
          if (signal?.aborted) {
            resolve();
          } else {
            reject(err);
          }
        });
    });
  },

  interrupt(sessionId: string): Promise<SessionDetail> {
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/interrupt`,
      {
        method: "POST",
      },
    );
  },

  cancel(sessionId: string): Promise<SessionDetail> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}/cancel`, {
      method: "POST",
    });
  },

  regenerate(sessionId: string): Promise<SessionDetail> {
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/regenerate`,
      {
        method: "POST",
      },
    );
  },

  rename(sessionId: string, name: string): Promise<SessionDetail> {
    const qs = new URLSearchParams({ name });
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}?${qs}`, {
      method: "PATCH",
    });
  },

  delete(sessionId: string): Promise<void> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  },

  // Per-session attached skill list. skill-creator is always active and is
  // not included in this list.
  getExtraSkills(sessionId: string): Promise<{ skill_ids: string[] }> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}/skills`);
  },

  setExtraSkills(
    sessionId: string,
    skillIds: string[],
  ): Promise<{ skill_ids: string[] }> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}/skills`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_ids: skillIds }),
    });
  },

  uploadAttachment(
    sessionId: string,
    file: File,
  ): Promise<SessionAttachmentItem> {
    const form = new FormData();
    form.append("file", file);
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/attachments`,
      {
        method: "POST",
        body: form,
      },
    );
  },

  listAttachments(
    sessionId: string,
  ): Promise<{ items: SessionAttachmentItem[] }> {
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/attachments`,
    );
  },

  /**
   * Attach one or more knowledge-base documents to ``sessionId`` as
   * live references (no file copy). Same panel slot as local
   * uploads. ``doc_ids`` already attached to the session are
   * silently dropped server-side; missing doc ids return 400.
   */
  addKbAttachments(
    sessionId: string,
    docIds: string[],
  ): Promise<{ items: SessionAttachmentItem[] }> {
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/attachments/kb`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_ids: docIds }),
      },
    );
  },

  /**
   * Remove a single session attachment. For ``source_kind="local"``
   * the backend also unlinks the underlying file; for
   * ``source_kind="kb_doc"`` only the row is deleted (the KB
   * document survives for other sessions).
   */
  deleteAttachment(sessionId: string, attachmentId: string): Promise<void> {
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`,
      { method: "DELETE" },
    );
  },

  /**
   * Change the approval mode for an existing session (kernel V5+1aae940;
   * live-reconcile since V5+bba3014). The new mode applies on the next
   * Send: Claude uses the live ``set_permission_mode`` mutator (with
   * fork-on-rebuild for the bypass tier); Codex threads
   * approval_policy / sandbox_policy per-turn via ``turn_kwargs``;
   * DeepAgents drops its cached graph for a cold rebuild. DeepAgents
   * sessions return 400 for ``"auto_review"``.
   */
  updatePermissionMode(
    sessionId: string,
    permissionMode: SessionPermissionMode,
  ): Promise<SessionDetail> {
    return fetchJson(
      `/v1/sessions/${encodeURIComponent(sessionId)}/permission-mode`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permission_mode: permissionMode }),
      },
    );
  },

  /**
   * Change the reasoning-effort budget for an existing session (kernel
   * V5+bba3014 ``ModelSettings.effort``). Live-reconcile: the new
   * effort applies on the next Send. Claude cold-reloads the SDK
   * client (effort is a build-time option); Codex drops it into
   * ``turn_kwargs.reasoning_effort`` (survives ``--resume``);
   * DeepAgents drops its cached graph so the next turn rebuilds the
   * langchain chat client with the new value. ``effort=null`` resets
   * to the SDK default. Returns 400 on an unknown value.
   */
  updateEffort(
    sessionId: string,
    effort: EffortLevel | null,
  ): Promise<SessionDetail> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}/effort`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ effort }),
    });
  },

  /**
   * Resolve a pending ``requires_action`` event with a user decision.
   * Idempotent on ``(pending_id, decision)``: a repeat with the same
   * decision returns the original ``accepted_at`` and
   * ``idempotent: true``; a conflicting decision throws (server 409).
   */
  submitAction(
    sessionId: string,
    request: SessionActionRequest,
  ): Promise<SessionActionResponse> {
    return fetchJson(`/v1/sessions/${encodeURIComponent(sessionId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  },
};
