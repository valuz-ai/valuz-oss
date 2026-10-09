import type { components } from "./generated/plugins";
import { createFetchJson } from "./fetch-json";

/**
 * Canonical client for built-in plugins composed by the backend. Its schema is
 * generated from api/openapi.yaml; toggles apply at the next process start.
 * The public state endpoint supports deciding which UI plugins to load at boot.
 */

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setBuiltinPluginsApiBase = (url: string): void => {
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
export type BuiltinPluginStatus =
  "active" | "failed" | "disabled" | "pending" | "disposed";

type BuiltinPluginSchema = components["schemas"]["BuiltinPlugin"];

export interface BuiltinPluginRow extends BuiltinPluginSchema {
  id: string;
  /** What this boot actually did. */
  status: BuiltinPluginStatus;
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

export interface BuiltinPluginList {
  /** ``false``: a bare OSS app with no plugin host (``plugins`` is empty). */
  composed: boolean;
  /** ``false``: a cloud deployment — the operator manages pluginSettings. */
  editable: boolean;
  plugins: BuiltinPluginRow[];
  /** JSON Schema 2020-12 of each plugin's config, keyed by plugin id. */
  config_schemas: Record<string, Record<string, unknown>>;
}

/**
 * ``GET /v1/builtin-plugins/state`` — what the running backend switched
 * off. Public (no auth) so the UI can read it before any session exists.
 */
export interface BuiltinPluginState {
  /** Ids of the backend plugins that are not running in this process. */
  inactive: string[];
}

export interface BuiltinPluginChange {
  /** The host cannot hot-apply a toggle: it takes effect at the next start. */
  application: "restart-required";
}

const BASE_PATH = "/v1/builtin-plugins";

/** Boot-time read: a slow backend must not hold the first paint. */
const STATE_TIMEOUT_MS = 2500;

export const builtinPluginsApi = {
  /**
   * Which backend plugins are inactive. Rejects on any failure (an older
   * backend answers 404, an offline one refuses the connection) — callers
   * that only use it to decide what to hide should treat a rejection as
   * "nothing is known to be off".
   */
  backendState(): Promise<BuiltinPluginState> {
    return fetchJson(`${BASE_PATH}/state`, { timeoutMs: STATE_TIMEOUT_MS });
  },

  listBuiltinPlugins(): Promise<BuiltinPluginList> {
    return fetchJson(BASE_PATH);
  },

  /** Record the desired state; applied at the next start. */
  setBuiltinPluginEnabled(
    id: string,
    enabled: boolean,
  ): Promise<BuiltinPluginChange> {
    return fetchJson(`${BASE_PATH}/${encodeURIComponent(id)}/enabled`, {
      method: "POST",
      json: { enabled },
    });
  },
};
