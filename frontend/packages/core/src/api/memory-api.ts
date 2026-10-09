/**
 * Client for /v1/memory/* endpoints (memory-system-design §11 / P2 #6).
 *
 * Inspect stored memory, prune individual entries or clear a scope, and
 * toggle the memory system on/off. Wire types come from the memory OpenAPI contract.
 */

import { createFetchJson, type RequestOptions } from "./fetch-json";
import type { components, operations } from "./generated/memory";
import { resolveApiBase } from "./base-resolver";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setMemoryApiBase = (url: string): void => {
  _apiBase = url;
};

type Schema = components["schemas"];
export type MemoryTarget = Schema["MemoryTarget"];
export type MemoryView = Schema["MemoryView"];
export type MemorySettings = Schema["MemorySettings"];
export type MemorySettingsPatch = Schema["MemorySettingsPatch"];
export type MemoryRecord = Schema["MemoryRecord"];
export type MemorySourceRef = Schema["MemorySourceRef"];
export type MemoryRecordsPage = Schema["MemoryRecordsPage"];
export type MemoryMutationReceipt = Schema["MemoryMutationReceipt"];
export type MemoryRecordAdd = Schema["MemoryRecordAdd"];
export type MemoryRecordCorrection = Schema["MemoryRecordCorrection"];
export type MemoryRecordForget = Schema["MemoryRecordForget"];
export type MemorySourceForget = Schema["MemorySourceForget"];
export type MemoryRecordsQuery = NonNullable<
  operations["listMemoryRecords"]["parameters"]["query"]
>;
export type MemoryRequestOptions = Pick<RequestOptions, "baseUrl" | "signal">;

const fetchJson = createFetchJson(() => _apiBase);
// Project memory follows the project's execution origin (multi-target
// editions); no project id / unknown id -> module default.
const maybeProjectBase = (projectId?: string): string | undefined =>
  projectId ? resolveApiBase({ projectId }, "") || undefined : undefined;

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const memoryApi = {
  getSettings(opts?: MemoryRequestOptions): Promise<MemorySettings> {
    return fetchJson("/v1/memory/settings", opts);
  },
  listRecords(
    query: MemoryRecordsQuery = {},
    opts?: MemoryRequestOptions,
  ): Promise<MemoryRecordsPage> {
    const qs = new URLSearchParams();
    if (query.project_id) qs.set("project_id", query.project_id);
    if (query.target) qs.set("target", query.target);
    if (query.offset != null) qs.set("offset", String(query.offset));
    if (query.limit != null) qs.set("limit", String(query.limit));
    return fetchJson(`/v1/memory/records${qs.size ? `?${qs}` : ""}`, {
      ...opts,
      baseUrl: opts?.baseUrl ?? maybeProjectBase(query.project_id),
    });
  },
  addRecord(
    payload: MemoryRecordAdd,
    opts?: MemoryRequestOptions,
  ): Promise<MemoryMutationReceipt> {
    const { operation_id, base_revision, target, content, project_id, kind, authority_id, authority_epoch } =
      payload;
    return fetchJson("/v1/memory/records", {
      ...jsonInit("POST", {
        operation_id,
        base_revision,
        authority_id,
        authority_epoch,        target,
        content,
        project_id,
        kind,
      }),
      ...opts,
      baseUrl: opts?.baseUrl ?? maybeProjectBase(project_id ?? undefined),
    });
  },
  correctRecord(
    id: string,
    payload: MemoryRecordCorrection,
    opts?: MemoryRequestOptions,
  ): Promise<MemoryMutationReceipt> {
    const { operation_id, base_revision, content, authority_id, authority_epoch } = payload;
    return fetchJson(`/v1/memory/records/${encodeURIComponent(id)}`, {
      ...jsonInit("PATCH", { operation_id, base_revision, content, authority_id, authority_epoch }),
      ...opts,
    });
  },
  forgetRecord(
    id: string,
    payload: MemoryRecordForget,
    opts?: MemoryRequestOptions,
  ): Promise<MemoryMutationReceipt> {
    const { operation_id, base_revision, target, project_id, authority_id, authority_epoch } = payload;
    return fetchJson(`/v1/memory/records/${encodeURIComponent(id)}`, {
      ...jsonInit("DELETE", {
        operation_id,
        base_revision,
        authority_id,
        authority_epoch,        target,
        project_id,
      }),
      ...opts,
      baseUrl: opts?.baseUrl ?? maybeProjectBase(project_id ?? undefined),
    });
  },
  forgetSource(
    payload: MemorySourceForget,
    opts?: MemoryRequestOptions,
  ): Promise<MemoryMutationReceipt> {
    const { operation_id, base_revision, kind, source_id, authority_id, authority_epoch } = payload;
    return fetchJson("/v1/memory/sources/forget", {
      ...jsonInit("POST", { operation_id, base_revision, kind, source_id, authority_id, authority_epoch }),
      ...opts,
    });
  },

  getMemory(projectId?: string): Promise<MemoryView> {
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return fetchJson<MemoryView>(`/v1/memory${q}`, {
      baseUrl: maybeProjectBase(projectId),
    });
  },

  patchSettings(
    payload: MemorySettingsPatch,
    opts?: MemoryRequestOptions,
  ): Promise<MemorySettings> {
    return fetchJson<MemorySettings>("/v1/memory/settings", {
      ...jsonInit("PATCH", payload),
      ...opts,
    });
  },

  deleteEntry(payload: {
    target: MemoryTarget;
    old_text: string;
    project_id?: string;
  }): Promise<MemoryView> {
    return fetchJson<MemoryView>("/v1/memory/entry", {
      ...jsonInit("DELETE", payload),
      baseUrl: maybeProjectBase(payload.project_id),
    });
  },

  clearScope(payload: {
    target: MemoryTarget;
    project_id?: string;
  }): Promise<MemoryView> {
    return fetchJson<MemoryView>("/v1/memory/scope", {
      ...jsonInit("DELETE", payload),
      baseUrl: maybeProjectBase(payload.project_id),
    });
  },
};
