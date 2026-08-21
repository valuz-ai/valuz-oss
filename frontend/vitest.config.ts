import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const resolvePath = (segment: string) => path.resolve(__dirname, segment);

export default defineConfig({
  plugins: [
    react(),
    {
      // PDF.js exposes its worker through Vite's `?url` loader. The commercial
      // workspace may resolve that package from a parent pnpm store outside
      // this config's root, which Vite correctly blocks during tests. Tests do
      // not execute the worker, so keep collection portable with a virtual URL.
      name: "pdf-worker-url-stub",
      enforce: "pre",
      resolveId(id) {
        if (id === "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url")
          return "\0pdf-worker-url-stub";
      },
      load(id) {
        if (id === "\0pdf-worker-url-stub")
          return 'export default "/pdf.worker.min.mjs";';
      },
    },
    {
      name: "virtual-edition-overlay-stub",
      resolveId(id) {
        if (id === "virtual:edition-overlay")
          return "\0virtual:edition-overlay";
      },
      load(id) {
        if (id === "\0virtual:edition-overlay")
          return "export const overlayProfile = null;";
      },
    },
  ],
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: {
      // This repo is often consumed from a parent commercial workspace. Pin
      // React resolution to this workspace's lockfile so Vitest cannot mix a
      // parent React dispatcher with the local ReactDOM renderer.
      react: resolvePath("./packages/a2ui/node_modules/react"),
      "react-dom": resolvePath("./packages/a2ui/node_modules/react-dom"),
      "@valuz/shared": resolvePath("./packages/shared/src"),
      "@valuz/core": resolvePath("./packages/core/src"),
      "@valuz/ui": resolvePath("./packages/ui/src"),
      "@valuz/a2ui": resolvePath("./packages/a2ui/src"),
      "@valuz/app": resolvePath("./packages/app/src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [resolvePath("./vitest.setup.ts")],
    server: {
      deps: {
        inline: [/@a2ui\//],
      },
    },
    include: [
      `${resolvePath("./apps")}/**/src/**/*.test.{ts,tsx}`,
      `${resolvePath("./packages")}/**/src/**/*.test.{ts,tsx}`,
      `${resolvePath("./packages/a2ui/demo")}/**/*.test.{ts,tsx}`,
    ],
    // ``**/node_modules/**`` alone does NOT stop the duplication: pnpm
    // symlinks every ``@valuz/*`` package into the other packages' (and
    // apps') ``node_modules``, and the include globs' ``**`` follows those
    // symlinks. Worse, the links nest
    // (``apps/desktop/node_modules/@valuz/app/node_modules/@valuz/core/…``),
    // so the same ``packages/<pkg>/src/**`` test files get collected
    // combinatorially — ~36× — ballooning one run to 10k+ tests / 1k+ files
    // and making it appear to hang. The ``/@valuz/`` path segment only ever
    // appears on those symlink-traversed copies (real sources live under
    // ``packages/<pkg>/src``), so excluding it collapses the run back to the
    // real ~57 files without dropping any genuine test.
    exclude: [
      "**/node_modules/**",
      "**/@valuz/**",
      "**/dist/**",
      "**/.turbo/**",
    ],
  },
});
