import "../../../plugin-sdk/src/build/test-env";
import path from "node:path";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { createServer } from "vite";
import { describe, expect, it } from "vitest";
import { routerInstancePlugin } from "./router-instance-plugin.ts";

const frontend = path.resolve(__dirname, "../../../..");

describe("browser router module resolution", () => {
  for (const app of ["webui", "desktop"]) {
    it(`uses one router graph for ${app} shell and aliased shared components`, async () => {
      const appDirectory = path.join(frontend, "apps", app);
      const cacheDir = mkdtempSync(path.join(tmpdir(), "valuz-router-resolution-"));
      const server = await createServer({
        configFile: false,
        root: appDirectory,
        cacheDir,
        optimizeDeps: { noDiscovery: true, include: [] },
        plugins: [routerInstancePlugin(appDirectory)],
        server: { middlewareMode: true, hmr: false, watch: null },
        appType: "custom",
      });
      try {
        const resolver = server.environments.client.pluginContainer;
        const importers = [
          path.join(appDirectory, "src/router.tsx"),
          path.join(frontend, "packages/app/src/components/CitationDocumentPreviewProvider.tsx"),
        ];
        for (const specifier of ["react-router-dom", "react-router", "react-router/dom"]) {
          const resolved = await Promise.all(importers.map((importer) =>
            resolver.resolveId(specifier, importer),
          ));
          expect(resolved[0]?.id).toBe(resolved[1]?.id);
          expect(resolved[0]?.id).toBeTruthy();
          expect(existsSync(resolved[0]!.id.split("?")[0])).toBe(true);
        }
      } finally {
        await server.close();
        rmSync(cacheDir, { recursive: true, force: true });
      }
    });
  }
});
