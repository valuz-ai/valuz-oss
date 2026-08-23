import { ApiError, createFetchJson } from "./fetch-json";
import { resolveApiBase } from "./base-resolver";
import { fanOutTargets, getListFanOutTargets } from "../edition/list-fanout";
import {
  getEntityOrigin,
  recordEntityOrigin,
  recordEntityOrigins,
} from "../edition/entity-origin";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setPlaybooksApiBase = (url: string): void => {
  _apiBase = url;
};

export type PlaybookStatus = "draft" | "active" | "retired";
export type PlaybookTriggerKind =
  | "user"
  | "agent"
  | "automation"
  | "playbook"
  | "api";

export interface PlaybookDefinition {
  id: string;
  project_id: string | null;
  name: string;
  status: PlaybookStatus;
  origin: "user" | "system_example_copy" | "fork";
  source_definition_id: string | null;
  current_version: number;
  revision: number;
  created_at: number;
  updated_at: number;
  /** Client-side execution target observation; never persisted by the API. */
  exec_origin?: string;
}

export interface PlaybookVersion {
  id: string;
  definition_id: string;
  version: number;
  content: string;
  reference_metadata: Record<string, unknown>[];
  default_executor: Record<string, unknown>;
  created_by: string | null;
  produced_by_run: string | null;
  base_version: number | null;
  created_at: number;
}

export interface PlaybookDetail {
  definition: PlaybookDefinition;
  current_version: PlaybookVersion;
  /** Loaded on demand for editing; newest immutable version first. */
  versions?: PlaybookVersion[];
}

export interface PlaybookRun {
  id: string;
  definition_id: string;
  definition_version: number;
  project_id: string | null;
  status:
    | "queued"
    | "planning"
    | "running"
    | "waiting_approval"
    | "completed"
    | "failed"
    | "stopped";
  trigger_kind: PlaybookTriggerKind;
  trigger_ref: string | null;
  content_snapshot: string;
  extra_instruction: string | null;
  session_id: string | null;
  task_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface PlaybookCreatePayload {
  name: string;
  content: string;
  status?: PlaybookStatus;
  project_id?: string | null;
  reference_metadata?: Record<string, unknown>[];
  default_executor?: Record<string, unknown>;
}

const fetchJson = createFetchJson(() => _apiBase);
const playbookBase = (definitionId: string): string | undefined =>
  resolveApiBase({ playbookId: definitionId }, "") || undefined;

export const playbooksApi = {
  async list(projectId?: string): Promise<PlaybookDefinition[]> {
    const suffix = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    if (projectId) {
      return fetchJson(`/v1/playbooks${suffix}`, {
        baseUrl: resolveApiBase({ projectId }, "") || undefined,
      });
    }
    if (getListFanOutTargets().length === 0) {
      return fetchJson(`/v1/playbooks`);
    }
    const outcome = await fanOutTargets(async (target, signal) => {
      try {
        return await fetchJson<PlaybookDefinition[]>(`/v1/playbooks`, {
          baseUrl: target.baseUrl,
          signal,
        });
      } catch (error) {
        // A reachable older runtime may predate the Playbook API. Treat that
        // target as an empty Playbook library instead of marking the whole
        // service unreachable; real transport and server failures still flow
        // through fan-out's degraded-target handling.
        if (error instanceof ApiError && error.status === 404) return [];
        throw error;
      }
    });
    recordEntityOrigins(
      outcome.values.flatMap(({ target, value }) =>
        value.map(
          (definition) => [definition.id, target.id] as [string, string],
        ),
      ),
    );
    return outcome.values.flatMap(({ target, value }) =>
      value.map((definition) => ({
        ...definition,
        exec_origin: target.id,
      })),
    );
  },

  async get(definitionId: string): Promise<PlaybookDetail> {
    const detail = await fetchJson<PlaybookDetail>(
      `/v1/playbooks/${encodeURIComponent(definitionId)}`,
      {
      baseUrl: playbookBase(definitionId),
      },
    );
    const origin = getEntityOrigin(definitionId, "playbook");
    return origin
      ? {
          ...detail,
          definition: { ...detail.definition, exec_origin: origin },
        }
      : detail;
  },

  async create(
    payload: PlaybookCreatePayload,
    opts?: { baseUrl?: string },
  ): Promise<PlaybookDetail> {
    const result = await fetchJson<{
      definition: PlaybookDefinition;
      version: PlaybookVersion;
    }>("/v1/playbooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl:
        opts?.baseUrl ??
        (payload.project_id
          ? resolveApiBase({ projectId: payload.project_id }, "") || undefined
          : undefined),
    });
    if (payload.project_id) {
      const origin = getEntityOrigin(payload.project_id, "project");
      if (origin) recordEntityOrigin(result.definition.id, origin);
    }
    return {
      definition: result.definition,
      current_version: result.version,
      versions: [result.version],
    };
  },

  createVersion(
    definitionId: string,
    payload: {
      base_version: number;
      content: string;
      reference_metadata?: Record<string, unknown>[];
      default_executor?: Record<string, unknown>;
      status?: PlaybookStatus;
    },
  ): Promise<{ definition: PlaybookDefinition; version: PlaybookVersion }> {
    return fetchJson(
      `/v1/playbooks/${encodeURIComponent(definitionId)}/versions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: playbookBase(definitionId),
      },
    );
  },

  updateDefinition(
    definitionId: string,
    payload: {
      expected_revision: number;
      name?: string;
      status?: PlaybookStatus;
      project_id?: string | null;
    },
  ): Promise<PlaybookDefinition> {
    return fetchJson(`/v1/playbooks/${encodeURIComponent(definitionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: playbookBase(definitionId),
    });
  },

  listVersions(definitionId: string): Promise<PlaybookVersion[]> {
    return fetchJson(
      `/v1/playbooks/${encodeURIComponent(definitionId)}/versions`,
      { baseUrl: playbookBase(definitionId) },
    );
  },

  deleteDefinition(
    definitionId: string,
    expectedRevision: number,
  ): Promise<void> {
    const query = new URLSearchParams({
      expected_revision: String(expectedRevision),
    });
    return fetchJson(
      `/v1/playbooks/${encodeURIComponent(definitionId)}?${query}`,
      {
        method: "DELETE",
        baseUrl: playbookBase(definitionId),
      },
    );
  },

  listRuns(params?: {
    projectId?: string;
    definitionId?: string;
  }): Promise<PlaybookRun[]> {
    const qs = new URLSearchParams();
    if (params?.projectId) qs.set("project_id", params.projectId);
    if (params?.definitionId)
      qs.set("definition_id", params.definitionId);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(`/v1/playbooks/runs/list${suffix}`, {
      baseUrl: params?.definitionId
        ? playbookBase(params.definitionId)
        : params?.projectId
          ? resolveApiBase({ projectId: params.projectId }, "") || undefined
          : undefined,
    });
  },

  getRun(runId: string): Promise<PlaybookRun> {
    return fetchJson(`/v1/playbooks/runs/${encodeURIComponent(runId)}`, {
      baseUrl: resolveApiBase({ playbookId: runId }, "") || undefined,
    });
  },
};
