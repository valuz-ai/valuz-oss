import type { ThirdPartyList, ThirdPartyPlugin } from "@valuz/core";

/** What the runtime remembers about a plugin it loaded. */
export interface LoadedPluginRecord {
  id: string;
  version: string;
  revision: number;
  sha256: string | null;
}

export interface ThirdPartyChanges {
  /** Loaded, but no longer wanted: removed, disabled, blocked, or safe mode. */
  unload: string[];
  /** Wanted and not loaded. */
  load: ThirdPartyPlugin[];
  /** Loaded, and the package or its revision changed: unload, then load. */
  reload: ThirdPartyPlugin[];
  /** Not loaded, and why (``disabled``, ``requires-unmet``, ``safe-mode`` …). */
  skipped: Array<{ id: string; reason: string }>;
}

/** Whether the list says this plugin should be running in the renderer. */
export function wantsLoad(plugin: ThirdPartyPlugin): boolean {
  return (
    plugin.enabled === true &&
    plugin.status === "enabled" &&
    typeof plugin.entry_url === "string" &&
    plugin.entry_url.length > 0
  );
}

function skipReason(plugin: ThirdPartyPlugin, safeMode: boolean): string {
  if (safeMode) return "safe-mode";
  if (plugin.status !== "enabled") return plugin.status;
  if (!plugin.enabled) return "disabled";
  return "no-entry";
}

const changedSince = (
  loaded: LoadedPluginRecord,
  plugin: ThirdPartyPlugin,
): boolean =>
  loaded.version !== plugin.version ||
  loaded.revision !== plugin.revision ||
  loaded.sha256 !== (plugin.sha256 ?? null);

/**
 * Diff the plugins that are loaded against the list the backend reports now.
 * Pure: the runtime applies the result (unloads first, then reloads, then
 * loads, in list order).
 */
export function planThirdPartyChanges(
  loaded: ReadonlyMap<string, LoadedPluginRecord>,
  list: Pick<ThirdPartyList, "safe_mode" | "plugins">,
): ThirdPartyChanges {
  const changes: ThirdPartyChanges = {
    unload: [],
    load: [],
    reload: [],
    skipped: [],
  };
  const present = new Set(list.plugins.map((plugin) => plugin.id));

  for (const id of loaded.keys()) {
    const plugin = list.plugins.find((entry) => entry.id === id);
    if (!present.has(id) || list.safe_mode || !plugin || !wantsLoad(plugin)) {
      changes.unload.push(id);
    }
  }

  for (const plugin of list.plugins) {
    const wanted = !list.safe_mode && wantsLoad(plugin);
    const current = loaded.get(plugin.id);
    if (!wanted) {
      changes.skipped.push({
        id: plugin.id,
        reason: skipReason(plugin, list.safe_mode),
      });
    } else if (!current) {
      changes.load.push(plugin);
    } else if (changedSince(current, plugin)) {
      changes.reload.push(plugin);
    }
  }
  return changes;
}
