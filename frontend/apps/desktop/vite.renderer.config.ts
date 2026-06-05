import path from "node:path";
import { defineConfig } from "vite";
import { editionOverlayPlugin } from "@valuz/core/vite";
import { baseViteConfig } from "@valuz/shared/vite";

const base = baseViteConfig({
  configDir: __dirname,
  editionOverlay: editionOverlayPlugin(),
});

export default defineConfig({
  ...base,
  envDir: path.resolve(__dirname, "../.."),
  define: {
    ...base.define,
    // In production builds, the desktop app bundles its own backend on port
    // 19100 (PERSONAL_PORTS.AGENT_SERVER). Override the default
    // http://localhost:8000 used by @valuz/core API modules.
    // In dev mode, keep the default so `valuz start` (which boots backend on :8000) works.
    ...(process.env.NODE_ENV === "production" ||
    !process.env.VITE_DEV_SERVER_URL
      ? {
          "import.meta.env.VITE_API_BASE_URL": JSON.stringify(
            "http://localhost:19100",
          ),
        }
      : {}),
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "127.0.0.1",
    open: false,
  },
  base: "./",
  build: {
    outDir: "dist",
  },
});
