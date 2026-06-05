import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  timeout: 60_000,
  retries: 0,
  // Electron only allows one instance per userData dir at a time; running tests
  // in parallel hits "Failed to create SingletonLock: File exists" and tears
  // down every worker. Force serial execution.
  workers: 1,
  // Seeds the backend with at least one configured model provider so the
  // shell's first-run gate doesn't redirect every test to /welcome.
  globalSetup: "./fixtures/seed-backend.ts",
  use: {
    trace: "on-first-retry",
  },
});
