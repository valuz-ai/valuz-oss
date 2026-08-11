import { defineConfig } from "vite";

export default defineConfig({
  root: "demo",
  server: {
    host: "127.0.0.1",
  },
  build: {
    outDir: "../dist-demo",
    emptyOutDir: true,
  },
});
