import type { Plugin } from "vite";

const VIRTUAL_ID = "virtual:edition-overlay";
const RESOLVED_ID = "\0" + VIRTUAL_ID;

/**
 * Vite plugin that provides `virtual:edition-overlay` — a build-time bridge
 * between the host app and an external overlay package.
 *
 * When `VALUZ_OVERLAY_PACKAGE` is set, the virtual module re-exports
 * `overlayProfile` from that package's `./renderer` export. Otherwise it
 * exports `null`, and the host app runs with the built-in personal profile.
 *
 * Usage:
 *   import { editionOverlayPlugin } from "@valuz/core/vite";
 *   plugins: [editionOverlayPlugin()]
 */
export function editionOverlayPlugin(): Plugin {
  return {
    name: "valuz-edition-overlay",
    enforce: "pre",

    resolveId(id) {
      if (id === VIRTUAL_ID) return RESOLVED_ID;
    },

    load(id) {
      if (id !== RESOLVED_ID) return;

      const overlayPackage = process.env.VALUZ_OVERLAY_PACKAGE;

      if (!overlayPackage) {
        return "export const overlayProfile = null;";
      }

      return `export { overlayProfile } from "${overlayPackage}/renderer";`;
    },
  };
}
