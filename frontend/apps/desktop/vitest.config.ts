import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const workspaceRoot = path.resolve(__dirname, "../..");

export default defineConfig({
  root: __dirname,
  plugins: [
    react(),
    {
      name: "virtual-edition-overlay-stub",
      resolveId(id) {
        if (id === "virtual:edition-overlay") return "\0virtual:edition-overlay";
      },
      load(id) {
        if (id === "\0virtual:edition-overlay") {
          return "export const overlayProfile = null;";
        }
      },
    },
  ],
  resolve: {
    alias: {
      "@valuz/shared": path.resolve(workspaceRoot, "packages/shared/src"),
      "@valuz/core": path.resolve(workspaceRoot, "packages/core/src"),
      "@valuz/ui": path.resolve(workspaceRoot, "packages/ui/src"),
      "@valuz/app": path.resolve(workspaceRoot, "packages/app/src"),
      "@valuz/desktop-network": path.resolve(
        workspaceRoot,
        "packages/desktop-network/src",
      ),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [path.resolve(workspaceRoot, "vitest.setup.ts")],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["**/node_modules/**", "**/dist/**", "**/.turbo/**"],
  },
});
