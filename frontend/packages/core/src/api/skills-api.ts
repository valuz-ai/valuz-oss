import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setSkillsApiBase = (url: string): void => {
  _apiBase = url;
};

export type SkillTargetScope = "user" | "project" | "official" | "tenant";

/**
 * Where the skill came from — host-side bookkeeping the backend keeps
 * in the ``valuz_skill_index`` DB row (never in SKILL.md):
 *
 * - ``"created"`` — built via the skill library dialog / skill-creator
 *   AI session / "duplicate" action. Drives the "创建" badge in the
 *   .agents group.
 * - ``"imported"`` — pulled in via archive / URL / directory import.
 *   Drives the "同步" badge.
 * - ``"discovered"`` — found on disk by the filesystem scan (e.g.
 *   hand-dropped or symlinked into ~/.agents/skills/), not originated
 *   by Valuz. Renders no badge.
 */
export type SkillCreationOrigin = "created" | "imported" | "discovered";

export interface SkillView {
  id: string;
  name: string;
  description: string;
  scope: SkillTargetScope;
  source: string;
  path: string;
  enabled: boolean;
  tags: string[];
  slug?: string;
  icon?: string | null;
  status?: string;
  readonly?: boolean;
  deletable: boolean;
  is_locked?: boolean;
  lock_reason?: string | null;
  project_root?: string | null;
  origin_label?: string | null;
  argument_hint?: string | null;
  context?: string | null;
  content_hash?: string | null;
  manifest_hash?: string | null;
  version?: number | null;
  /**
   * Folder birthtime as Unix epoch milliseconds (UTC); format via
   * ``new Date(ms)``. Drives the DESC sort on the skill management
   * page — newest folder on top, name ASC as the tiebreaker. ``null``
   * for legacy rows that weren't scanned with the new helper (e.g.
   * immediately after the DB migration but before the next
   * ``startup_scan``); the sort puts ``null`` last.
   */
  folder_created_at?: number | null;
  /**
   * See ``SkillCreationOrigin``. Drives the "创建" / "同步" badge in
   * the .agents group on the skill management page; "discovered"
   * renders no badge. Always present — the backend defaults it to
   * "discovered".
   */
  creation_origin: SkillCreationOrigin;
}

/** Import provenance for a URL/GitHub-imported skill (mirrors the backend
 * ``valuz_skill_index.origin_json``). Lets the detail UI show "Imported from …"
 * and link back to the source. */
export interface SkillOrigin {
  type: "github" | "url";
  source_url: string;
  /** In-repo relative path when the skill came from a multi-skill
   * collection/plugin; empty for a single-skill source. */
  path: string;
}

export interface SkillDetail extends SkillView {
  instructions_markdown?: string | null;
  file_count?: number;
  root_path?: string | null;
  manifest_filename?: string | null;
  metadata?: Record<string, unknown>;
  /** Null/absent for skills not imported from a URL. */
  origin?: SkillOrigin | null;
}

export interface SkillsCatalog {
  project_id: string;
  skills: SkillView[];
}

export interface SkillScanResponse {
  discovered: number;
}

export interface SkillCreateRequest {
  name: string;
  description?: string;
  target_scope?: SkillTargetScope;
  project_id?: string;
  instructions_markdown?: string;
  add_to_project?: boolean;
}

export interface SkillUpdateRequest {
  name?: string;
  description?: string;
  instructions_markdown?: string;
  tags?: string[];
}

export interface SkillCopyRequest {
  new_name: string;
  project_id?: string;
  add_to_project?: boolean;
}

export interface SkillDeletePreview {
  affected_projects: { project_id: string; name: string }[];
  count: number;
}

export interface SkillImportPreviewFile {
  path: string;
  type: "file" | "directory";
  size: number | null;
  /** Directory entries carry nested ``children`` from the backend
   * tree-walker (``_build_skill_file_tree``). The /skills/{id}/files
   * endpoint always returns this shape; the import-preview tree uses
   * the same node format. */
  children?: SkillImportPreviewFile[];
}

