import { defineConfig } from "vitest/config";
import workspaceConfig from "../../vitest.config";

// Keep the same React/Router module graph and asset loaders as the workspace
// gate. The package command still discovers only this desktop app's tests.
export default defineConfig({
  ...workspaceConfig,
  root: __dirname,
  test: {
    ...workspaceConfig.test,
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["**/node_modules/**", "**/dist/**", "**/.turbo/**"],
  },
});
