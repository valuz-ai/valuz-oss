import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";

import * as sdk from "../index";
import { SHARED_MODULE_IDS, type SharedModuleId } from "../index";
import * as sdkUi from "../ui";

export const SHARED_MODULES_GLOBAL = "__VALUZ_PLUGIN_SHARED__";

export type SharedModuleTable = Record<SharedModuleId, unknown>;

/** The table plugins read their shared imports from. */
export function createSharedModuleTable(): SharedModuleTable {
  return {
    react: React,
    "react/jsx-runtime": jsxRuntime,
    "react-dom": ReactDOM,
    "react-dom/client": ReactDOMClient,
    "@valuz/plugin-sdk": sdk,
    "@valuz/plugin-sdk/ui": sdkUi,
  };
}

/**
 * Publish the shared module table as ``globalThis.__VALUZ_PLUGIN_SHARED__``.
 * The renderer entries call this before any plugin loads; the build preset
 * rewrites a plugin's imports of these six modules into reads from it, so the
 * plugin and the host run the same React and the same SDK instance.
 */
export function installSharedModules(
  target: Record<string, unknown> = globalThis as unknown as Record<string, unknown>,
): SharedModuleTable {
  const table = createSharedModuleTable();
  target[SHARED_MODULES_GLOBAL] = table;
  return table;
}

export { SHARED_MODULE_IDS };
