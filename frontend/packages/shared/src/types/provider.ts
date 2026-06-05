export type ProviderAuthType = "api_key" | "oauth";

/**
 * Kernel-side ``api_protocol`` enum (V5+bba3014, hyphenated user-facing
 * form). Mirrors ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` keys:
 *   * claude_agent → ["anthropic"]
 *   * codex        → ["openai-response"]
 *   * deepagents   → ["anthropic", "openai-completion", "gemini"]
 */
export type ApiProtocol =
  | "anthropic"
  | "openai-completion"
  | "openai-response"
  | "gemini";

export interface ProviderDescriptor {
  kind: string;
  display_name: string;
  supports_managed_provider: boolean;
  supports_custom_base_url: boolean;
  supports_connection_test: boolean;
  supports_protocol_selection: boolean;
  default_base_url: string;
  /** When ``supports_protocol_selection`` is True, the anthropic-shape
   *  endpoint may live at a different path than ``default_base_url`` +
   *  "/anthropic" (e.g. Moonshot — different prefix). Empty = use the
   *  append-/anthropic fallback. REP-107. */
  anthropic_base_url: string;
  default_model: string;
  model_options: string[];
  docs_url: string;
  /** REP-107: ``api_key`` (default — credentials live in secret_ref or
   *  account_connection) vs ``oauth`` (credentials live in an external
   *  CLI's keychain — claude /login, codex /login). The Add-Provider
   *  picker uses this to route OAuth-typed providers through the CLI
   *  login flow instead of the api_key form. */
  auth_type: ProviderAuthType;
  /** User-facing CLI command for OAuth providers ("claude /login").
   *  Empty for api_key providers. */
  oauth_login_command: string;
  /** Default api_protocol the provider should be seeded with — empty
   *  means "derive from provider_kind". */
  default_protocol: string;
}

export interface ProviderListItem {
  id: string;
  name: string;
  provider_kind: string;
  source: string;
  enabled: boolean;
  is_default: boolean;
  deletable: boolean;
  default_model: string | null;
  test_status: string;
  credential_source: string;
  auth_type: ProviderAuthType;
  /** User-facing hyphen form (``anthropic | openai-completion |
   *  openai-response | gemini``). Pre-V5+bba3014 rows may carry the
   *  legacy bare ``"openai"`` — treat as ``"openai-completion"``. */
  protocol: string | null;
  /** Single wire protocol the row currently pins to (kernel V5+bba3014
   *  4-value enum). Use ``compatible_protocols`` for compatibility
   *  checks — some upstreams (DeepSeek / Zhipu / Moonshot / MiniMax
   *  / custom) speak multiple. */
  effective_protocol: ApiProtocol;
  /** All wire protocols this provider can drive. DeepSeek-class
   *  upstreams return ``["anthropic", "openai-completion"]`` because
   *  they expose both shapes; the official OpenAI provider returns
   *  ``["openai-completion", "openai-response"]``; subscription
   *  providers and single-protocol upstreams return a one-element
   *  list. Drives the runtime-compatibility filter on the provider
   *  picker. */
  compatible_protocols: ApiProtocol[];
  /** Available model ids — surfaced on the list response so the
   *  Default-config picker can flatten (provider × model) entries
   *  without an N+1 fetch. */
  model_options: string[];
  /** Human-readable reason the provider is currently disabled. Only
   *  populated for ``source="system"`` (overlay-contributed) when
   *  ``enabled=false`` — e.g. "未登录 Valuz 账户". Other providers
   *  return ``null``. */
  unavailable_reason: string | null;
}

export interface ProviderDetail extends ProviderListItem {
  base_url: string | null;
  supports_custom_base_url: boolean;
  supports_connection_test: boolean;
}

export interface ConnectionTestResult {
  success: boolean;
  latency_ms: number | null;
  error_message: string | null;
}

export interface PingResponse {
  ok: string[];
  failed: { model: string; reason: string }[];
}

export interface ProbeModelsResponse {
  models: string[];
  suggested_default: string | null;
}

export interface DiscoverModelsResponse {
  provider_id: string;
  /** Model ids the upstream returned. */
  discovered: string[];
  /** Provider's full model_options after merging discovered ids with
   *  any user-added entries (sorted, de-duplicated). */
  merged: string[];
}
