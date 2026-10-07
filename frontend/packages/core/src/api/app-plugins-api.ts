import type { components } from "./generated/plugins";
import { createFetchJson } from "./fetch-json";
import { requestRaw } from "./request";

/**
 * Client for third-party plugins on a local deployment (ADR-034): install,
 * dev-link, lifecycle, logs, config, storage, plugin-declared automations and
 * the frontend assets the renderer loads. The contract is docs task card 04
 * (commercial repo) §D; the routes are ``valuz_agent/api/routes/app_plugins.py``.
 *
 * Every route answers ``403`` on a cloud deployment.
 */

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setAppPluginsApiBase = (url: string): void => {
  _apiBase = url;
};

/** The base the plugin assets (``entry_url``, ``style_urls``) resolve against. */
export const getAppPluginsApiBase = (): string => _apiBase;

const fetchJson = createFetchJson(() => _apiBase);

export type LocalizedText = string | Record<string, string>;

export type AppPluginStatus = components["schemas"]["AppPlugin"]["status"];


export type AppPluginSourceKind = "file" | "url" | "dev" | "catalog";

export interface AppPluginSource {
  kind: AppPluginSourceKind;
  path?: string;
  url?: string;
  scope?: string;
  item_id?: string;
}

export interface AppPluginAutomationDecl {
  name: string;
  runtime: string;
  trigger?: unknown;
  automation_id?: string | null;
}

export interface AppPlugin extends Omit<components["schemas"]["AppPlugin"], "name" | "description" | "publisher" | "source" | "automations"> {
  id: string;
  version: string;
  name: LocalizedText;
  description?: LocalizedText | null;
  publisher: { name: string; url?: string; email?: string };
  source: AppPluginSource;
  status: AppPluginStatus;
  status_reason: string | null;
  enabled: boolean;
  permissions: string[];
  requires: string[];
  unmet_requires: string[];
  engines: Record<string, string>;
  /** Relative to the API base, e.g. ``/v1/app-plugin-assets/<id>/<revision>/frontend/index.js``. */
  entry_url: string | null;
  style_urls: string[];
  /** ``{ "zh-CN": {...}, "en-US": {...} }`` — the package's locale files, inlined. */
  locales: Record<string, Record<string, unknown>>;
  config_schema: Record<string, unknown> | null;
  automations: AppPluginAutomationDecl[];
  revision: number;
  dev_path: string | null;
  sha256: string | null;
  installed_at: number | null;
}

export interface AppPluginList {
  api_version: string;
  safe_mode: boolean;
  safe_mode_reason: string | null;
  generation: number;
  plugins: AppPlugin[];
}

/** Either a local path (zip or directory) or an https URL of a zip. */
export type AppPluginSourceSpec = { source_path: string } | { url: string };

export interface AppPluginInspection {
  manifest: Record<string, unknown> | null;
  sha256: string | null;
  size: number | null;
  permissions: string[];
  requires: string[];
  unmet_requires: string[];
  errors: string[];
  warnings: string[];
  has_backend: boolean;
  existing: { version: string } | null;
  added_permissions: string[];
}

export interface AppPluginLogEntry {
  ts: number;
  level: string;
  message: string;
  source: "frontend" | "backend" | "audit" | string;
}

export interface AppPluginStorageItem {
  key: string;
  size: number;
  updated_at: number;
}

export interface AppPluginAutomation {
  name: string;
  automation_id: string | null;
  runtime: string;
  trigger: unknown;
  status: string | null;
  latest_run: Record<string, unknown> | null;
}

const BASE = "/v1/app-plugins";
const id = (pluginId: string) => `${BASE}/${encodeURIComponent(pluginId)}`;

