import type { Agent } from "./agents-api";
import { createFetchJson } from "./fetch-json";
import { requestBlob } from "./request";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setAgentTemplatesApiBase = (url: string): void => {
  _apiBase = url;
};

/** One role inside a team template, annotated with library state. */
export interface AgentTemplateRole {
  /** Fixed slug — the de-dup key when adding to the library. */
  slug: string;
  name: string;
  description: string;
  instructions: string;
  /** Avatar preset key. */
  avatar: string;
  skills: string[];
  connector_types: string[];
  /** Recommended runtime (claude_agent / codex), shown as a badge. Display
   *  metadata — added agents follow the user's default channel. */
  runtime: string;
  /** Recommended reasoning-effort budget (display badge); null = unset. */
  effort: string | null;
  /** True when an agent with this slug already exists in the caller's library. */
  in_library: boolean;
}

/** A ready-made team template (a set of roles), grouped by scenario. */
export interface AgentTemplate {
  id: string;
  /** Grouping label for the panel, e.g. 投研 / 内容创作. */
  scenario: string;
  name: string;
  description: string;
  /** Avatar preset key rendered on the team card. */
  icon: string;
  /** True when every role is already in the library (card shows 已添加). */
  added: boolean;
  roles: AgentTemplateRole[];
}

/** Result of adding a template — the de-dup counts + every role agent. */
export interface AddAgentTemplateResult {
  template_id: string;
  created: number;
  skipped: number;
  /** All of the template's role agents — newly created and pre-existing. */
  roles: Agent[];
}

/** Optional display header to brand an exported group. */
export interface ExportCollection {
  name?: string;
  description?: string;
  scenario?: string;
  icon?: string;
}

export interface ImportPreviewAgent {
  slug: string;
  name: string;
  description: string;
  /** Already present in the caller's library (will be skipped on confirm). */
  in_library: boolean;
}

export interface ImportPreviewSkill {
  slug: string;
  /** "embedded" (carried in the pack) | "bundled" (app-shipped, referenced). */
  source: string;
}

export interface ImportPreviewConnector {
  slug: string;
  display_name: string;
  requires_credentials: boolean;
  requires_setup: boolean;
  already_present: boolean;
}

export interface ImportPreviewCollection {
  id: string;
  name: string;
  description: string;
  scenario: string;
  icon: string;
}

/** Staged preview of an uploaded .valuzpack — what it contains + how it lands. */
export interface ImportPackPreview {
  preview_id: string;
  collection: ImportPreviewCollection | null;
  agents: ImportPreviewAgent[];
  skills: ImportPreviewSkill[];
  connectors: ImportPreviewConnector[];
}

export interface ConnectorToConfigure {
  slug: string;
  display_name: string;
  requires_credentials: boolean;
  requires_setup: boolean;
}

/** Result of committing an import — counts, agents, and the connectors that
 *  still need the user's attention (a key / local setup). */
export interface ImportPackResult {
  created: number;
  skipped: number;
  roles: Agent[];
  connectors_to_configure: ConnectorToConfigure[];
}

/** A downloaded archive plus its server-suggested filename. */
export interface ExportedPack {
  blob: Blob;
  filename: string;
}

const fetchJson = createFetchJson(() => _apiBase);

function filenameFromDisposition(header: string | null): string {
  const m = header ? /filename="?([^";]+)"?/.exec(header) : null;
  return m?.[1] ?? "agents.valuzpack";
}

export const agentTemplatesApi = {
  listTemplates(): Promise<{ templates: AgentTemplate[] }> {
    return fetchJson("/v1/agent-templates");
  },

  /** Copy a template's missing roles into the caller's library (idempotent). */
  addTemplate(templateId: string): Promise<AddAgentTemplateResult> {
    return fetchJson(
      `/v1/agent-templates/${encodeURIComponent(templateId)}:add`,
      { method: "POST" },
    );
  },

  /** Export library agents as a .valuzpack — returns the blob + filename for
   *  the caller to trigger a browser download (core stays DOM-free). */
  async exportPack(
    agentSlugs: string[],
    collection?: ExportCollection,
  ): Promise<ExportedPack> {
    const { blob, headers } = await requestBlob("/v1/agent-packs/export", {
      baseUrl: _apiBase,
      method: "POST",
      json: { agent_slugs: agentSlugs, collection },
    });
    return {
      blob,
      filename: filenameFromDisposition(headers.get("Content-Disposition")),
    };
  },

  /** Upload a .valuzpack and stage it — returns a preview to confirm with. */
  importPack(file: File): Promise<ImportPackPreview> {
    const form = new FormData();
    form.append("file", file);
    return fetchJson("/v1/agent-packs/import", { method: "POST", body: form });
  },

  /** Commit a staged import by preview_id. */
  confirmImportPack(previewId: string): Promise<ImportPackResult> {
    return fetchJson("/v1/agent-packs/import/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preview_id: previewId }),
    });
  },
};
