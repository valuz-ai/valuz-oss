/**
 * Vite plugin: auto-regenerate i18n type definitions when locale JSON
 * files change during development.
 *
 * Watches `i18n/locales/*.json` and runs `gen_types.py` on change,
 * producing both:
 *   - frontend/packages/shared/src/types/i18n.ts (TS I18nKey union)
 *   - backend/valuz_agent/generated/i18n_keys.py (Python Literal)
 *
 * Usage in vite config:
 *   import { i18nHmrPlugin } from "@valuz/shared/vite";
 *   plugins: [i18nHmrPlugin({ root: path.resolve(__dirname, "../../../..") })]
 */
import { execFile } from "node:child_process";
import path from "node:path";
import type { Plugin } from "vite";

export interface I18nHmrPluginOptions {
  /** Absolute path to the monorepo root (where `i18n/locales/` lives). */
  root: string;
  /**
   * Command to run gen_types. Defaults to:
   * ["uv", "run", "python", "../i18n/scripts/gen_types.py"]
   * executed with cwd = `${root}/backend`
   */
  command?: string[];
}

export function i18nHmrPlugin(options: I18nHmrPluginOptions): Plugin {
  const { root } = options;
  const command = options.command ?? [
    "uv",
    "run",
    "python",
    "../i18n/scripts/gen_types.py",
  ];

  const localesDir = path.join(root, "i18n", "locales");
  let running = false;

  function runGen() {
    if (running) return;
    running = true;
    const [cmd, ...args] = command;
    execFile(cmd, args, { cwd: path.join(root, "backend") }, (err, stdout) => {
      running = false;
      if (err) {
        console.error("[i18n-hmr] gen_types failed:", err.message);
      } else if (stdout) {
        console.log("[i18n-hmr]", stdout.trim());
      }
    });
  }

  return {
    name: "valuz-i18n-hmr",
    apply: "serve",
    configureServer(server) {
      server.watcher.add(path.join(localesDir, "*.json"));
      server.watcher.on("change", (file) => {
        if (file.startsWith(localesDir) && file.endsWith(".json")) {
          runGen();
        }
      });
    },
  };
}
