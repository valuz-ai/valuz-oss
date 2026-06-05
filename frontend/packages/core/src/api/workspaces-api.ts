import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setWorkspacesApiBase = (url: string): void => {
  _apiBase = url;
};

export interface WorkspaceListItem {
  id: string;
  name: string;
  kind: "chat" | "project";
  root_path: string | null;
  icon: string | null;
  /** Resolved working directory the kernel runs sessions in.
   * Project workspaces: equals ``root_path``.
   * Chat workspaces: managed dir under ``data_dir/workspaces/{id}/``. */
  cwd: string | null;
}

export interface WorkspaceDetail extends WorkspaceListItem {
  instructions_md: string | null;
  memory_summary: string | null;
}

export interface WorkspaceDeletePreview {
  session_count: number;
  doc_binding_count: number;
  schedule_count: number;
  skill_config_count: number;
}

export interface WorkspaceFileNode {
  name: string;
  type: "file" | "directory";
  size: number | null;
  modified: string | null;
  children?: WorkspaceFileNode[];
}

export interface LastSessionPick {
  runtime_provider: string | null;
  provider_id: string | null;
  model_id: string | null;
}

export interface ProjectCreateRequest {
  name: string;
  root_path: string;
}

const fetchJson = createFetchJson(() => _apiBase);

export const workspacesApi = {
  list(): Promise<{ workspaces: WorkspaceListItem[] }> {
    return fetchJson("/v1/workspaces");
  },

  get(workspaceId: string): Promise<WorkspaceDetail> {
    return fetchJson(`/v1/workspaces/${encodeURIComponent(workspaceId)}`);
  },

  /**
   * Most-recent (runtime, provider, model) picked in this workspace.
   * Used by the project composer to seed pickers with the user's last
   * choice instead of the global Settings default. All three fields
   * are ``null`` when the workspace has no prior session.
   */
  getLastSessionPick(workspaceId: string): Promise<LastSessionPick> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/last-session-pick`,
    );
  },

  create(payload: ProjectCreateRequest): Promise<WorkspaceDetail> {
    return fetchJson("/v1/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  rename(workspaceId: string, name: string): Promise<WorkspaceDetail> {
    const qs = new URLSearchParams({ name });
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}?${qs}`,
      {
        method: "PATCH",
      },
    );
  },

  updateInstructions(
    workspaceId: string,
    instructionsMd: string,
  ): Promise<{ ok: boolean }> {
    const qs = new URLSearchParams({ instructions_md: instructionsMd });
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/instructions?${qs}`,
      {
        method: "PUT",
      },
    );
  },

  listFiles(
    workspaceId: string,
    opts?: { depth?: number; includeHidden?: boolean },
  ): Promise<{ files: WorkspaceFileNode[] }> {
    const qs = new URLSearchParams();
    if (opts?.depth !== undefined) qs.set("depth", String(opts.depth));
    if (opts?.includeHidden) qs.set("include_hidden", "true");
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/files${suffix}`,
    );
  },

  deletePreview(workspaceId: string): Promise<WorkspaceDeletePreview> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/delete-preview`,
    );
  },

  delete(workspaceId: string): Promise<void> {
    return fetchJson(`/v1/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "DELETE",
    });
  },

  getMcpServers(workspaceId: string): Promise<{ slugs: string[] }> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/connectors`,
    );
  },

  setMcpServers(
    workspaceId: string,
    slugs: string[],
  ): Promise<{ ok: boolean }> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/connectors`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slugs }),
      },
    );
  },
};
