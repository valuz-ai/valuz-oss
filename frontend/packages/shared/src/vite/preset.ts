/**
 * Shared Vite config factory for @valuz workspace apps.
 *
 * Provides the common plugin stack (react, tailwindcss, i18n HMR),
 * edition define, and package alias resolution. Each app extends the
 * result with app-specific options and passes in the edition overlay
 * plugin (from @valuz/core/vite) to avoid circular deps.
 *
 * Usage:
 *   import { editionOverlayPlugin } from "@valuz/core/vite";
 *   import { baseViteConfig } from "@valuz/shared/vite/preset";
 *   export default defineConfig({
 *     ...baseViteConfig({
 *       configDir: __dirname,
 *       editionOverlay: editionOverlayPlugin(),
 *     }),
 *     server: { port: 1420 },
 *   });
 */

import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { PluginOption, UserConfig } from "vite";
import { i18nHmrPlugin } from "./i18n-hmr-plugin.ts";

const edition = process.env.EDITION ?? "personal";

export interface BaseViteConfigOptions {
  /** Directory of the vite.config file — used to resolve aliases. */
  configDir: string;
  /** Root of the frontend monorepo (default: configDir/../../..). */
  monorepoRoot?: string;
  /** Edition overlay plugin from @valuz/core/vite. */
  editionOverlay: PluginOption;
}

export function baseViteConfig(options: BaseViteConfigOptions): UserConfig {
  const root =
    options.monorepoRoot ?? path.resolve(options.configDir, "../../..");

  return {
    plugins: [
      react(),
      tailwindcss(),
      options.editionOverlay,
      i18nHmrPlugin({
        root,
        command: ["uv", "run", "python", "../i18n/scripts/gen_types.py"],
      }),
    ],
    define: {
      __EDITION__: JSON.stringify(edition),
    },
    resolve: {
      alias: {
        "@valuz/shared": path.resolve(
          options.configDir,
          "../../packages/shared/src",
        ),
        "@valuz/core": path.resolve(
          options.configDir,
          "../../packages/core/src",
        ),
        "@valuz/ui": path.resolve(options.configDir, "../../packages/ui/src"),
        "@valuz/app": path.resolve(options.configDir, "../../packages/app/src"),
      },
    },
  };
}
