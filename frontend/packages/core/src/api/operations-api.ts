import { resolveApiBase } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setOperationsApiBase = (url: string): void => {
  _apiBase = url;
};

export type OperationState =
  | "proposed"
  | "awaiting_confirmation"
  | "executing"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired"
  | "stale"
  | "superseded";

export interface OperationView {
  id: string;
  project_id: string | null;
  operation_type: string;
  operation_version: number;
  actor_kind: "user" | "agent" | "playbook" | "automation" | "system";
  actor_id: string | null;
  origin_session_id: string | null;
  origin_tool_call_id: string | null;
  origin_playbook_run_id: string | null;
  origin_automation_run_id: string | null;
  target_refs: Record<string, unknown>[];
  input_payload: Record<string, unknown>;
  preview: Record<string, unknown>;
  expected_revisions: Record<string, unknown>;
  risk_level: "low" | "material" | "destructive" | "external";
  confirmation_policy:
    | "direct"
    | "explicit_submit"
    | "confirm"
    | "approval"
    | "preauthorized";
  state: OperationState;
  proposal_hash: string;
  canonical_result_refs: Record<string, unknown>[];
  result_payload: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  created_at: number;
  updated_at: number;
}

export interface OperationToolResult {
  ok: boolean;
  action: string;
  operation?: OperationView;
  error_code?: string;
  message?: string;
}

const fetchJson = createFetchJson(() => _apiBase);
const sessionBase = (sessionId?: string | null): string | undefined =>
  sessionId ? resolveApiBase({ sessionId }, "") || undefined : undefined;

export const operationsApi = {
  get(operationId: string, sessionId?: string | null): Promise<OperationView> {
    return fetchJson(`/v1/operations/${encodeURIComponent(operationId)}`, {
      baseUrl: sessionBase(sessionId),
    });
  },

  /** ``decision`` answers a choice the proposal left open — a
   *  ``skill.submit`` whose slug collides with a library skill the draft was
   *  not prepared from needs ``{mode: "new_version"}`` or
   *  ``{mode: "rename", new_slug}``. It is not part of the proposal hash. */
  confirm(
    operationId: string,
    proposalHash: string,
    sessionId?: string | null,
    decision?: Record<string, unknown> | null,
  ): Promise<OperationView> {
    return fetchJson(
      `/v1/operations/${encodeURIComponent(operationId)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proposal_hash: proposalHash,
          ...(decision ? { decision } : {}),
        }),
        baseUrl: sessionBase(sessionId),
      },
    );
  },

  cancel(
    operationId: string,
    proposalHash: string,
    sessionId?: string | null,
  ): Promise<OperationView> {
    return fetchJson(
      `/v1/operations/${encodeURIComponent(operationId)}/cancel`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposal_hash: proposalHash }),
        baseUrl: sessionBase(sessionId),
      },
    );
  },

  status(
    operationIds: string[],
    sessionId?: string | null,
  ): Promise<{ operations: Record<string, OperationView> }> {
    return fetchJson("/v1/operations/status/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation_ids: operationIds }),
      baseUrl: sessionBase(sessionId),
    });
  },
};

export function parseOperationToolOutput(
  raw: string | undefined | null,
): OperationToolResult | null {
  if (!raw) return null;

  const parseValue = (value: unknown, depth = 0): OperationToolResult | null => {
    if (depth > 4) return null;
    if (typeof value === "string") {
      try {
        return parseValue(JSON.parse(value), depth + 1);
      } catch {
        return null;
      }
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        const parsed = parseValue(item, depth + 1);
        if (parsed) return parsed;
      }
      return null;
    }
    if (typeof value !== "object" || value === null) return null;

    const record = value as Record<string, unknown>;
    if (typeof record.ok === "boolean" && typeof record.action === "string") {
      return record as unknown as OperationToolResult;
    }

    // MCP runtimes may persist a structured content block instead of the
    // server's raw JSON text. Walk only the conventional envelope fields so
    // replayed operation cards survive runtime-specific serialization.
    for (const key of ["text", "content", "result", "output", "data"]) {
      if (!(key in record)) continue;
      const parsed = parseValue(record[key], depth + 1);
      if (parsed) return parsed;
    }
    return null;
  };

  try {
    return parseValue(JSON.parse(raw));
  } catch {
    return null;
  }
}
