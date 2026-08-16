import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setMarketplaceApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

/** Mirrors ``api/openapi.yaml`` → Marketplace* schemas (hand-synced). */
export type MarketplaceItemType =
  | "skill"
  | "agent_template"
  | "agent_team_template"
  | "connector"
  | "plugin";
export type MarketplaceSource =
  | "skillhub"
  | "valuz_official"
  | "modelscope"
  | "redskill"
  | "pluginmarket";
export type MarketplaceBadge =
  | "free_install"
  | "requires_api_key"
  | "third_party_cost"
  | "reviewed_skillhub"
  | "reviewed_valuz"
  | "community"
  | "verified"
  | "locked";
export type MarketplaceInstallTarget =
  | "skill_library"
  | "agent_library"
  | "agent_library_project"
  | "connector_library"
  | "plugin_library";
/** Derived (never authored) plugin composition — ``skills_only`` = 技能套件,
 * ``with_connectors`` = plugin shipping an ``mcp.json``. */
export type MarketplacePluginComposition = "skills_only" | "with_connectors";
export type MarketplaceConnectorRequirementKind =
  "required" | "optional" | "api_key" | "cost";

export interface MarketplaceStats {
  downloads?: number | null;
  stars?: number | null;
  installs?: number | null;
  views?: number | null;
}

export interface MarketplaceTeamMember {
  slug?: string | null;
  name: string;
  role: string;
  lead: boolean;
  skill_count?: number | null;
}

export interface MarketplaceConnectorRequirement {
  name: string;
  requirement: MarketplaceConnectorRequirementKind;
}

/** One member of a ``plugin`` market item (detail only). Skills and
 * connectors share the same row shape, discriminated by ``kind``. */
export interface MarketplacePluginMember {
  kind: "skill" | "connector";
  slug: string;
  name: string;
  description?: string | null;
  meta_version?: string | null;
  path?: string | null;
}

export interface MarketplaceConnectorConfigField {
  key: string;
  name: string;
  target: "env" | "header" | "param";
  label: string;
  required: boolean;
  secret: boolean;
  placeholder?: string | null;
  prefix?: string | null;
}

export interface MarketplaceConnectorConfig {
  slug: string;
  transport: "stdio" | "http" | "sse";
  url?: string | null;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  headers: Record<string, string>;
  params: Record<string, string>;
  auth_type: "none" | "bearer" | "oauth";
  fields: MarketplaceConnectorConfigField[];
  supported: boolean;
  unsupported_reason?: string | null;
}

export interface MarketplaceFileEntry {
  path: string;
  size?: number | null;
  sha256?: string | null;
}

export interface MarketplaceSecurityProviderReport {
  provider: string;
  status: string;
  url?: string | null;
}

export interface MarketplaceSecurityReport {
  status: "benign" | "unknown" | "flagged";
  summary: string;
  reports: MarketplaceSecurityProviderReport[];
}

export interface MarketplaceEvaluationDimension {
  key:
    "trust" | "reliability" | "adaptability" | "convention" | "effectiveness";
  code: "T" | "R" | "A" | "C" | "E";
  label: string;
  score?: number | null;
  summary?: string | null;
}

export interface MarketplaceEvaluationReport {
  system: "TRACE";
  score?: number | null;
  rating?: string | null;
  summary?: string | null;
  dimensions: MarketplaceEvaluationDimension[];
}

/** Normalized card shape shared by every marketplace source. ``id`` is a
 * stable ``{source}:{type}:{ref}`` string. */
export interface MarketplaceItem {
  id: string;
  type: MarketplaceItemType;
  source: MarketplaceSource;
  source_ref: string;
  title: string;
  subtitle?: string | null;
  description: string;
  icon?: string | null;
  category?: string | null;
  category_label?: string | null;
  subcategories: string[];
  badges: MarketplaceBadge[];
  stats: MarketplaceStats;
  version?: string | null;
  runtime?: string | null;
  /** Teams: total skills across members. Plugins: number of member skills. */
  skill_count?: number | null;
  /** Plugins only: number of member connectors (``mcp.json`` servers). */
  connector_count?: number | null;
  /** Plugins only: derived composition. */
  composition?: MarketplacePluginComposition | null;
  members?: MarketplaceTeamMember[] | null;
  install_target: MarketplaceInstallTarget;
  installed: boolean;
  locked?: boolean;
}

