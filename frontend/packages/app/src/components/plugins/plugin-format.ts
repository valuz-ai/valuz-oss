import type {
  AgentPluginComposition,
  AgentPluginFormat,
  AgentPluginSource,
} from "@valuz/core";

/** i18n key for a derived plugin composition (library ``plugin.*`` namespace). */
export const PLUGIN_COMPOSITION_LABEL_KEYS: Record<AgentPluginComposition, string> = {
  skills_only: "plugin.compositionSkillsOnly",
  with_connectors: "plugin.compositionWithConnectors",
};

/** i18n key for where an installed plugin came from. */
export const PLUGIN_SOURCE_LABEL_KEYS: Record<AgentPluginSource, string> = {
  market: "plugin.sourceMarket",
  local_dir: "plugin.sourceLocalDir",
  zip: "plugin.sourceZip",
  url: "plugin.sourceUrl",
  claude_plugin: "plugin.sourceClaude",
  codebuddy_plugin: "plugin.sourceCodebuddy",
};

/** i18n key for the package layout a preview detected. */
export const PLUGIN_FORMAT_LABEL_KEYS: Record<AgentPluginFormat, string> = {
  agent_plugins: "plugin.formatAgentPlugins",
  claude_plugin: "plugin.formatClaude",
  codebuddy_plugin: "plugin.formatCodebuddy",
};

/** Read a string field off a ``plugin.json`` manifest object; ``author`` may
 * be a nested ``{name, email, url}`` object per the Agent Plugins schema. */
export function manifestString(
  manifest: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = manifest?.[key];
  if (typeof value === "string") return value.trim() || null;
  if (key === "author" && value && typeof value === "object") {
    const name = (value as { name?: unknown }).name;
    return typeof name === "string" && name.trim() ? name.trim() : null;
  }
  return null;
}

export function manifestKeywords(
  manifest: Record<string, unknown> | null | undefined,
): string[] {
  const value = manifest?.keywords;
  return Array.isArray(value)
    ? value.filter((k): k is string => typeof k === "string" && !!k.trim())
    : [];
}

/** Local-path-or-URL input → the JSON install body field it belongs in. */
export function pluginLocatorInput(raw: string): { path?: string; url?: string } {
  const value = raw.trim();
  if (/^https?:\/\//i.test(value)) return { url: value };
  return { path: value };
}
