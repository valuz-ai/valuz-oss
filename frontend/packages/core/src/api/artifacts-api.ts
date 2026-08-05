import { resolveApiBase, type ApiBaseRef } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setArtifactsApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

/**
 * One version of a deliverable. Every version keeps its own snapshot path, so
 * any of them can be opened — that is what "delivering does not overwrite"
 * means for the client.
 */
export interface ArtifactRevisionItem {
  id: string;
  version_no: number;
  file_name: string;
  file_path: string;
  /**
   * File identity for ``filesApi.resolve``. **Empty** when the version's bytes
   * are gone (a removed worktree, or a legacy row whose file was already
   * missing when it was migrated) — show the version, but do not offer to open
   * it.
   */
  ref: string;
  file_size: number;
  mime_type: string | null;
  /** ``ready`` | ``missing`` — see ``ref``. */
  status: string;
  /** The conversation that produced this version, if it is still known. */
  source_session_id: string | null;
  created_at: number;
}

/** A deliverable at its latest version. */
export interface ArtifactSummary {
  id: string;
  display_name: string;
  /** Content family: document | presentation | spreadsheet | ui | media | file. */
  kind: string;
  version_no: number;
  updated_at: number;
  current: ArtifactRevisionItem;
}

export const artifactsApi = {
  /**
   * The workspace's deliverables, most recently updated first.
   *
   * Scoped by worktree as well as project, because a worktree is an independent
   * line of work whose deliverables live in its own directory. Pass the
   * session's worktree name (or omit it for the project's own working
   * directory) — mixing the two would list files that cannot both be open.
   */
  list(
    projectId: string,
    options: { worktree?: string; limit?: number; baseUrl?: ApiBaseRef } = {},
  ): Promise<{ items: ArtifactSummary[]; total: number }> {
    const query = new URLSearchParams({ project_id: projectId });
    if (options.worktree) query.set("worktree", options.worktree);
    if (options.limit != null) query.set("limit", String(options.limit));
    return fetchJson(`/v1/artifacts?${query.toString()}`, {
      baseUrl: options.baseUrl
        ? resolveApiBase(options.baseUrl, "") || undefined
        : undefined,
    });
  },

  /** One deliverable's history, oldest first. */
  listRevisions(
    artifactId: string,
    options: { baseUrl?: ApiBaseRef } = {},
  ): Promise<{
    artifact_id: string;
    display_name: string;
    items: ArtifactRevisionItem[];
  }> {
    return fetchJson(
      `/v1/artifacts/${encodeURIComponent(artifactId)}/revisions`,
      {
        baseUrl: options.baseUrl
          ? resolveApiBase(options.baseUrl, "") || undefined
          : undefined,
      },
    );
  },
};
