import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setApiBase = (url: string): void => {
  _apiBase = url;
};

// ── KB types ────────────────────────────────────────────────────────

export interface KbListItem {
  id: string;
  name: string;
  root_path: string;
  parser_routing: string;
  document_count: number;
  status: "all_ready" | "has_processing" | "has_missing";
  created_at: number | null;
}

export interface KbDetail extends KbListItem {
  auto_discover: boolean;
  last_full_scan_at: number | null;
}

export interface KbTreeNode {
  id: string;
  name: string;
  relative_path: string;
  kind: "folder" | "document";
  status: string;
  document_count: number;
}

export interface BindingItem {
  workspace_id?: string;
  binding_kind: "kb" | "folder" | "document";
  target_id: string;
}

// ── Document types ──────────────────────────────────────────────────

export type DocStatus =
  | "queued"
  | "processing"
  | "ready"
  | "failed"
  | "missing";

export interface DocListItem {
  id: string;
  filename: string;
  title: string | null;
  status: DocStatus;
  chunk_count: number;
  file_size_bytes: number;
  mime_type: string | null;
  kb_id: string | null;
  kb_folder_id: string | null;
  relative_path: string | null;
  created_at: number | null;
}

/**
 * One row in the per-document parser attempt history. The backend
 * appends an entry for every plugin run on the doc — failed / fallback
 * attempts (``ok: false`` with an ``error``) AND the final successful
 * one (``ok: true``, empty ``error``). Kept on the doc after
 * ``status="ready"`` so the panel renders the full timeline, e.g.
 * "MinerU ✗ → LightLocal ✓".
 */
export interface ParserAttempt {
  plugin_id: string;
  error: string;
  occurred_at: string;
  /** ``true`` for the plugin that succeeded; ``false`` for failed /
   *  fallback attempts. */
  ok: boolean;
}

export interface DocDetail extends DocListItem {
  source_path: string | null;
  parser_mode: string | null;
  docs_runtime_id: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  /** Full plugin-attempt history. Present on ``DocDetail`` since
   *  V5+SS-1 (kernel hash …) but only consumed by the frontend
   *  from this commit onward. May be ``[]`` for legacy docs
   *  imported before the field was wired. */
  parser_attempts: ParserAttempt[];
}

export interface DocPreview {
  document_id: string;
  markdown: string;
}

export interface ImportTask {
  task_id: string;
  task_type: "rescan" | "reindex";
  status: "queued" | "processing" | "completed" | "failed";
  total_items: number;
  processed_items: number;
  failed_items: number;
  kb_id: string | null;
  workspace_id: string | null;
  created_at: number | null;
}

export interface SearchHit {
  document_id: string;
  filename: string;
  score: number;
  snippet: string;
  page_ref: string | null;
  chunk_ref: string | null;
}

export interface DocsHealth {
  status: "healthy" | "unavailable";
  total_documents: number;
  ready_count: number;
  processing_count: number;
  failed_count: number;
  missing_count: number;
}

const fetchJson = createFetchJson(() => _apiBase);

const jsonPost = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const jsonPut = (body: unknown): RequestInit => ({
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// ── KB API ──────────────────────────────────────────────────────────

export const kbApi = {
  create(params: {
    name: string;
    root_path: string;
    parser_routing?: string;
    auto_discover?: boolean;
  }): Promise<KbDetail> {
    return fetchJson("/v1/kb", jsonPost(params));
  },

  list(): Promise<{ knowledge_bases: KbListItem[] }> {
    return fetchJson("/v1/kb");
  },

  get(kbId: string): Promise<KbDetail> {
    return fetchJson(`/v1/kb/${kbId}`);
  },

  update(
    kbId: string,
    params: {
      name?: string;
      parser_routing?: string;
    },
  ): Promise<KbDetail> {
    return fetchJson(`/v1/kb/${kbId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
  },

  delete(kbId: string): Promise<{ kb_id: string }> {
    return fetchJson(`/v1/kb/${kbId}`, { method: "DELETE" });
  },

  rescan(kbId: string): Promise<ImportTask> {
    return fetchJson(`/v1/kb/${kbId}/rescan`, { method: "POST" });
  },

  tree(kbId: string, folderId?: string): Promise<{ nodes: KbTreeNode[] }> {
    const qs = folderId ? `?folder_id=${folderId}` : "";
    return fetchJson(`/v1/kb/${kbId}/tree${qs}`);
  },
};

// ── Document API ────────────────────────────────────────────────────

export const docsApi = {
  list(params?: {
    q?: string;
    status?: string;
    kb_id?: string;
  }): Promise<{ documents: DocListItem[] }> {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.status) qs.set("status", params.status);
    if (params?.kb_id) qs.set("kb_id", params.kb_id);
    const query = qs.toString();
    return fetchJson(`/v1/docs${query ? `?${query}` : ""}`);
  },

  get(id: string): Promise<DocDetail> {
    return fetchJson(`/v1/docs/${id}`);
  },

  preview(id: string): Promise<DocPreview> {
    return fetchJson(`/v1/docs/${id}/preview`);
  },

  delete(id: string): Promise<{ document_id: string }> {
    return fetchJson(`/v1/docs/${id}`, { method: "DELETE" });
  },

  search(params: {
    query: string;
    workspace_id: string;
    top_k?: number;
    folder_ids?: string[];
    document_ids?: string[];
  }): Promise<{ hits: SearchHit[] }> {
    return fetchJson(
      "/v1/docs/search",
      jsonPost({
        query: params.query,
        workspace_id: params.workspace_id,
        top_k: params.top_k ?? 5,
        folder_ids: params.folder_ids,
        document_ids: params.document_ids,
      }),
    );
  },

  reindex(documentIds: string[]): Promise<ImportTask> {
    return fetchJson(
      "/v1/docs/reindex",
      jsonPost({ document_ids: documentIds }),
    );
  },

  health(): Promise<DocsHealth> {
    return fetchJson("/v1/docs/health");
  },

  getTask(taskId: string): Promise<ImportTask> {
    return fetchJson(`/v1/docs/tasks/${taskId}`);
  },
};

// ── Binding API ─────────────────────────────────────────────────────

export const bindingApi = {
  list(workspaceId: string): Promise<{ bindings: BindingItem[] }> {
    return fetchJson(`/v1/workspaces/${workspaceId}/kb-bindings`);
  },

  update(
    workspaceId: string,
    bindings: Array<{ binding_kind: string; target_id: string }>,
  ): Promise<{ bindings: BindingItem[] }> {
    return fetchJson(
      `/v1/workspaces/${workspaceId}/kb-bindings`,
      jsonPut({ bindings }),
    );
  },

  removeAll(workspaceId: string): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/workspaces/${workspaceId}/kb-bindings`, {
      method: "DELETE",
    });
  },
};
