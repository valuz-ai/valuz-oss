import type {
  ProviderAuthType,
  ApiProtocol,
  ProviderDescriptor,
  ProviderListItem,
  ProviderDetail,
  ConnectionTestResult,
  PingResponse,
  ProbeModelsResponse,
  DiscoverModelsResponse,
} from "@valuz/shared";
import { registerDynamicModelLabels } from "@valuz/shared";
import { createFetchJson } from "./fetch-json";

/** Project every provider's ``model_labels`` map into the global
 * ``modelLabel`` resolver overlay so downstream UIs (Composer, AgentDetailView,
 * ProjectContextPanel, …) render the admin-set human label without having to
 * pass providers in by hand. Safe to call repeatedly — last-write-wins. */
function _hydrateModelLabels(
  items: { model_labels?: Record<string, string> }[],
): void {
  for (const p of items) {
    if (p.model_labels) registerDynamicModelLabels(p.model_labels);
  }
}

export type {
  ProviderAuthType,
  ApiProtocol,
  ProviderDescriptor,
  ProviderListItem,
  ProviderDetail,
  ConnectionTestResult,
  PingResponse,
  ProbeModelsResponse,
  DiscoverModelsResponse,
};

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setProvidersApiBase = (url: string): void => {
  _apiBase = url;
};

export interface ProviderCreateRequest {
  name: string;
  provider_kind: string;
  base_url?: string | null;
  api_key?: string | null;
  default_model?: string | null;
  protocol?: string | null;
  /** Custom (``compatible``) channels only — the user-supplied model id
   *  list. Built-in channels ignore this field and discover their list
   *  via ``GET /v1/models``. */
  models?: string[] | null;
}

export interface ProviderUpdateRequest {
  name?: string | null;
  base_url?: string | null;
  api_key?: string | null;
  default_model?: string | null;
  protocol?: string | null;
  auth_type?: ProviderAuthType | null;
  /** Replace the stored ``model_ids`` for a custom channel. Ignored for
   *  built-in channels. */
  models?: string[] | null;
}

export interface ProviderValidateRequest {
  provider_kind: string;
  api_key?: string | null;
  base_url?: string | null;
  default_model?: string | null;
  protocol?: string | null;
}

const fetchJson = createFetchJson(() => _apiBase);

export const providersApi = {
  listProviders(): Promise<{ providers: ProviderDescriptor[] }> {
    return fetchJson("/v1/providers/config");
  },

  async list(): Promise<{ providers: ProviderListItem[] }> {
    const res = await fetchJson<{ providers: ProviderListItem[] }>(
      "/v1/providers",
    );
    _hydrateModelLabels(res.providers);
    return res;
  },

  async get(providerId: string): Promise<ProviderDetail> {
    const res = await fetchJson<ProviderDetail>(
      `/v1/providers/${encodeURIComponent(providerId)}`,
    );
    _hydrateModelLabels([res]);
    return res;
  },

  create(payload: ProviderCreateRequest): Promise<ProviderDetail> {
    return fetchJson("/v1/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  update(
    providerId: string,
    payload: ProviderUpdateRequest,
  ): Promise<ProviderDetail> {
    return fetchJson(`/v1/providers/${encodeURIComponent(providerId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  delete(providerId: string): Promise<void> {
    return fetchJson(`/v1/providers/${encodeURIComponent(providerId)}`, {
      method: "DELETE",
    });
  },

  test(providerId: string): Promise<ConnectionTestResult> {
    return fetchJson(`/v1/providers/${encodeURIComponent(providerId)}/test`, {
      method: "POST",
    });
  },

  validate(payload: ProviderValidateRequest): Promise<ConnectionTestResult> {
    return fetchJson("/v1/providers/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  /**
   * Mark a provider as the global default. ``defaultModel`` (optional) is
   * written onto the provider row AND synced to the app-setting keys, so both
   * the kernel's model_resolver (reads the ``is_default`` provider's
   * ``default_model``) and the composer/settings UI (read the app-setting
   * keys) agree on the user's pick.
   */
  setDefault(
    providerId: string,
    defaultModel?: string,
  ): Promise<{ provider_id: string; message: string }> {
    return fetchJson("/v1/providers/default", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider_id: providerId,
        ...(defaultModel !== undefined ? { default_model: defaultModel } : {}),
      }),
    });
  },

  /**
   * Enable an OAuth/CLI-subscription provider after the user has logged in via
   * the CLI (``claude /login`` / ``codex login``). The host can't see the CLI
   * keychain, so the frontend tells it the login succeeded; the backend flips
   * ``enabled`` + ``credential_source="cli_keychain"``.
   */
  enable(providerId: string): Promise<ProviderDetail> {
    return fetchJson(`/v1/providers/${encodeURIComponent(providerId)}/enable`, {
      method: "POST",
    });
  },

  /**
   * REP-107: probe the provider's upstream for the available model list
   * via ``GET /v1/models``. Returns both the freshly-discovered ids and
   * the merged set after combining with the provider's existing
   * ``model_options`` (no overwriting). Backend returns 502 + reason on
   * failure (timeout / 401 / 404 / unsupported endpoint); the provider
   * edit dialog should surface the reason and let the user fall back
   * to typing model ids by hand.
   */
  discoverModels(providerId: string): Promise<DiscoverModelsResponse> {
    return fetchJson(
      `/v1/providers/${encodeURIComponent(providerId)}/discover-models`,
      { method: "POST" },
    );
  },

  /**
   * Stateless model-list probe — used by the add-provider dialog's [test]
   * button to surface real models before the row is persisted. Backend
   * returns 422 + reason on failure (auth / network / etc.).
   */
  probeModels(payload: ProbeModelsRequest): Promise<ProbeModelsResponse> {
    return fetchJson("/v1/providers/probe-models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  /**
   * Stateless connection-test for a custom (compatible) channel. The
   * backend pings every model id with a 1-token chat / messages
   * request and returns the verified subset — caller persists ``ok``
   * and surfaces ``failed`` as per-model diagnostics. Hard 422 only
   * fires on bad-input (empty key / endpoint / models).
   */
  ping(payload: PingRequest): Promise<PingResponse> {
    return fetchJson("/v1/providers/ping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
};

export interface PingRequest {
  base_url: string;
  /** Omit when ``provider_id`` is supplied — the server pulls the
   *  stored key out of secret_store. Required otherwise. */
  api_key?: string | null;
  protocol?: string | null;
  models: string[];
  /** When set, the server uses the row's stored api_key (edit-mode
   *  re-test without forcing the user to re-type a saved key). */
  provider_id?: string | null;
}

export interface ProbeModelsRequest {
  provider_kind: string;
  api_key: string;
  base_url?: string;
  protocol?: string;
}
