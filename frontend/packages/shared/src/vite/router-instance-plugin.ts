import { createRequire } from "node:module";
import path from "node:path";
import type { Plugin } from "vite";

/** Route contexts must come from one physical dependency graph, even when
 * source aliases cross between an OSS checkout and its parent workspace. */
export function routerInstancePlugin(appDirectory: string): Plugin {
  const require = createRequire(path.join(appDirectory, "package.json"));
  const routerDomEntry = require.resolve("react-router-dom");

  return {
    name: "valuz-router-instance",
    enforce: "pre",
    async resolveId(source, _importer, options) {
      if (!/^react-router(?:-dom)?(?:\/|$)/.test(source)) return null;
      // Let Vite resolve the package's conditional exports and subpaths. A
      // package-directory alias would turn react-router/dom into a nonexistent
      // file path instead of honoring the package's ./dom export.
      return this.resolve(source, routerDomEntry, { ...options, skipSelf: true });
    },
  };
}
