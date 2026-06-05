import { createFetchJson } from "./fetch-json";

// Connector management client.
// Reads/writes /v1/connectors — builtin, directory, and custom MCP connectors.

import type {
  CatalogEntry,
  CatalogGroup,
  CatalogItem,
  CatalogConnector,
  CatalogField,
  ConnectorItem,
  OauthCredentialField,
  HeaderParam,
  ToolInfo,
} from "@valuz/shared";

export type {
  CatalogEntry,
  CatalogGroup,
  CatalogItem,
  CatalogConnector,
  CatalogField,
  ConnectorItem,
  OauthCredentialField,
  HeaderParam,
  ToolInfo,
};

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setConnectorsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface ConnectorListResponse {
  connectors: ConnectorItem[];
}

export interface StartOAuthRequest {
  credentials?: Record<string, string>;
}

export interface CatalogListResponse {
  items: CatalogEntry[];
}

export interface StartOAuthResponse {
  connector_id: string;
  authorization_url: string;
}

export interface TestConnectorResponse {
  ok: boolean;
  tool_count: number | null;
  // Tool names only — kept for existing callers (Settings "Test" toast).
  tools: string[];
  // Richer name + description list rendered by the Connectors page.
  tool_details: ToolInfo[];
  error: string | null;
}

export interface CreateConnectorResponse {
  id: string;
  slug: string;
  needs_auth: boolean;
  authorization_url: string | null;
}

export interface DiscoverConnectorRequest {
  url: string;
  transport?: string;
}

export interface DiscoverConnectorResponse {
  // "oauth" when RFC 8414 / OIDC metadata was found, else "none".
  auth_type: string;
  discovered: boolean;
  oauth_authorization_endpoint: string | null;
  oauth_token_endpoint: string | null;
  // null => server has no dynamic client registration; the user must supply
  // a pre-registered client_id / client_secret.
  oauth_registration_endpoint: string | null;
}

export interface CreateConnectorRequest {
  slug?: string;
  display_name: string;
  transport: string;
  description?: string | null;
  connector_type?: string;
  // HTTP / SSE
  url?: string | null;
  auth_type?: string;
  headers?: HeaderParam[] | null;
  params?: HeaderParam[] | null;
  // OAuth credentials (client_id / client_secret for manual registration)
  credentials?: Record<string, string>;
  // Manual OAuth endpoints — used when the server has no discoverable
  // metadata / no dynamic client registration (e.g. GitHub MCP).
  oauth_authorization_endpoint?: string | null;
  oauth_token_endpoint?: string | null;
  oauth_registration_endpoint?: string | null;
  // Stdio
  command?: string | null;
  args?: string[];
  working_dir?: string | null;
  env?: Record<string, string> | null;
}

export interface UpdateConnectorRequest {
  display_name?: string | null;
  description?: string | null;
  url?: string | null;
  auth_type?: string | null;
  headers?: HeaderParam[] | null;
  params?: HeaderParam[] | null;
  command?: string | null;
  args?: string[] | null;
  working_dir?: string | null;
  env?: Record<string, string> | null;
  enabled?: boolean | null;
}

const fetchJson = createFetchJson(() => _apiBase);

export const connectorsApi = {
  list(): Promise<ConnectorListResponse> {
    return fetchJson("/v1/connectors");
  },

  get(connectorId: string): Promise<ConnectorItem> {
    return fetchJson(`/v1/connectors/${encodeURIComponent(connectorId)}`);
  },

  create(payload: CreateConnectorRequest): Promise<CreateConnectorResponse> {
    return fetchJson("/v1/connectors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  discover(
    payload: DiscoverConnectorRequest,
  ): Promise<DiscoverConnectorResponse> {
    return fetchJson("/v1/connectors/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  update(
    connectorId: string,
    payload: UpdateConnectorRequest,
  ): Promise<ConnectorItem> {
    return fetchJson(`/v1/connectors/${encodeURIComponent(connectorId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  delete(connectorId: string): Promise<{ ok: boolean }> {
    return fetchJson(`/v1/connectors/${encodeURIComponent(connectorId)}`, {
      method: "DELETE",
    });
  },

  enable(connectorId: string): Promise<ConnectorItem> {
    return fetchJson(
      `/v1/connectors/${encodeURIComponent(connectorId)}/enable`,
      {
        method: "POST",
      },
    );
  },

  disable(connectorId: string): Promise<ConnectorItem> {
    return fetchJson(
      `/v1/connectors/${encodeURIComponent(connectorId)}/disable`,
      {
        method: "POST",
      },
    );
  },

  test(connectorId: string): Promise<TestConnectorResponse> {
    return fetchJson(`/v1/connectors/${encodeURIComponent(connectorId)}/test`, {
      method: "POST",
    });
  },

  listDirectory(): Promise<CatalogListResponse> {
    return fetchJson("/v1/connectors/recommended");
  },
};
