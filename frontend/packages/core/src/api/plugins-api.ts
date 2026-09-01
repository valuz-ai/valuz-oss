import { createFetchJson } from "./fetch-json";
import { invalidateRequestCache, requestBlob } from "./request";

/**
 * Client for ``/v1/plugins`` — installed Agent Plugins (Agent Plugins
 * Specification 1.0.0: ``plugin.json`` + ``skills/`` + optional ``mcp.json``).
 *
 * Mirrors ``api/openapi.yaml`` → Plugin* schemas (hand-synced). Type names
 * carry the ``AgentPlugin`` prefix because ``@valuz/core`` already exports
 * unrelated ``Plugin*`` names for the frontend edition/parser plugin system.
 */

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setPluginsApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

export type AgentPluginMemberKind = "skill" | "connector";
/** Derived (never authored): ``skills_only`` = 技能套件, ``with_connectors``
 * = the plugin ships an ``mcp.json``. */
export type AgentPluginComposition = "skills_only" | "with_connectors";
export type AgentPluginSource =
  "market" | "local_dir" | "zip" | "url" | "claude_plugin" | "codebuddy_plugin";
/** Per-member policy when a same-slug resource already exists with different
 * content: ``skip`` (default — link only, flag ``content_differs``) or
 * ``overwrite`` (replace the library copy). Never silent. */
export type AgentPluginOnConflict = "skip" | "overwrite";
export type AgentPluginFormat =
  "agent_plugins" | "claude_plugin" | "codebuddy_plugin";

export interface AgentPluginAuthor {
  name?: string | null;
  email?: string | null;
  url?: string | null;
}

export interface AgentPluginMemberRef {
  kind: AgentPluginMemberKind;
  slug: string;
}

export interface AgentPluginMember extends AgentPluginMemberRef {
  name: string;
  description: string | null;
  /** Skill ``metadata.version`` from the SKILL.md frontmatter — display only. */
  meta_version: string | null;
  content_hash: string;
  installed: boolean;
  /** Library copy differs from this plugin's copy (skipped on conflict). */
  content_differs: boolean;
}

export interface AgentPluginView {
  id: string;
  /** ``plugin.json.name`` — unique per user. */
  name: string;
  /** ``plugin.json.version`` — THE distribution version. */
  version: string | null;
  description: string | null;
  author: AgentPluginAuthor | null;
  homepage: string | null;
  repository: string | null;
  license: string | null;
  keywords: string[];
  source: AgentPluginSource;
  /** Market item id / path / URL the plugin was installed from. */
  source_ref: string | null;
  composition: AgentPluginComposition;
  enabled: boolean;
  members: AgentPluginMember[];
  skill_count: number;
  connector_count: number;
  root_path: string;
  installed_at: string;
  updated_at: string;
  update_available: boolean | null;
  /** Any member skill is protected — derived per request, never stored. */
  protected?: boolean;
}

export interface AgentPluginList {
  items: AgentPluginView[];
}

export interface AgentPluginSkippedMember extends AgentPluginMemberRef {
  reason: string;
}

export interface AgentPluginInstallResult {
  plugin: AgentPluginView;
  status: "installed" | "updated" | "already_installed";
  skipped: AgentPluginSkippedMember[];
  conflicts: AgentPluginMemberRef[];
  warnings: string[];
}

/** Member row in a preview — the ``installed`` / ``content_differs`` /
 * ``content_hash`` fields describe the *would-be* state and may be omitted
 * by older servers. */
export interface AgentPluginPreviewMember extends AgentPluginMemberRef {
  name: string;
  description?: string | null;
  meta_version?: string | null;
  content_hash?: string | null;
  installed?: boolean;
  content_differs?: boolean;
}

export interface AgentPluginPreview {
  manifest: Record<string, unknown>;
  members: AgentPluginPreviewMember[];
  /** Same-slug members whose library copy differs — the UI must ask
   * skip / overwrite before installing. */
  conflicts: AgentPluginMemberRef[];
  warnings: string[];
  format: AgentPluginFormat;
  composition?: AgentPluginComposition;
  skipped?: AgentPluginSkippedMember[];
  /** A same-name plugin is already installed: from the same source (install
   * = update) or from another source (install would conflict). */
  existing?: "same_source" | "other_source" | null;
}

export interface AgentPluginKeptMember extends AgentPluginMemberRef {
  reason: "referenced_by_other_plugin" | "standalone";
}

export interface AgentPluginUninstallResult {
  removed_members: AgentPluginMemberRef[];
  kept_members: AgentPluginKeptMember[];
}

/** ``slug → owning plugins`` for library-card badges; a slug that belongs
 * to no plugin maps to ``[]``. */
export type AgentPluginMembershipMap = Record<
  string,
  { id: string; name: string }[]
>;

/** JSON install/preview input — exactly one of ``path`` / ``url`` /
 * ``market_item_id`` (``market:plugin:<slug>``). */
export interface AgentPluginInstallRequest {
  path?: string;
  url?: string;
  market_item_id?: string;
  on_conflict?: AgentPluginOnConflict;
}

/** Multipart install/preview input — a plugin zip (Agent Plugins layout, or
 * a ``.claude-plugin`` / ``.codebuddy-plugin`` layout, auto-detected). */
export interface AgentPluginZipInput {
  file: File;
  on_conflict?: AgentPluginOnConflict;
}

