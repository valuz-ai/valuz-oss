import { createFetchJson } from "./fetch-json";
import { resolveApiBase } from "./base-resolver";
import { fanOutTargets, getListFanOutTargets } from "../edition/list-fanout";
import { recordEntityOrigins } from "../edition/entity-origin";

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
  /** CLIENT-side tag on multi-target editions: which execution target
   * answered the list row (e.g. "local"/"cloud"). Never sent by the server;
   * absent on single-backend builds. */
  exec_origin?: string;
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
  project_id?: string;
  binding_kind: "kb" | "folder" | "document";
  target_id: string;
}

// ── Document types ──────────────────────────────────────────────────

export type DocStatus =
  "queued" | "processing" | "ready" | "failed" | "missing";

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
  project_id: string | null;
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
const projectBase = (projectId: string): string =>
  resolveApiBase({ projectId }, _apiBase);
const kbBase = (kbId: string): string => resolveApiBase({ kbId }, _apiBase);

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
  create(
    params: {
      name: string;
      /** Omit/empty to allocate a backend-managed root (cloud / headless). */
      root_path?: string;
      parser_routing?: string;
      auto_discover?: boolean;
    },
    opts?: { baseUrl?: string },
  ): Promise<KbDetail> {
    return fetchJson("/v1/kb", { ...jsonPost(params), baseUrl: opts?.baseUrl });
  },

  async list(): Promise<{ knowledge_bases: KbListItem[] }> {
    // Multi-target editions: fan out to every registered target, tag each
    // row's ``exec_origin`` with the answering target, and feed the origin
    // index so KB-scoped calls route to the owning backend. Zero targets
    // (OSS) keeps the single-backend path byte-identical.
    if (getListFanOutTargets().length === 0) {
      return fetchJson("/v1/kb");
    }
    const outcome = await fanOutTargets((target) =>
      fetchJson<{ knowledge_bases: KbListItem[] }>("/v1/kb", {
        baseUrl: target.baseUrl,
      }),
    );
    const seen = new Set<string>();
    const merged: KbListItem[] = [];
    for (const { target, value } of outcome.values) {
      recordEntityOrigins(value.knowledge_bases.map((w) => [w.id, target.id]));
      for (const kb of value.knowledge_bases) {
        if (seen.has(kb.id)) continue;
        seen.add(kb.id);
        merged.push({ ...kb, exec_origin: target.id });
      }
    }
    return { knowledge_bases: merged };
  },

  get(kbId: string): Promise<KbDetail> {
    return fetchJson(`/v1/kb/${kbId}`, { baseUrl: kbBase(kbId) });
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
      baseUrl: kbBase(kbId),
    });
  },

  delete(kbId: string): Promise<{ kb_id: string }> {
    return fetchJson(`/v1/kb/${kbId}`, {
      method: "DELETE",
      baseUrl: kbBase(kbId),
    });
  },

  rescan(kbId: string): Promise<ImportTask> {
    return fetchJson(`/v1/kb/${kbId}/rescan`, {
      method: "POST",
      baseUrl: kbBase(kbId),
    });
  },

  /** Upload one or more documents into the KB's root dir (multipart form).
   *  Each file's ``name`` is the target relative path (parent dirs are
   *  created by the backend). After writing, the backend kicks a rescan
   *  and returns the task row so the caller can poll progress via
   *  ``docsApi.getTask(taskId)``. Uses ``fetchJson`` with a ``FormData``
   *  body — no ``Content-Type`` so the browser sets the multipart boundary.
   *  This is the path that works in a browser and against a remote
   *  (cloud-managed) backend, where the Electron-only ``File.path`` fast
   *  path in ``KnowledgePage.handleDrop`` is unavailable. */
  uploadFiles(kbId: string, files: File[]): Promise<ImportTask> {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return fetchJson(`/v1/kb/${encodeURIComponent(kbId)}/files`, {
      method: "POST",
      body: form,
      baseUrl: kbBase(kbId),
    });
  },

  tree(kbId: string, folderId?: string): Promise<{ nodes: KbTreeNode[] }> {
    const qs = folderId ? `?folder_id=${folderId}` : "";
    return fetchJson(`/v1/kb/${kbId}/tree${qs}`, { baseUrl: kbBase(kbId) });
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
    // A doc list is always scoped to a KB on multi-target editions — route
    // it to the backend that owns that KB. Unscoped lists (no kb_id) stay on
    // the module default.
    const baseUrl = params?.kb_id ? kbBase(params.kb_id) : undefined;
    return fetchJson(`/v1/docs${query ? `?${query}` : ""}`, { baseUrl });
  },

  /** Optional ``kbId`` routes the call to the KB's owning backend on
   *  multi-target editions (the doc has no server-side origin field; the
   *  caller knows the KB it is browsing). Omit on single-backend builds. */
  get(id: string, kbId?: string): Promise<DocDetail> {
    return fetchJson(`/v1/docs/${id}`, {
      baseUrl: kbId ? kbBase(kbId) : undefined,
    });
  },

  preview(id: string, kbId?: string): Promise<DocPreview> {
    return fetchJson(`/v1/docs/${id}/preview`, {
      baseUrl: kbId ? kbBase(kbId) : undefined,
    });
  },

  delete(id: string, kbId?: string): Promise<{ document_id: string }> {
    return fetchJson(`/v1/docs/${id}`, {
      method: "DELETE",
      baseUrl: kbId ? kbBase(kbId) : undefined,
    });
  },

  search(params: {
    query: string;
    project_id: string;
    top_k?: number;
    folder_ids?: string[];
    document_ids?: string[];
  }): Promise<{ hits: SearchHit[] }> {
    return fetchJson(
      "/v1/docs/search",
      jsonPost({
        query: params.query,
        project_id: params.project_id,
        top_k: params.top_k ?? 5,
        folder_ids: params.folder_ids,
        document_ids: params.document_ids,
      }),
    );
  },

  /**
   * Re-parse and re-index documents.
   *
   * ``kbId`` routes the call the same way every other per-document call is
   * routed. Without it this always went to the module default, so retrying a
   * failed document in a CLOUD library posted to the local backend, which has
   * never heard of it — the button reported failure every single time, which
   * reads as "this document cannot be retried".
   */
  reindex(documentIds: string[], kbId?: string): Promise<ImportTask> {
    return fetchJson("/v1/docs/reindex", {
      ...jsonPost({ document_ids: documentIds }),
      baseUrl: kbId ? kbBase(kbId) : undefined,
    });
  },

  async health(): Promise<DocsHealth> {
    // Fan out on multi-target editions so the KB-page health summary
    // reflects BOTH backends' documents, matching kbApi.list(). Zero targets
    // (OSS) keeps the single-backend path. Counts summed across backends;
    // healthy if any backend is healthy.
    if (getListFanOutTargets().length === 0) {
      return fetchJson("/v1/docs/health");
    }
    const outcome = await fanOutTargets((target) =>
      fetchJson<DocsHealth>("/v1/docs/health", { baseUrl: target.baseUrl }),
    );
    const sum: DocsHealth = {
      status: "unavailable",
      total_documents: 0,
      ready_count: 0,
      processing_count: 0,
      failed_count: 0,
      missing_count: 0,
    };
    for (const { value } of outcome.values) {
      sum.total_documents += value.total_documents;
      sum.ready_count += value.ready_count;
      sum.processing_count += value.processing_count;
      sum.failed_count += value.failed_count;
      sum.missing_count += value.missing_count;
      if (value.status === "healthy") sum.status = "healthy";
    }
    return sum;
  },

  getTask(taskId: string): Promise<ImportTask> {
    return fetchJson(`/v1/docs/tasks/${taskId}`);
  },
};

// ── Binding API ─────────────────────────────────────────────────────

export const bindingApi = {
  list(projectId: string): Promise<{ bindings: BindingItem[] }> {
    return fetchJson(`/v1/projects/${projectId}/kb-bindings`, {
      baseUrl: projectBase(projectId),
    });
  },

  update(
    projectId: string,
    bindings: Array<{ binding_kind: string; target_id: string }>,
  ): Promise<{ bindings: BindingItem[] }> {
    return fetchJson(`/v1/projects/${projectId}/kb-bindings`, {
      ...jsonPut({ bindings }),
      baseUrl: projectBase(projectId),
    });
  },

  removeAll(projectId: string): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/projects/${projectId}/kb-bindings`, {
      method: "DELETE",
      baseUrl: projectBase(projectId),
    });
  },
};
