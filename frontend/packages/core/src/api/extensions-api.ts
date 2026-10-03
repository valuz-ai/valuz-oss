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
  /**
   * Ids of the REQUIRED plugins that depend on this one. Non-empty means it is
   * locked on by them even though it is not ``required`` itself; empty when
   * nothing required leans on it. Absent from backends that predate it.
   */
  requiredBy?: string[];
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

/**
 * ``GET /v1/extensions/backend/state`` — what the running backend switched
 * off. Public (no auth) so the UI can read it before any session exists.
 */
export interface BackendExtensionState {
  /** Ids of the backend plugins that are not running in this process. */
  inactive: string[];
}

export interface BackendExtensionChange {
  /** The host cannot hot-apply a toggle: it takes effect at the next start. */
  application: "restart-required";
}

const BASE_PATH = "/v1/extensions/backend";

/** Boot-time read: a slow backend must not hold the first paint. */
const STATE_TIMEOUT_MS = 2500;

export const extensionsApi = {
  /**
   * Which backend plugins are inactive. Rejects on any failure (an older
   * backend answers 404, an offline one refuses the connection) — callers
   * that only use it to decide what to hide should treat a rejection as
   * "nothing is known to be off".
   */
  backendState(): Promise<BackendExtensionState> {
    return fetchJson(`${BASE_PATH}/state`, { timeoutMs: STATE_TIMEOUT_MS });
  },

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
