// ``buildPlugin(dir)``: build a plugin directory's frontend with the esbuild
// preset. Used by ``valuz-plugin build`` / ``dev`` and usable from scripts.
//
//   source  package.json ``valuzPlugin.source``, else src/index.tsx, .ts,
//           .jsx, .js
//   output  manifest ``frontend.entry`` (default frontend/index.js), plus
//           ``<entry>.css`` when the source imports CSS
//
// When the build produces CSS that ``frontend.styles`` does not list, the
// manifest is updated (and the report says so): the host only injects the
// styles the manifest names, so a missing entry would silently drop them.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { esbuildPreset } from "./esbuild-preset.mjs";
import { loadEsbuild } from "./esbuild.mjs";
import { checkBundleImports } from "./imports.mjs";
import {
  ManifestError,
  cleanRel,
  frontendEntry,
  hasDotDot,
  readManifest,
  writeManifest,
} from "./manifest.mjs";

const SOURCE_CANDIDATES = ["src/index.tsx", "src/index.ts", "src/index.jsx", "src/index.js"];

/** The plugin's source entry (absolute), or throws with what to do. */
export function resolveSourceEntry(dir) {
  const pkgFile = path.join(dir, "package.json");
  if (existsSync(pkgFile)) {
    let pkg = null;
    try {
      pkg = JSON.parse(readFileSync(pkgFile, "utf8"));
    } catch {
      pkg = null;
    }
    const source = pkg?.valuzPlugin?.source;
    if (typeof source === "string" && source) {
      const abs = path.resolve(dir, source);
      if (!existsSync(abs)) {
        throw new ManifestError(`package.json valuzPlugin.source: ${source} does not exist`);
      }
      return abs;
    }
  }
  for (const candidate of SOURCE_CANDIDATES) {
    const abs = path.join(dir, candidate);
    if (existsSync(abs)) return abs;
  }
  throw new ManifestError(
    `no source entry in ${dir}: create src/index.tsx (or set "valuzPlugin": { "source": "…" } in package.json)`,
  );
}

function formatMessage(message, dir) {
  const loc = message.location;
  const where = loc ? `${path.relative(dir, path.resolve(dir, loc.file)) || loc.file}:${loc.line}:${loc.column}: ` : "";
  const plugin = message.pluginName && message.pluginName !== "valuz-plugin" ? `[${message.pluginName}] ` : "";
  return `${where}${plugin}${message.text}`;
}

/** Everything a build needs from the plugin directory. */
export function planBuild(dir, options = {}) {
  const root = path.resolve(dir);
  const manifest = readManifest(root);
  const pluginId = manifest.id;
  if (typeof pluginId !== "string" || !pluginId) {
    throw new ManifestError("valuz-plugin.json: id is required");
  }
  const entryRel = frontendEntry(manifest);
  if (hasDotDot(entryRel) || path.isAbsolute(entryRel)) {
    throw new ManifestError(`frontend.entry: "${entryRel}" must be a path inside the plugin directory`);
  }
  if (!/\.m?js$/.test(entryRel)) {
    throw new ManifestError(`frontend.entry: "${entryRel}" must be a .js or .mjs file`);
  }
  const outfile = path.join(root, entryRel);
  const cssRel = entryRel.replace(/\.m?js$/, ".css");
  const source = resolveSourceEntry(root);
  const minify = Boolean(options.minify);
  const sourcemap = options.sourcemap ?? (options.watch ? "inline" : false);
  const buildOptions = esbuildPreset({ pluginId, entry: source, outfile, minify, sourcemap });
  return { root, manifest, pluginId, entryRel, cssRel, outfile, source, buildOptions };
}

/**
 * Turn an esbuild result (``write: false``) into files on disk + a report.
 * Nothing is written when the build or the import check fails.
 */
