/**
 * Client for the parser plugin + setup endpoints.
 *
 * Mirrors the schemas in `api/openapi.yaml` under the ``parser`` tag.
 * PR-2 currently uses only the setup-job endpoints (RapidOCR
 * authorization flow); PR-3 wires up the routing + per-plugin config
 * endpoints when the settings page is rewritten.
 *
 * Types are hand-written here to match the OpenAPI schema 1:1 — once
 * the `pnpm generate-types` pass runs in CI, the generated types
 * supersede these. Keep the shapes aligned in the meantime.
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setParserApiBase = (url: string): void => {
  _apiBase = url;
};

// ── Setup jobs ────────────────────────────────────────────────

export type SetupJobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface SetupRequirement {
  id: string;
  label_zh: string;
  kind: "credential" | "model_download";
  network_required: boolean;
  size_bytes: number | null;
  source: string | null;
  license_name: string | null;
  license_url: string | null;
  /** i18n key for ``label_zh`` (Phase 1 plugin model). Frontend
   *  prefers this and falls back to ``label_zh``. */
  label_key: string | null;
}

export interface SetupJobStatusResponse {
  setup_id: string;
  status: SetupJobStatus;
  downloaded_bytes: number;
  total_bytes: number | null;
  error: string | null;
  source: string | null;
  started_at: number | null;
  completed_at: number | null;
  updated_at: number | null;
  requirement: SetupRequirement | null;
}

export interface SetupJobListResponse {
  jobs: SetupJobStatusResponse[];
}

export interface StartSetupJobRequest {
  accept_license: boolean;
  confirmed_source: string;
}

// ── Plugin descriptors ────────────────────────────────────────

export type CapabilityStatus = "ready" | "needs_setup" | "unavailable";

export interface PluginCapability {
  kind: string;
  status: CapabilityStatus;
  setup: SetupRequirement | null;
  reason_zh: string | null;
}

export interface ConfigField {
  key: string;
  label_zh: string;
  type: "string" | "secret" | "bool" | "select" | "number";
  required: boolean;
  default: string | boolean | number | null;
  placeholder: string | null;
  help_zh: string | null;
  /** Optional doc URL — frontend renders this as a clickable link
   *  beside ``help_zh`` (typically "where to get a token"). */
  help_url: string | null;
  /** Plugin-contributed i18n keys (Phase 1 plugin model). Frontend
   *  prefers ``*_key`` over the inline ``*_zh`` strings; the inline
   *  values stay as fallback for plugins that haven't migrated. */
  label_key: string | null;
  help_key: string | null;
  placeholder_key: string | null;
  options: Array<[string, string]> | null;
  /** Parallel to ``options`` when present (same length); each entry,
   *  if non-null, is the i18n key for that option's label. */
  option_keys: Array<string | null> | null;
}

export interface PluginDescriptor {
  id: string;
  name_zh: string;
  description_zh: string;
  mode: "sync" | "async_poll";
  capabilities: PluginCapability[];
  config_schema: ConfigField[];
  supported_kinds: string[];
  is_configured: boolean;
  requires_secret: boolean;
  /** Plugin-contributed i18n keys (Phase 1 plugin model). */
  name_key: string | null;
  description_key: string | null;
  /** i18n namespace this plugin owns. Defaults to ``parser_<id>``
   *  on the wire. */
  i18n_namespace: string | null;
  /** Sort weight for the settings UI. Lower = earlier; default 50. */
  sort_weight: number;
}

export interface PluginsListResponse {
  plugins: PluginDescriptor[];
}

// ── Routing ───────────────────────────────────────────────────

export interface ParserRoutingResponse {
  primary_plugin_id: string;
  by_kind: Record<string, string>;
  fallback_to_local_on_error: boolean;
  effective_by_kind: Record<string, string>;
  locked_kinds: string[];
}

export interface ParserRoutingPatchRequest {
  primary_plugin_id?: string;
  by_kind?: Record<string, string>;
  fallback_to_local_on_error?: boolean;
}

const _baseFetchJson = createFetchJson(() => _apiBase);

/** Parser endpoints always send JSON — inject Content-Type automatically. */
async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  return _baseFetchJson(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
}

export interface PluginConfigResponse {
  plugin_id: string;
  enabled: boolean;
  has_secret: boolean;
  options: Record<string, unknown>;
}

export interface PluginConfigPatchRequest {
  enabled?: boolean;
  secret?: string;
  options?: Record<string, unknown>;
}

export interface PluginTestResponse {
  ok: boolean;
  plugin_id: string;
  error: string | null;
  latency_ms: number | null;
}

export const parserApi = {
  // Setup jobs
  listSetupJobs(): Promise<SetupJobListResponse> {
    return fetchJson("/v1/system/parser/setup");
  },
  getSetupJob(setupId: string): Promise<SetupJobStatusResponse> {
    return fetchJson(`/v1/system/parser/setup/${encodeURIComponent(setupId)}`);
  },
  startSetupJob(
    setupId: string,
    payload: StartSetupJobRequest,
  ): Promise<SetupJobStatusResponse> {
    return fetchJson(
      `/v1/system/parser/setup/${encodeURIComponent(setupId)}/start`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },
  cancelSetupJob(setupId: string): Promise<SetupJobStatusResponse> {
    return fetchJson(`/v1/system/parser/setup/${encodeURIComponent(setupId)}`, {
      method: "DELETE",
    });
  },

  // Plugins + routing (used by PR-3 settings rewrite)
  listPlugins(): Promise<PluginsListResponse> {
    return fetchJson("/v1/settings/parser/plugins");
  },
  getRouting(): Promise<ParserRoutingResponse> {
    return fetchJson("/v1/settings/parser");
  },
  patchRouting(
    payload: ParserRoutingPatchRequest,
  ): Promise<ParserRoutingResponse> {
    return fetchJson("/v1/settings/parser", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  // Per-plugin configuration + health check
  getPluginConfig(pluginId: string): Promise<PluginConfigResponse> {
    return fetchJson(
      `/v1/settings/parser/plugins/${encodeURIComponent(pluginId)}/config`,
    );
  },
  patchPluginConfig(
    pluginId: string,
    payload: PluginConfigPatchRequest,
  ): Promise<PluginConfigResponse> {
    return fetchJson(
      `/v1/settings/parser/plugins/${encodeURIComponent(pluginId)}/config`,
      {
        method: "PATCH",
        body: JSON.stringify(payload),
      },
    );
  },
  testPlugin(pluginId: string): Promise<PluginTestResponse> {
    return fetchJson(
      `/v1/settings/parser/plugins/${encodeURIComponent(pluginId)}/test`,
      { method: "POST" },
    );
  },
};