/** One skill detected inside an import source. When a URL/archive points at a
 * collection or plugin (multiple SKILL.md), the preview lists every candidate
 * so the user can multi-select; each carries its own ``preview_id`` and confirm
 * is called once per chosen skill. */
export interface SkillImportCandidate {
  preview_id: string;
  name: string;
  description: string;
  file_count: number;
  /** Location within the fetched tree (for display). */
  relpath: string;
}

export interface SkillImportArchivePreview {
  preview_id: string;
  name: string;
  description: string;
  tags: string[];
  file_tree: SkillImportPreviewFile[];
  validation_warnings: string[];
  name_conflict: boolean;
  suggested_name: string | null;
  /** When the source contains MULTIPLE skills (a collection/plugin), this lists
   * every detected skill (each with its own ``preview_id``). Length <= 1 → the
   * top-level fields above ARE the single skill (backward compatible). */
  skills: SkillImportCandidate[];
}

export interface SkillImportArchiveConfirmRequest {
  preview_id: string;
  name?: string;
  target_scope?: SkillTargetScope;
  project_id?: string;
  add_to_project?: boolean;
}

export interface SkillImportDirectoryPreviewRequest {
  directory_path: string;
  target_scope?: SkillTargetScope;
  project_id?: string;
}

const fetchJson = createFetchJson(() => _apiBase);

