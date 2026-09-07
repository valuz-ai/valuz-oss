import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import type { Plugin } from "vite";

const ASSET_DIRS = ["cmaps", "standard_fonts"] as const;
const URL_PREFIX = "/pdfjs/";

/**
 * Serves (dev) and copies (build) pdf.js's predefined CMaps and standard
 * fonts under `<base>/pdfjs/{cmaps,standard_fonts}/`. Without them pdf.js
 * cannot load CID-keyed CJK fonts that are not embedded in the file, so
 * Chinese text in many HK / A-share filings renders as blank glyphs
 * ("Error during font loading: Ensure that the `cMapUrl` API parameter is
 * provided"). `PdfDocumentRenderer` points `cMapUrl` / `standardFontDataUrl`
 * at these paths via `assetUrl()`.
 */
export function pdfjsAssetsPlugin(options: { configDir: string }): Plugin {
  const uiDir = path.resolve(options.configDir, "../../packages/ui");
  let distDir: string | null = null;
  try {
    distDir = path.dirname(
      createRequire(path.join(uiDir, "_")).resolve("pdfjs-dist/package.json"),
    );
  } catch {
    distDir = null;
  }
  const resolveAsset = (url: string): string | null => {
    if (!distDir) return null;
    const clean = url.split("?")[0] ?? "";
    const index = clean.indexOf(URL_PREFIX);
    if (index < 0) return null;
    const [dir, ...rest] = clean.slice(index + URL_PREFIX.length).split("/");
    if (!(ASSET_DIRS as readonly string[]).includes(dir ?? "")) return null;
    if (rest.length === 0 || rest.some((s) => !s || s === "..")) return null;
    const file = path.join(distDir, dir!, ...rest);
    return fs.existsSync(file) ? file : null;
  };
  return {
    name: "valuz-pdfjs-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const file = resolveAsset(req.url ?? "");
        if (!file) return next();
        res.setHeader("Content-Type", "application/octet-stream");
        res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
        fs.createReadStream(file).pipe(res);
      });
    },
    writeBundle(outputOptions) {
      if (!distDir) return;
      const outDir = outputOptions.dir ?? path.resolve(options.configDir, "dist");
      for (const dir of ASSET_DIRS) {
        fs.cpSync(path.join(distDir, dir), path.join(outDir, "pdfjs", dir), {
          recursive: true,
        });
      }
    },
  };
}
