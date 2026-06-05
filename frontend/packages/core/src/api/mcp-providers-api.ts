// MCP data-source catalog client.
// Reads /v1/providers/mcp — the list returned by `McpProviderService.list_providers`
// in the backend, projected together with each provider's connection status.
//
// Connection state for OAuth-backed providers (Reportify) is the same row as
// the Anthropic managed-model provider auth — completing the OAuth flow flips
// `connected` to true here without any extra dance.

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setMcpProvidersApiBase = (url: string): void => {
  _apiBase = url;
};

export interface McpProviderItem {
  slug: string;
  display_name: string;
  description: string | null;
  kind: string;
  auth_kind: string;
  oauth_provider_id: string | null;
  transport: string;
  modules: string[];
  enabled: boolean;
  connected: boolean;
}

export interface McpProviderListResponse {
  providers: McpProviderItem[];
}

const fetchJson = createFetchJson(() => _apiBase);

export const mcpProvidersApi = {
  list(): Promise<McpProviderListResponse> {
    return fetchJson("/v1/providers/mcp");
  },
};
