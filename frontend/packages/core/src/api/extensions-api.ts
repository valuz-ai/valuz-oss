import { createFetchJson } from "./fetch-json";

/**
 * Client for ``/v1/extensions`` — the backend extensions (plugins) the running
 * process composed (plugin-architecture design §8).
 *
 * ``/v1/extensions/*`` is not in ``api/openapi.yaml`` yet, so the types below
 * mirror ``valuz_agent/api/routes/extensions.py`` and ``plugin_host.PluginInfo``
 * by hand, in the same way ``plugins-api.ts`` / ``dsh-plugins-api.ts`` do.
 *
 * Toggles are recorded and applied at the **next start** (FastAPI cannot drop
 * routes from a live app), so a successful change answers
 * ``{ application: "restart-required" }``.
 *
 * Failures reach the caller as ``ApiError`` whose ``message`` is the server's
 * ``detail``: ``403`` = a cloud deployment (the operator manages extensions),
 * ``404`` = unknown id / no plugin host, ``409`` = disabling a required plugin.
 */

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setExtensionsApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

/**
 * What this boot did with the plugin — ``plugin_host.PluginStatus``:
 * ``active`` / ``failed`` / ``pending`` are the lifecycle, ``disabled`` was
 * switched off for this start (never applied), ``disposed`` was applied and
 * then unloaded. An enabled plugin whose need is unavailable is ``failed``.
 * Consumers must treat the set as open.
 */
export type BackendExtensionStatus =
  "active" | "failed" | "disabled" | "pending" | "disposed";

export interface BackendExtensionRow {
  id: string;
  /** What this boot actually did. */
  status: BackendExtensionStatus;
  /** Cannot be switched off. */
  required: boolean;
  /** Capability keys the plugin needs from other plugins. */
  needs: string[];
  /** Capability keys the plugin provides. */
  provides: string[];
  /** The entitlement key that gates it (paid boundary), when it has one. */
  entitlement: string | null;
  /** Why it failed. */
  error: string | null;
  hasConfig: boolean;
  /** The persisted desire, applied at the next start. */
  desiredEnabled: boolean;
  boundPorts: string[];
}

export interface BackendExtensionList {
  /** ``false``: a bare OSS app with no plugin host (``plugins`` is empty). */
  composed: boolean;
  /** ``false``: a cloud deployment — the operator manages extensions. */
  editable: boolean;
  plugins: BackendExtensionRow[];
  /** JSON Schema 2020-12 of each plugin's config, keyed by plugin id. */
  config_schemas: Record<string, Record<string, unknown>>;
}

export interface BackendExtensionChange {
  /** The host cannot hot-apply a toggle: it takes effect at the next start. */
  application: "restart-required";
}

const BASE_PATH = "/v1/extensions/backend";

export const extensionsApi = {
  listBackendExtensions(): Promise<BackendExtensionList> {
    return fetchJson(BASE_PATH);
  },

  /** Record the desired state; applied at the next start. */
  setBackendExtensionEnabled(
    id: string,
    enabled: boolean,
  ): Promise<BackendExtensionChange> {
    return fetchJson(`${BASE_PATH}/${encodeURIComponent(id)}/enabled`, {
      method: "POST",
      json: { enabled },
    });
  },
};
