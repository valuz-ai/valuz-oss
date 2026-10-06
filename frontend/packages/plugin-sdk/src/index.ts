/**
 * @valuz/plugin-sdk — the runtime a third-party Valuz plugin imports (doc 12).
 *
 * One instance lives in the host app and is handed to plugins through the
 * shared module table (``globalThis.__VALUZ_PLUGIN_SHARED__``); the npm copy is
 * for types, the build preset and the test host.
 */
export { PLUGIN_API_VERSION, FIXED_PUBLIC_SLOTS, KEYED_PUBLIC_SLOTS } from "./types";
export type * from "./types";

export {
  definePlugin,
  pageRoute,
  settingsPage,
  sidebarItem,
  slotComponent,
} from "./define";
export {
  usePluginConfig,
  useHostContext,
  useTranslation,
  useValuz,
} from "./scope";
export { host } from "./host-api";
export { ValuzApiError } from "./valuz-client";

/** The modules the host shares with plugins (``__VALUZ_PLUGIN_SHARED__`` keys). */
export const SHARED_MODULE_IDS = [
  "react",
  "react/jsx-runtime",
  "react-dom",
  "react-dom/client",
  "@valuz/plugin-sdk",
  "@valuz/plugin-sdk/ui",
] as const;

export type SharedModuleId = (typeof SHARED_MODULE_IDS)[number];
