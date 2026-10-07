/** Compatibility for App Plugin packages built before the naming migration. */
export {
  adaptAppPlugin as adaptThirdPartyPlugin,
  APP_PLUGIN_SETTINGS_GROUP as EXTENSIONS_SETTINGS_GROUP,
} from "./adapt";
export type { AppPluginItem as ThirdPartyPluginItem } from "./adapt";

/** Old bundles scope their CSS to this attribute. Keep it beside the new one. */
export function legacyAppPluginAttributes(pluginId: string) {
  return { "data-valuz-ext": pluginId };
}

/** Older SDK bundles may have stored already-qualified host translation keys. */
export function legacyAppPluginLocaleNamespace(pluginId: string): string {
  return `ext.${pluginId}`;
}
