import type {
  DesktopRouteModule,
  Edition,
  FeatureFlags,
  ServiceDescriptor,
  SettingsSectionModule,
  ProjectPanelModule,
} from './profile'
import { useRegistryStore } from './registry-store'

/**
 * Plugin manifest — the contract between a plugin bundle and the host shell.
 *
 * Plugins are ESM modules whose default export is a PluginManifest. They run
 * in the host React root (no sandbox), so they must be trusted. Signature /
 * sandbox enforcement belongs at the delivery / loader layer, not here.
 */
export interface PluginManifest {
  id: string
  version: string
  routes?: DesktopRouteModule[]
  settingsSections?: SettingsSectionModule[]
  projectPanels?: ProjectPanelModule[]
  services?: ServiceDescriptor[]
  activate?: (ctx: PluginContext) => void | Promise<void>
  deactivate?: () => void | Promise<void>
}

export interface PluginContext {
  edition: Edition
  features: FeatureFlags
}

export interface LoadedPlugin {
  manifest: PluginManifest
  unload: () => Promise<void>
}

const activePlugins = new Map<string, LoadedPlugin>()

/**
 * Register a plugin manifest against the runtime registry. Returns a
 * `LoadedPlugin` whose `unload()` removes every contribution in reverse
 * order and invokes `deactivate` if provided.
 */
export const registerPlugin = async (manifest: PluginManifest): Promise<LoadedPlugin> => {
  if (activePlugins.has(manifest.id)) {
    throw new Error(`Plugin "${manifest.id}" is already loaded`)
  }

  const store = useRegistryStore.getState()
  const disposers: Array<() => void> = []

  for (const route of manifest.routes ?? []) {
    disposers.push(store.registerRoute(route))
  }
  for (const section of manifest.settingsSections ?? []) {
    disposers.push(store.registerSettingsSection(section))
  }
  for (const panel of manifest.projectPanels ?? []) {
    disposers.push(store.registerProjectPanel(panel))
  }
  for (const service of manifest.services ?? []) {
    disposers.push(store.registerService(service))
  }

  const ctx: PluginContext = {
    edition: store.edition,
    features: store.features,
  }

  if (manifest.activate) {
    await manifest.activate(ctx)
  }

  const loaded: LoadedPlugin = {
    manifest,
    unload: async () => {
      if (manifest.deactivate) {
        await manifest.deactivate()
      }
      for (const dispose of disposers.reverse()) {
        dispose()
      }
      activePlugins.delete(manifest.id)
    },
  }

  activePlugins.set(manifest.id, loaded)
  return loaded
}

/**
 * Load a plugin from an ESM URL. The module must export a `PluginManifest`
 * either as the default export or as the named export `plugin`.
 */
export const loadPluginFromUrl = async (url: string): Promise<LoadedPlugin> => {
  const module: Record<string, unknown> = await import(/* @vite-ignore */ url)
  const candidate = (module.default ?? module.plugin) as PluginManifest | undefined
  if (!candidate || typeof candidate !== 'object' || typeof candidate.id !== 'string') {
    throw new Error(`Module at ${url} does not export a valid PluginManifest`)
  }
  return registerPlugin(candidate)
}

export const listLoadedPlugins = (): LoadedPlugin[] => Array.from(activePlugins.values())

export const getLoadedPlugin = (id: string): LoadedPlugin | undefined => activePlugins.get(id)