export const skillsApi = {
  list(projectId?: string): Promise<SkillsCatalog> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(`/v1/skills${qs}`);
  },

  get(skillId: string, projectId?: string): Promise<SkillDetail> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}${qs}`);
  },

  create(payload: SkillCreateRequest): Promise<SkillView> {
    return fetchJson("/v1/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  update(
    skillId: string,
    payload: SkillUpdateRequest,
    projectId?: string,
  ): Promise<SkillView> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}${qs}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  copy(skillId: string, payload: SkillCopyRequest): Promise<SkillView> {
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}/copy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  deleteDryRun(
    skillId: string,
    projectId?: string,
  ): Promise<SkillDeletePreview> {
    const qs = new URLSearchParams({ mode: "dry_run" });
    if (projectId) qs.set("project_id", projectId);
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}?${qs}`, {
      method: "DELETE",
    });
  },

  deleteConfirm(skillId: string, projectId?: string): Promise<void> {
    const qs = new URLSearchParams({ mode: "confirm" });
    if (projectId) qs.set("project_id", projectId);
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}?${qs}`, {
      method: "DELETE",
    });
  },

  importArchivePreview(
    file: File,
    targetScope?: string,
    projectId?: string,
  ): Promise<SkillImportArchivePreview> {
    const form = new FormData();
    form.append("file", file);
    if (targetScope) form.append("target_scope", targetScope);
    if (projectId) form.append("project_id", projectId);
    return fetchJson("/v1/skills/import/archive", {
      method: "POST",
      body: form,
    });
  },

  importArchiveConfirm(
    payload: SkillImportArchiveConfirmRequest,
  ): Promise<SkillView> {
    return fetchJson("/v1/skills/import/archive/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  importDirectoryPreview(
    payload: SkillImportDirectoryPreviewRequest,
  ): Promise<SkillImportArchivePreview> {
    return fetchJson("/v1/skills/import/directory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  importDirectoryConfirm(
    payload: SkillImportArchiveConfirmRequest,
  ): Promise<SkillView> {
    return fetchJson("/v1/skills/import/archive/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  importUrlPreview(
    url: string,
    targetScope?: SkillTargetScope,
    projectId?: string,
  ): Promise<SkillImportArchivePreview> {
    return fetchJson("/v1/skills/import/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        target_scope: targetScope,
        project_id: projectId,
      }),
    });
  },

  importUrlConfirm(
    payload: SkillImportArchiveConfirmRequest,
  ): Promise<SkillView> {
    return fetchJson("/v1/skills/import/url/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  listTags(projectId?: string): Promise<{ tags: string[] }> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(`/v1/skills/tags${qs}`);
  },

  listFiles(
    skillId: string,
    projectId?: string,
  ): Promise<SkillImportPreviewFile[]> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}/files${qs}`);
  },

  getFileContent(
    skillId: string,
    filePath: string,
    projectId?: string,
  ): Promise<{ path: string; content: string }> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(
      `/v1/skills/${encodeURIComponent(skillId)}/files/${filePath}${qs}`,
    );
  },

  updateFile(
    skillId: string,
    action: {
      action: "create" | "rename" | "delete";
      path: string;
      new_path?: string;
      content?: string;
    },
    projectId?: string,
  ): Promise<{ path: string; content: string }> {
    const qs = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return fetchJson(`/v1/skills/${encodeURIComponent(skillId)}/files${qs}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action),
    });
  },

  projectCatalog(projectId: string): Promise<SkillsCatalog> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/skills`,
    );
  },

  // Project skill *binding* (scan / setSkillState / overwrite) removed —
  // skills bind on the Agent now (08-agents-module). ``projectCatalog``
  // above stays: it feeds the conversation composer's skill-insert chips.

  eventsStreamUrl(): string {
    return `${_apiBase}/v1/skills/events/stream`;
  },

  // Scenario B — AI 创建 Skill (chat-driven authoring) ──────────────────

  startCreateChat(payload?: {
    model_id?: string | null;
    provider_id?: string | null;
  }): Promise<SkillCreateChatStart> {
    // Match SessionCreateRequest's nullable shape — undefined and null
    // both fall through to the provider default on the backend; only an
    // explicit string forces the override.
    const hasBody =
      payload != null &&
      (payload.model_id != null || payload.provider_id != null);
    return fetchJson("/v1/skills/create/chat/start", {
      method: "POST",
      headers: hasBody ? { "Content-Type": "application/json" } : undefined,
      body: hasBody ? JSON.stringify(payload) : undefined,
    });
  },

  /** Unified skill-creator launcher used by all three product entries
   * (chat / project / skills_library). The backend stamps
   * ``creation_context`` onto the kernel session so the ``submit_skill``
   * confirm endpoint can decide which side-effects to apply. */
  startCreate(
    payload: SkillCreateStartRequest,
  ): Promise<SkillCreateStartResponse> {
    return fetchJson("/v1/skills/create/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  /** User accepts the skill the agent submitted via ``submit_skill``.
   * Promotes the staged slug to ``~/.agents/skills/{slug}/`` and applies
   * per-context side-effects (project entries also bind to the project). */
  confirmSubmission(
    sessionId: string,
    slug: string,
    payload?: SkillSubmissionConfirmRequest,
  ): Promise<SkillSubmissionConfirmResponse> {
    const body = payload ?? {};
    return fetchJson(
      `/v1/skills/submissions/${encodeURIComponent(sessionId)}/${encodeURIComponent(slug)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },

  /** User discards the agent's submission. Cleans up the staged slug;
   * idempotent — calling twice returns ``removed: false`` on the second
   * call. */
  dismissSubmission(
    sessionId: string,
    slug: string,
  ): Promise<SkillSubmissionDismissResponse> {
    return fetchJson(
      `/v1/skills/submissions/${encodeURIComponent(sessionId)}/${encodeURIComponent(slug)}/dismiss`,
      { method: "POST" },
    );
  },

  // Staging (Scenario B + D3 accept path) ─────────────────────────────

  scanStaging(sessionId: string): Promise<StagingScanResponse> {
    return fetchJson(
      `/v1/skills/staging/${encodeURIComponent(sessionId)}/scan`,
    );
  },

  readStagingFile(
    sessionId: string,
    slug: string,
    path: string,
  ): Promise<{ path: string; content: string }> {
    const qs = new URLSearchParams({ slug, path });
    return fetchJson(
      `/v1/skills/staging/${encodeURIComponent(sessionId)}/file?${qs}`,
    );
  },

  syncStaging(
    sessionId: string,
    payload: StagingSyncRequest,
  ): Promise<StagingSyncResponse> {
    return fetchJson(
      `/v1/skills/staging/${encodeURIComponent(sessionId)}/sync`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  },

  optimizeFromSkill(
    sessionId: string,
    sourceSkillId: string,
  ): Promise<StagingOptimizeResponse> {
    return fetchJson(
      `/v1/skills/staging/${encodeURIComponent(sessionId)}/optimize`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_skill_id: sourceSkillId }),
      },
    );
  },
};

export interface SkillCreateChatStart {
  session_id: string;
  authoring_project_id: string;
}

/** Where the user opened the skill-creator from. The backend persists
 * this on the kernel session so the ``submit_skill`` confirm endpoint
 * can apply the right side-effects on user approval. */
export type SkillCreationKind = "chat" | "project" | "skills_library";

export interface SkillCreationContext {
  kind: SkillCreationKind;
  /** Required when ``kind === "project"``; identifies the project the
   * new skill should be bound to on confirm. */
  project_id?: string;
}

export interface SkillCreateStartRequest {
  context: SkillCreationContext;
  model_id?: string | null;
  provider_id?: string | null;
  /** Runtime explicitly picked by the user. Defaults to whatever the
   *  session-service derives from the provider when omitted. */
  runtime_id?: string | null;
}

export interface SkillCreateStartResponse {
  session_id: string;
  authoring_project_id: string;
  creation_context: SkillCreationContext;
}

/** Body passed to ``POST /v1/skills/submissions/{session_id}/{slug}/confirm``.
 * Frontend pulls these fields off the original ``submit_skill`` ``tool_use``
 * event so they ride along to the audit log; the actual content lives in
 * the staging dir. */
export interface SkillSubmissionConfirmRequest {
  summary?: string | null;
  change_kind?: "create" | "update";
  files_touched?: string[];
}

export interface SkillSubmissionConfirmResponse {
  skill: SkillView;
  creation_context: SkillCreationContext;
  /** Populated when the submission was confirmed under a project entry —
   * the new skill was bound to this project. ``null`` for chat /
   * skills_library entries. */
  bound_to_project_id?: string | null;
}

export interface SkillSubmissionDismissResponse {
  session_id: string;
  slug: string;
  removed: boolean;
}

// Staging types ───────────────────────────────────────────────────────

export type StagingConflictKind = "none" | "same_source" | "diverged";
export type StagingSyncStrategy = "overwrite" | "fork" | "abort";

export interface StagingFileNode {
  path: string;
  type: "file" | "directory";
  size?: number | null;
}

export interface StagingSlugView {
  slug: string;
  name: string;
  description: string;
  file_count: number;
  total_bytes: number;
  files: StagingFileNode[];
  conflict_kind: StagingConflictKind;
  suggested_strategy: StagingSyncStrategy;
  suggested_new_slug?: string | null;
  source_skill_id?: string | null;
  version?: number | null;
}

export interface StagingScanResponse {
  session_id: string;
  staging_path: string;
  slugs: StagingSlugView[];
}

export interface StagingSyncItem {
  slug: string;
  strategy?: StagingSyncStrategy;
  new_slug?: string | null;
}

export interface StagingSyncRequest {
  items: StagingSyncItem[];
  target_scope?: SkillTargetScope;
  project_id?: string | null;
}

export interface StagingSyncItemResult {
  slug: string;
  strategy: StagingSyncStrategy;
  written_path?: string | null;
  new_slug?: string | null;
  skipped: boolean;
}

export interface StagingSyncResponse {
  session_id: string;
  results: StagingSyncItemResult[];
}

export interface StagingOptimizeResponse {
  session_id: string;
  slug: string;
  staging_path: string;
}