function finishBuild(plan, result) {
  const { root, pluginId, entryRel, cssRel, outfile } = plan;
  const report = {
    ok: false,
    pluginId,
    source: path.relative(root, plan.source),
    outputs: [],
    errors: result.errors.map((m) => formatMessage(m, root)),
    warnings: result.warnings.map((m) => formatMessage(m, root)),
    manifestUpdated: false,
  };
  if (report.errors.length) return report;

  const files = result.outputFiles ?? [];
  const js = files.find((f) => path.resolve(f.path) === path.resolve(outfile));
  if (!js) {
    report.errors.push(`the build produced no ${entryRel}`);
    return report;
  }
  const disallowed = checkBundleImports(js.text);
  if (disallowed.length) {
    report.errors.push(
      `${entryRel} would import ${disallowed.map((s) => `"${s}"`).join(", ")} at runtime; a plugin bundle must be self-contained (only react, react-dom and @valuz/plugin-sdk come from the host)`,
    );
    return report;
  }

  for (const file of files) {
    mkdirSync(path.dirname(file.path), { recursive: true });
    writeFileSync(file.path, file.contents);
    report.outputs.push({
      path: file.path,
      file: cleanRel(path.relative(root, file.path)),
      bytes: file.contents.byteLength,
    });
  }

  // frontend.styles must list the CSS the build produced.
  const producedCss = report.outputs.some((o) => o.file === cssRel);
  const manifest = readManifest(root);
  const styles = Array.isArray(manifest.frontend?.styles) ? manifest.frontend.styles : [];
  const listed = styles.some((s) => typeof s === "string" && cleanRel(s) === cssRel);
  if (producedCss && !listed && manifest.frontend && typeof manifest.frontend === "object") {
    manifest.frontend.styles = [...styles, cssRel];
    writeManifest(root, manifest);
    report.manifestUpdated = true;
    report.warnings.push(`added ${cssRel} to frontend.styles in valuz-plugin.json`);
  } else if (!producedCss && listed && existsSync(path.join(root, cssRel))) {
    report.warnings.push(
      `${cssRel} is listed in frontend.styles but this build did not produce it (the source imports no CSS); delete it and remove it from frontend.styles if it is left over from an earlier build`,
    );
  }
  plan.manifest = manifest;
  report.ok = true;
  return report;
}

/**
 * Build the plugin in ``dir``.
 *
 * - ``{ watch: false }`` (default): one build; resolves to the report
 *   ``{ ok, pluginId, source, outputs: [{ path, file, bytes }], errors,
 *   warnings, manifestUpdated }``.
 * - ``{ watch: true }``: builds, then rebuilds on every source change;
 *   ``onBuild(report)`` runs after each build (the first included). Resolves
 *   after the first build to ``{ report, dispose() }``.
 */
export async function buildPlugin(dir, options = {}) {
  const plan = planBuild(dir, options);
  const esbuild = loadEsbuild();
  const buildOptions = { ...plan.buildOptions, write: false };

  if (!options.watch) {
    let result;
    try {
      result = await esbuild.build(buildOptions);
    } catch (error) {
      // esbuild throws on errors; the failure carries the messages.
      result = { errors: error.errors ?? [{ text: String(error.message ?? error) }], warnings: error.warnings ?? [] };
    }
    const report = finishBuild(plan, result);
    options.onBuild?.(report);
    return report;
  }

  let resolveFirst;
  const first = new Promise((resolve) => {
    resolveFirst = resolve;
  });
  const reporter = {
    name: "valuz-plugin-report",
    setup(build) {
      build.onEnd((result) => {
        let report;
        try {
          report = finishBuild(plan, result);
        } catch (error) {
          report = {
            ok: false,
            pluginId: plan.pluginId,
            source: path.relative(plan.root, plan.source),
            outputs: [],
            errors: [String(error?.message ?? error)],
            warnings: [],
            manifestUpdated: false,
          };
        }
        try {
          options.onBuild?.(report);
        } finally {
          resolveFirst?.(report);
          resolveFirst = null;
        }
      });
    },
  };
  const ctx = await esbuild.context({
    ...buildOptions,
    plugins: [...buildOptions.plugins, reporter],
  });
  await ctx.watch();
  const report = await first;
  return {
    report,
    async dispose() {
      await ctx.dispose();
    },
  };
}
