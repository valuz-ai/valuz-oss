import { createFetchJson } from "./fetch-json";
import { invalidateRequestCache, requestBlob } from "./request";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setProjectsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface ProjectListItem {
  id: string;
  name: string;
  kind: "chat" | "project";
  root_path: string | null;
  icon: string | null;
  /** Resolved working directory the kernel runs sessions in.
   * Project projects: equals ``root_path``.
   * Chat projects: managed dir under ``data_dir/projects/{id}/``. */
  cwd: string | null;
}

export interface ProjectDetail extends ProjectListItem {
  instructions_md: string | null;
}

export interface ProjectDeletePreview {
  session_count: number;
  doc_binding_count: number;
  schedule_count: number;
  skill_config_count: number;
}

/** Header summary of a project inside an import preview bundle. */
export interface ImportProjectPreviewProject {
  name: string;
  kind: string;
  icon: string | null;
  has_instructions: boolean;
  has_memory: boolean;
  memory_file_count: number;
}

/** A member (agent deployment) inside an import preview bundle. */
export interface ImportProjectPreviewMember {
  agent_slug: string;
  source_agent_slug: string | null;
  name: string;
  description: string;
  /** True when an agent with this slug already exists in the caller's library. */
  in_library: boolean;
}

/** An automation inside an import preview bundle. */
export interface ImportProjectPreviewAutomation {
  name: string;
  agent_kind: string;
  agent_slug: string;
  trigger_kind: string;
  action_kind: string;
  status: string;
}

/** A skill reference inside an import preview bundle. */
export interface ImportProjectPreviewSkill {
  slug: string;
  source: string;
  already_present: boolean;
}

/** A connector reference inside an import preview bundle. */
export interface ImportProjectPreviewConnector {
  slug: string;
  display_name: string;
  requires_credentials: boolean;
  requires_setup: boolean;
  already_present: boolean;
}

/** Staged preview of an uploaded ``.valuzpack`` project — what's inside + how it lands. */
export interface ImportProjectPreview {
  preview_id: string;
  /** True when a project with this name already exists (confirm will skip). */
  name_conflict: boolean;
  project: ImportProjectPreviewProject;
  members: ImportProjectPreviewMember[];
  automations: ImportProjectPreviewAutomation[];
  skills: ImportProjectPreviewSkill[];
  connectors: ImportProjectPreviewConnector[];
}

/** A connector the user still needs to wire up after an import. */
export interface ProjectConnectorToConfigure {
  slug: string;
  display_name: string;
  requires_credentials: boolean;
  requires_setup: boolean;
}

/** Result of committing a project import. */
export interface ImportProjectConfirmResult {
  status: "created" | "skipped_name_conflict";
  project: ProjectListItem | null;
  members_created: number;
  members_reused: number;
  agents_created: number;
  agents_skipped: number;
  automations_created: number;
  automation_errors: { name: string; error: string }[];
  connectors_to_configure: ProjectConnectorToConfigure[];
}

/** A downloaded project bundle plus its server-suggested filename. */
export interface ExportedProject {
  blob: Blob;
  filename: string;
}

export interface ProjectFileNode {
  name: string;
  type: "file" | "directory";
  size: number | null;
  modified: string | null;
  children?: ProjectFileNode[];
}

export type ArtifactPreviewKind =
  | "markdown"
  | "code"
  | "image"
  | "pdf"
  | "html"
  | "docx"
  | "media"
  | "spreadsheet"
  | "plain"
  | "unsupported";

export interface ArtifactDescriptor {
  id: string;
  kind:
    | "project_file"
    | "session_output"
    | "document_artifact"
    | "slides_artifact"
    | "webpage_artifact";
  projectId?: string;
  path?: string;
  name: string;
  mimeType?: string | null;
  extension?: string | null;
  size?: number | null;
  modifiedAt?: string | null;
  previewKind: ArtifactPreviewKind;
  capabilities: {
    canPreview: boolean;
    canEdit: boolean;
    canOpenExternal: boolean;
    canCopyContent: boolean;
    canDownload: boolean;
  };
}

export type ArtifactContent =
  | {
      kind: "text";
      encoding: "utf-8";
      content: string;
      truncated: boolean;
      etag?: string | null;
      modifiedAt?: string | null;
    }
  | {
      kind: "binary";
      openUrl: string;
      mimeType: string;
      size?: number | null;
      reason?: string | null;
    }
  | {
      kind: "external";
      openUrl?: string | null;
      reason: string;
    };

export interface ArtifactFileResponse {
  artifact: ArtifactDescriptor;
  content: ArtifactContent;
}

export interface LastSessionPick {
  runtime_provider: string | null;
  provider_id: string | null;
  model_id: string | null;
  /** The last chat conversation's agent — seeds the composer's Chat mode. */
  agent_slug?: string | null;
  /** The Lead of the last task — seeds the composer's Task mode, remembered
   *  separately from the chat agent so each mode keeps its own role. */
  task_agent_slug?: string | null;
}

export interface ProjectCreateRequest {
  name: string;
  /** Omit/empty to allocate a backend-managed cwd (cloud / headless). */
  root_path?: string;
}

const fetchJson = createFetchJson(() => _apiBase);
const PROJECTS_TAG = "projects";
const PROJECTS_CACHE_TTL_MS = 30_000;
const PROJECTS_LIST_CACHE = {
  ttlMs: PROJECTS_CACHE_TTL_MS,
  tags: [PROJECTS_TAG],
};

