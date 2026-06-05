import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const resolvePath = (segment: string) => path.resolve(__dirname, segment);

export default defineConfig({
  plugins: [
    react(),
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
    alias: {
      "@valuz/shared": resolvePath("./packages/shared/src"),
      "@valuz/core": resolvePath("./packages/core/src"),
      "@valuz/ui": resolvePath("./packages/ui/src"),
      "@valuz/app": resolvePath("./packages/app/src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [resolvePath("./vitest.setup.ts")],
    include: [
      `${resolvePath("./apps")}/**/src/**/*.test.{ts,tsx}`,
      `${resolvePath("./packages")}/**/src/**/*.test.{ts,tsx}`,
    ],
    exclude: ["**/node_modules/**", "**/dist/**", "**/.turbo/**"],
  },
});