export type AgentPluginInstallInput =
  AgentPluginZipInput | AgentPluginInstallRequest;

export interface AgentPluginExport {
  blob: Blob;
  filename: string;
}

const SKILLS_TAG = "skills";

/** Plugin installs / removals add or drop skills + connectors, so drop the
 * cached skill catalog reads that the library pages share. */
function invalidateAfterPluginChange(): void {
  invalidateRequestCache({ tags: [SKILLS_TAG] });
}

function isZipInput(
  input: AgentPluginInstallInput,
): input is AgentPluginZipInput {
  return (
    typeof File !== "undefined" &&
    (input as AgentPluginZipInput).file instanceof File
  );
}

function postInput<T>(
  path: string,
  input: AgentPluginInstallInput,
): Promise<T> {
  if (isZipInput(input)) {
    const form = new FormData();
    form.append("file", input.file);
    if (input.on_conflict) form.append("on_conflict", input.on_conflict);
    return fetchJson<T>(path, { method: "POST", body: form });
  }
  const body: AgentPluginInstallRequest = {};
  if (input.path) body.path = input.path;
  if (input.url) body.url = input.url;
  if (input.market_item_id) body.market_item_id = input.market_item_id;
  if (input.on_conflict) body.on_conflict = input.on_conflict;
  return fetchJson<T>(path, { method: "POST", json: body });
}

function filenameFromDisposition(
  header: string | null,
  fallback: string,
): string {
  const m = header
    ? /filename\*?="?(?:UTF-8'')?([^";]+)"?/i.exec(header)
    : null;
  return m?.[1] ? decodeURIComponent(m[1]) : fallback;
}

export const pluginsApi = {
  list(): Promise<AgentPluginList> {
    return fetchJson("/v1/plugins");
  },

  get(pluginId: string): Promise<AgentPluginView> {
    return fetchJson(`/v1/plugins/${encodeURIComponent(pluginId)}`);
  },

  /** Dry run: manifest + members + same-slug conflicts. No side effects. */
  preview(input: AgentPluginInstallInput): Promise<AgentPluginPreview> {
    return postInput("/v1/plugins/preview", input);
  },

  async install(
    input: AgentPluginInstallInput,
  ): Promise<AgentPluginInstallResult> {
    const result = await postInput<AgentPluginInstallResult>(
      "/v1/plugins/install",
      input,
    );
    invalidateAfterPluginChange();
    return result;
  },

  async enable(pluginId: string): Promise<AgentPluginView> {
    const view = await fetchJson<AgentPluginView>(
      `/v1/plugins/${encodeURIComponent(pluginId)}/enable`,
      { method: "POST" },
    );
    invalidateAfterPluginChange();
    return view;
  },

  async disable(pluginId: string): Promise<AgentPluginView> {
    const view = await fetchJson<AgentPluginView>(
      `/v1/plugins/${encodeURIComponent(pluginId)}/disable`,
      { method: "POST" },
    );
    invalidateAfterPluginChange();
    return view;
  },

  /** Re-install from ``source_ref`` (market / url sources). */
  async update(
    pluginId: string,
    onConflict?: AgentPluginOnConflict,
  ): Promise<AgentPluginInstallResult> {
    const result = await fetchJson<AgentPluginInstallResult>(
      `/v1/plugins/${encodeURIComponent(pluginId)}/update`,
      { method: "POST", json: onConflict ? { on_conflict: onConflict } : {} },
    );
    invalidateAfterPluginChange();
    return result;
  },

  /** Reference-counted removal: members still referenced by another plugin
   * (or installed standalone) are kept, the rest removed. */
  async uninstall(pluginId: string): Promise<AgentPluginUninstallResult> {
    const result = await fetchJson<AgentPluginUninstallResult>(
      `/v1/plugins/${encodeURIComponent(pluginId)}`,
      { method: "DELETE" },
    );
    invalidateAfterPluginChange();
    return result;
  },

  /** Absolute URL of the Agent-Plugins-layout zip export. */
  exportUrl(pluginId: string): string {
    return `${_apiBase.replace(/\/+$/, "")}/v1/plugins/${encodeURIComponent(pluginId)}/export`;
  },

  /** Download the export zip — returns blob + filename so the caller can
   * trigger a browser download (core stays DOM-free). */
  async export(
    pluginId: string,
    fallbackName = "plugin",
  ): Promise<AgentPluginExport> {
    const { blob, headers } = await requestBlob(
      `/v1/plugins/${encodeURIComponent(pluginId)}/export`,
      { baseUrl: _apiBase, method: "GET" },
    );
    return {
      blob,
      filename: filenameFromDisposition(
        headers.get("Content-Disposition"),
        `${fallbackName}.zip`,
      ),
    };
  },

  /** Batch ``slug → owning plugins`` lookup for library-card badges. Empty
   * ``slugs`` short-circuits to ``{}`` without a request. */
  memberships(
    kind: AgentPluginMemberKind,
    slugs: string[],
  ): Promise<AgentPluginMembershipMap> {
    const unique = Array.from(new Set(slugs.filter((s) => !!s)));
    if (unique.length === 0) return Promise.resolve({});
    const search = new URLSearchParams();
    search.set("kind", kind);
    search.set("slugs", unique.join(","));
    return fetchJson(`/v1/plugins/memberships?${search.toString()}`);
  },
};