export interface MarketplaceItemDetail extends MarketplaceItem {
  owner?: string | null;
  origin_url?: string | null;
  updated_at?: string | null;
  instructions?: string | null;
  workflow?: string[] | null;
  deliverables?: string[] | null;
  usage_notes?: string[] | null;
  bound_skills?: string[] | null;
  connectors?: MarketplaceConnectorRequirement[] | null;
  files?: MarketplaceFileEntry[] | null;
  security?: MarketplaceSecurityReport | null;
  evaluation?: MarketplaceEvaluationReport | null;
  connector_config?: MarketplaceConnectorConfig | null;
  /** Plugins only: the ``plugin.json`` manifest object verbatim. */
  plugin_manifest?: Record<string, unknown> | null;
  /** Opaque, type-varies-by-`type` install payload from the market index.
   * Not consumed by the frontend — carried for type parity with the backend. */
  install_manifest?: Record<string, unknown> | null;
}

export interface MarketplaceItemList {
  items: MarketplaceItem[];
  total: number;
  page: number;
  page_size: number;
  /** True when the market index was unreachable and results are empty/partial. */
  degraded: boolean;
}

export interface MarketplaceSubcategory {
  key: string;
  label: string;
}

export interface MarketplaceCategory {
  key: string;
  label: string;
  count?: number | null;
  subcategories?: MarketplaceSubcategory[];
}

export interface MarketplaceCategoryList {
  categories: MarketplaceCategory[];
  degraded: boolean;
}

export interface MarketplaceInstallResult {
  item_id: string;
  status: "installed" | "already_installed";
  installed_ref?: string | null;
  created?: number | null;
  skipped?: number | null;
}

export interface MarketplaceListParams {
  type: MarketplaceItemType;
  category?: string;
  subcategory?: string;
  source?: MarketplaceSource;
  /** ``type=plugin`` only: restrict to one derived composition. */
  composition?: MarketplacePluginComposition;
  q?: string;
  page?: number;
  page_size?: number;
}

/**
 * Plugin members ride on the same ``members`` wire field a team roster uses
 * (the shapes differ; ``kind`` is the discriminant). Read them through this
 * helper so team code keeps its ``MarketplaceTeamMember`` typing untouched.
 */
export function marketplacePluginMembers(
  detail: MarketplaceItemDetail | MarketplaceItem | null | undefined,
): MarketplacePluginMember[] {
  const raw = (detail as { members?: unknown } | null | undefined)?.members;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (m): m is MarketplacePluginMember =>
      !!m &&
      typeof m === "object" &&
      ((m as { kind?: unknown }).kind === "skill" ||
        (m as { kind?: unknown }).kind === "connector") &&
      typeof (m as { slug?: unknown }).slug === "string",
  );
}

export const marketplaceApi = {
  categories(
    kind: "skill" | "agent" | "connector" | "plugin",
  ): Promise<MarketplaceCategoryList> {
    return fetchJson(`/v1/marketplace/categories?kind=${kind}`);
  },

  list(params: MarketplaceListParams): Promise<MarketplaceItemList> {
    const search = new URLSearchParams();
    search.set("type", params.type);
    if (params.category) search.set("category", params.category);
    if (params.subcategory) search.set("subcategory", params.subcategory);
    if (params.source) search.set("source", params.source);
    if (params.composition) search.set("composition", params.composition);
    if (params.q) search.set("q", params.q);
    if (params.page) search.set("page", String(params.page));
    if (params.page_size) search.set("page_size", String(params.page_size));
    return fetchJson(`/v1/marketplace/items?${search.toString()}`);
  },

  get(itemId: string): Promise<MarketplaceItemDetail> {
    return fetchJson(`/v1/marketplace/items/${encodeURIComponent(itemId)}`);
  },

  install(itemId: string): Promise<MarketplaceInstallResult> {
    return fetchJson(
      `/v1/marketplace/items/${encodeURIComponent(itemId)}:install`,
      {
        method: "POST",
      },
    );
  },
};