function projectDetailCache(projectId: string) {
  return {
    ttlMs: PROJECTS_CACHE_TTL_MS,
    tags: [PROJECTS_TAG, `projects:${projectId}`],
  };
}

function invalidateProjects(): void {
  invalidateRequestCache({ tags: [PROJECTS_TAG] });
}

function filenameFromDisposition(header: string | null): string {
  const m = header ? /filename="?([^";]+)"?/.exec(header) : null;
  return m?.[1] ?? "project.valuzpack";
}

export const projectsApi = {
  list(): Promise<{ projects: ProjectListItem[] }> {
    return fetchJson("/v1/projects", { cache: PROJECTS_LIST_CACHE });
  },

  get(projectId: string): Promise<ProjectDetail> {
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}`, {
      cache: projectDetailCache(projectId),
    });
  },

  /**
   * Most-recent (runtime, provider, model) picked in this project.
   * Used by the project composer to seed pickers with the user's last
   * choice instead of the global Settings default. All three fields
   * are ``null`` when the project has no prior session.
   */
  getLastSessionPick(projectId: string): Promise<LastSessionPick> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/last-session-pick`,
    );
  },

  async create(payload: ProjectCreateRequest): Promise<ProjectDetail> {
    const result = await fetchJson<ProjectDetail>("/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    invalidateProjects();
    return result;
  },

  async rename(projectId: string, name: string): Promise<ProjectDetail> {
    const qs = new URLSearchParams({ name });
    const result = await fetchJson<ProjectDetail>(
      `/v1/projects/${encodeURIComponent(projectId)}?${qs}`,
      {
        method: "PATCH",
      },
    );
    invalidateProjects();
    return result;
  },

  async updateInstructions(
    projectId: string,
    instructionsMd: string,
  ): Promise<{ ok: boolean }> {
    const qs = new URLSearchParams({ instructions_md: instructionsMd });
    const result = await fetchJson<{ ok: boolean }>(
      `/v1/projects/${encodeURIComponent(projectId)}/instructions?${qs}`,
      {
        method: "PUT",
      },
    );
    invalidateProjects();
    return result;
  },

  listFiles(
    projectId: string,
    opts?: { depth?: number; includeHidden?: boolean; worktree?: string },
  ): Promise<{ files: ProjectFileNode[] }> {
    const qs = new URLSearchParams();
    if (opts?.depth !== undefined) qs.set("depth", String(opts.depth));
    if (opts?.includeHidden) qs.set("include_hidden", "true");
    // Scope the tree to a worktree session's checkout instead of the shared
    // project cwd when a worktree name is supplied.
    if (opts?.worktree) qs.set("worktree", opts.worktree);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/files${suffix}`,
    );
  },

  /** Upload one or more files into the project's cwd (multipart form).
   *  Each file's ``name`` is the target relative path (parent dirs are
   *  created by the backend). Works for cloud-managed projects whose cwd
   *  the client cannot reach directly, and equally for local ones.
   *  Uses ``fetchJson`` with a ``FormData`` body — no ``Content-Type`` so
   *  the browser sets the multipart boundary (same shape as
   *  ``importProjectPreview``). */
  uploadFiles(
    projectId: string,
    files: File[],
  ): Promise<{ project_id: string; written: string[] }> {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}/files`, {
      method: "POST",
      body: form,
    });
  },

  deletePreview(projectId: string): Promise<ProjectDeletePreview> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/delete-preview`,
    );
  },

  async delete(projectId: string): Promise<void> {
    await fetchJson(`/v1/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
    invalidateProjects();
  },

  getMcpServers(projectId: string): Promise<{ slugs: string[] }> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/connectors`,
    );
  },

  setMcpServers(projectId: string, slugs: string[]): Promise<{ ok: boolean }> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/connectors`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slugs }),
      },
    );
  },

  /** Export a project as a ``.valuzpack`` — returns the blob + filename for
   *  the caller to trigger a browser download (core stays DOM-free). */
  async exportProject(projectId: string): Promise<ExportedProject> {
    const { blob, headers } = await requestBlob(
      `/v1/projects/${encodeURIComponent(projectId)}/export`,
      { baseUrl: _apiBase },
    );
    return {
      blob,
      filename: filenameFromDisposition(headers.get("Content-Disposition")),
    };
  },

  /** Upload a ``.valuzpack`` project and stage it — returns a preview to confirm with. */
  importProjectPreview(file: File): Promise<ImportProjectPreview> {
    const form = new FormData();
    form.append("file", file);
    return fetchJson("/v1/projects/import-preview", {
      method: "POST",
      body: form,
    });
  },

  /**
   * Commit a staged project import by preview_id. ``rootPath`` is the
   * user-picked project folder (optional); when omitted the backend creates
   * the project under a managed cwd.
   */
  async importProjectConfirm(
    previewId: string,
    rootPath?: string,
  ): Promise<ImportProjectConfirmResult> {
    const result = await fetchJson<ImportProjectConfirmResult>(
      "/v1/projects/import/confirm",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preview_id: previewId,
          root_path: rootPath || null,
        }),
      },
    );
    invalidateProjects();
    return result;
  },
};