export const appPluginsApi = {
  list(): Promise<AppPluginList> {
    return fetchJson(BASE);
  },

  /** Resolves when ``generation`` moves past ``since`` or after ``timeoutS``. */
  watch(
    since: number,
    timeoutS = 25,
    signal?: AbortSignal,
  ): Promise<{ generation: number }> {
    return fetchJson(`${BASE}/watch?since=${since}&timeout=${timeoutS}`, {
      timeoutMs: (timeoutS + 10) * 1000,
      signal,
    });
  },

  setSafeMode(
    enabled: boolean,
    reason?: string,
  ): Promise<{ safe_mode: boolean }> {
    return fetchJson(`${BASE}/safe-mode`, {
      method: "POST",
      json: { enabled, reason },
    });
  },

  inspect(source: AppPluginSourceSpec): Promise<AppPluginInspection> {
    return fetchJson(`${BASE}/inspect`, { method: "POST", json: source });
  },

  install(
    source: AppPluginSourceSpec,
    opts: { expected_sha256?: string; enable?: boolean } = {},
  ): Promise<{ plugin: AppPlugin; updated_from: string | null }> {
    return fetchJson(`${BASE}/install`, {
      method: "POST",
      json: { ...source, ...opts },
    });
  },

  devLink(path: string): Promise<{ plugin: AppPlugin }> {
    return fetchJson(`${BASE}/dev-link`, { method: "POST", json: { path } });
  },

  reload(pluginId: string): Promise<{ plugin: AppPlugin }> {
    return fetchJson(`${id(pluginId)}/reload`, { method: "POST" });
  },

  setEnabled(
    pluginId: string,
    enabled: boolean,
  ): Promise<{ plugin: AppPlugin }> {
    return fetchJson(`${id(pluginId)}/${enabled ? "enable" : "disable"}`, {
      method: "POST",
    });
  },

  uninstall(
    pluginId: string,
    purgeData = false,
  ): Promise<{ removed: boolean; automations_deleted: number }> {
    return fetchJson(`${id(pluginId)}?purge_data=${purgeData}`, {
      method: "DELETE",
    });
  },

  logs(
    pluginId: string,
    limit = 200,
  ): Promise<{ entries: AppPluginLogEntry[] }> {
    return fetchJson(`${id(pluginId)}/logs?limit=${limit}`);
  },

  writeLog(pluginId: string, level: string, message: string): Promise<void> {
    return fetchJson(`${id(pluginId)}/logs`, {
      method: "POST",
      json: { level, message },
      headers: { "X-Valuz-App-Plugin-Id": pluginId },
    });
  },

  getConfig(
    pluginId: string,
  ): Promise<{
    values: Record<string, unknown>;
    schema: Record<string, unknown> | null;
  }> {
    return fetchJson(`${id(pluginId)}/config`);
  },

  putConfig(
    pluginId: string,
    values: Record<string, unknown>,
  ): Promise<{
    values: Record<string, unknown>;
    schema: Record<string, unknown> | null;
  }> {
    return fetchJson(`${id(pluginId)}/config`, {
      method: "PUT",
      json: { values },
    });
  },

  storageList(
    pluginId: string,
    prefix = "",
  ): Promise<{ items: AppPluginStorageItem[] }> {
    return fetchJson(
      `${id(pluginId)}/storage?prefix=${encodeURIComponent(prefix)}`,
    );
  },

  automations(
    pluginId: string,
  ): Promise<{ automations: AppPluginAutomation[] }> {
    return fetchJson(`${id(pluginId)}/automations`);
  },

  validate(path: string): Promise<{
    ok: boolean;
    errors: string[];
    warnings: string[];
    manifest: Record<string, unknown> | null;
  }> {
    return fetchJson(`${BASE}/validate`, { method: "POST", json: { path } });
  },

  pack(
    path: string,
    outDir?: string,
  ): Promise<{ path: string; sha256: string; size: number }> {
    return fetchJson(`${BASE}/pack`, {
      method: "POST",
      json: { path, out_dir: outDir },
    });
  },

  /** Raw text of a plugin asset (the renderer turns it into a Blob URL). */
  async assetText(relativeUrl: string): Promise<string> {
    const response = await requestRaw(relativeUrl, { baseUrl: _apiBase });
    return response.text();
  },
};
