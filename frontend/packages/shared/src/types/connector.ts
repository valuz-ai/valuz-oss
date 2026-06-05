// Connector data types (wire-level shapes shared by core + ui + apps).
// Moved from core/api/connectors-api.ts — pure data, no runtime deps.

// One header or query-param entry. `secret` is the per-entry credential
// marker; `value` is the final complete value (the client sends any scheme
// prefix itself). A secret entry comes back without `value` once the
// server-side split exists (Slice 3); until then everything is plaintext.
export interface HeaderParam {
  key: string;
  secret: boolean;
  value: string | null;
}

// One MCP tool exposed by a connector: name + (optional) description,
// sourced from the server's list_tools(). Drives the Connectors detail
// panel's tool list.
export interface ToolInfo {
  name: string;
  description: string | null;
}

export interface ConnectorItem {
  id: string;
  slug: string;
  display_name: string;
  description: string | null;
  connector_type: string;
  transport: string;
  url: string | null;
  auth_type: string;
  has_api_key: boolean;
  command: string | null;
  args: string[];
  working_dir: string | null;
  env: Record<string, string>;
  headers: HeaderParam[];
  params: HeaderParam[];
  enabled: boolean;
  status: string;
  tool_count: number | null;
  last_tested_at: number | null;
  error_message: string | null;
  created_at: number;
  updated_at: number;
}

export interface OauthCredentialField {
  key: string;
  label: string;
  placeholder: string | null;
  required: boolean;
  secret: boolean;
}

// Declared credential/config unit for a catalog connector. Server is
// authoritative over `secret`; `name` is the real header/param name the
// backend matches request entries against; `prefix` is a UI-prefill hint
// only (backend never applies it).
// Whether a field targets a header or a query-param is determined by
// which schema it sits in (header_schema / param_schema), not a per-entry
// `target` — invalid/mismatched targets are impossible by construction.
export interface CatalogField {
  key: string;
  label: string | Record<string, string> | null;
  placeholder: string | null;
  required: boolean;
  name: string;
  secret: boolean;
  prefix: string | null;
}

export interface CatalogConnector {
  slug: string;
  display_name: string;
  description: string | null;
  url: string;
  auth_type: string;
  transport: string;
  installed: boolean;
  oauth_credentials_schema: OauthCredentialField[];
  header_schema: CatalogField[];
  param_schema: CatalogField[];
  credentials_help_url: string | null;
  command: string | null;
  args: string[];
  working_dir: string | null;
  env: Record<string, string> | null;
}

export interface CatalogGroup {
  kind: "group";
  slug: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  categories: string[];
  connectors: CatalogConnector[];
}

export interface CatalogItem {
  kind: "connector";
  slug: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  categories: string[];
  url: string;
  auth_type: string;
  transport: string;
  installed: boolean;
  oauth_credentials_schema: OauthCredentialField[];
  header_schema: CatalogField[];
  param_schema: CatalogField[];
  credentials_help_url: string | null;
  command: string | null;
  args: string[];
  working_dir: string | null;
  env: Record<string, string> | null;
}

export type CatalogEntry = CatalogGroup | CatalogItem;
