import { Context } from "@deepseek-ai/cordis";

import { useRegistryStore } from "../edition/registry-store";
import { useCategoryRegistry } from "../hooks/use-resource-categories";
import type {
  ValuzPluginContext,
  PluginHost,
  PluginRecord,
  PluginRegistry,
  PluginSkipReason,
  ValuzPlugin,
} from "./types";

type Fiber = ReturnType<Context["plugin"]>;

interface Entry {
  record: PluginRecord;
  fiber?: Fiber;
}

/** Identity helper that types a plugin literal. */
export function definePlugin(plugin: ValuzPlugin): ValuzPlugin {
  return plugin;
}

/**
 * Create an isolated plugin host. Lifetimes are delegated to Cordis: each
 * plugin runs in its own fiber, every registration is an effect on that fiber,
 * and disposing the fiber (unload, or a failed ``apply``) runs the disposers in
 * reverse order.
 */
export function createPluginHost(): PluginHost {
  const root = new Context();
  const entries = new Map<string, Entry>();
  const listeners = new Set<() => void>();
  let snapshot: readonly PluginRecord[] = [];

  const setRecord = (id: string, patch: Partial<PluginRecord>) => {
    const entry = entries.get(id);
    if (!entry) return;
    entry.record = { ...entry.record, ...patch };
    snapshot = [...entries.values()].map((e) => e.record);
    for (const listener of listeners) listener();
  };

  function makeContext(id: string, scope: Context): ValuzPluginContext {
    // ``ctx.effect`` requires a disposer; tolerate setups that return nothing.
    const hold = (dispose: () => void) => {
      scope.effect(() => dispose);
    };

    const store = () => useRegistryStore.getState();

    const registry: PluginRegistry = {
      slot: (name, registration) =>
        hold(store().registerSlot(name, registration)),
      route: (route, placement) => hold(store().registerRoute(route, placement)),
      removeRoute: (routeId) => hold(store().registerRouteRemoval(routeId)),
      settingsSection: (section, placement) =>
        hold(store().registerSettingsSection(section, placement)),
      removeSettingsSection: (sectionId) =>
        hold(store().registerSettingsSectionRemoval(sectionId)),
      projectPanel: (panel) => hold(store().registerProjectPanel(panel)),
      navItem: (item, placement) =>
        hold(store().registerNavItem(item, placement)),
      removeNavItem: (itemId) => hold(store().registerNavItemRemoval(itemId)),
      navGroup: (group, placement) =>
        hold(store().registerNavGroup(group, placement)),
      service: (descriptor) => hold(store().registerService(descriptor)),
      categories: (type, categories) =>
        hold(useCategoryRegistry.getState().contribute(type, id, categories)),
      suppress: (surface) => {
        store().setSuppressed(surface, true);
        hold(() => store().setSuppressed(surface, false));
      },
      capabilities: (patch) => hold(store().registerCapabilities(patch)),
    };

    return {
      pluginId: id,
      registry,
      effect: (setup) => {
        scope.effect(() => {
          const dispose = setup();
          return () => dispose?.();
        });
      },
      legacy: (name, install) => {
        install();
        const entry = entries.get(id);
        if (entry) setRecord(id, { legacy: [...entry.record.legacy, name] });
      },
      plugin: (child) => mount(child, scope),
    };
  }

  async function mount(
    plugin: ValuzPlugin,
    parent: Context,
  ): Promise<PluginRecord> {
    const existing = entries.get(plugin.id);
    if (
      existing &&
      (existing.record.status === "active" ||
        existing.record.status === "loading")
    ) {
      console.warn(
        `[plugin-host] plugin "${plugin.id}" is already loaded; ignoring the duplicate`,
      );
      return existing.record;
    }

    const entry: Entry = {
      record: { id: plugin.id, status: "loading", legacy: [] },
    };
    // Re-inserting moves a reloaded plugin to the end of the load order.
    entries.delete(plugin.id);
    entries.set(plugin.id, entry);
    setRecord(plugin.id, {});

    const fiber = parent.plugin({
      name: plugin.id,
      apply: async (scope: Context) => {
        // Registered first so it runs last on unload, after every
        // contribution has been withdrawn.
        scope.effect(() => () => {
          const current = entries.get(plugin.id);
          if (current !== entry || entry.record.status !== "active") return;
          if (entry.record.legacy.length > 0) {
            console.warn(
              `[plugin-host] plugin "${plugin.id}" unloaded, but its legacy installs stay in effect: ${entry.record.legacy.join(", ")}`,
            );
          }
          setRecord(plugin.id, { status: "disposed" });
        });
        await plugin.apply(makeContext(plugin.id, scope));
      },
    });
    entry.fiber = fiber;

    try {
      await fiber;
      setRecord(plugin.id, { status: "active" });
    } catch (error) {
      console.error(
        `[plugin-host] plugin "${plugin.id}" failed to load`,
        error,
      );
      setRecord(plugin.id, { status: "failed", error });
      // Cordis keeps a FAILED fiber attached to its parent; release it so a
      // later reload of the same id starts clean.
      await fiber.dispose().catch(() => undefined);
    }
    return entry.record;
  }

  return {
    load: (plugin) => mount(plugin, root),
    async loadAll(plugins) {
      const records: PluginRecord[] = [];
      for (const plugin of plugins) {
        records.push(await mount(plugin, root));
      }
      return records;
    },
    skip(id, reason: PluginSkipReason) {
      const existing = entries.get(id);
      if (
        existing &&
        (existing.record.status === "active" ||
          existing.record.status === "loading")
      ) {
        return existing.record;
      }
      // Re-inserting keeps the list in the order the caller walked the plugins.
      entries.delete(id);
      entries.set(id, { record: { id, status: reason, legacy: [] } });
      setRecord(id, {});
      return entries.get(id)!.record;
    },
    async unload(id) {
      const entry = entries.get(id);
      if (!entry?.fiber || entry.record.status !== "active") return;
      await entry.fiber.dispose();
    },
    get: (id) => entries.get(id)?.record,
    list: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** The app-wide host that editions load their plugins into. */
export const pluginHost: PluginHost = createPluginHost();
