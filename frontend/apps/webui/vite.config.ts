import { defineConfig } from "vite";
import { editionOverlayPlugin } from "@valuz/core/vite";
import { baseViteConfig } from "@valuz/shared/vite";

export default defineConfig(
  baseViteConfig({
    configDir: __dirname,
    editionOverlay: editionOverlayPlugin(),
  }),
);
