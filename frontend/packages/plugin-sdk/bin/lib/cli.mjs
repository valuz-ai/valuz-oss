// The ``valuz-plugin`` commands (bin/valuz-plugin.mjs is the executable).
import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import { buildPlugin } from "../../src/build/build-plugin.mjs";
import { ManifestError, SDK_ROOT } from "../../src/build/manifest.mjs";
import { PackError, packPlugin } from "../../src/build/pack.mjs";
import { validatePlugin } from "../../src/build/validate.mjs";
import { BackendError, devLink, reloadPlugin, resolveBackend } from "./backend.mjs";
import { CreateError, createPlugin } from "./create.mjs";
import { runPluginTests } from "./test-runner.mjs";

const out = (line = "") => process.stdout.write(`${line}\n`);
const err = (line = "") => process.stderr.write(`${line}\n`);

class UsageError extends Error {}

// ── argument parsing ──────────────────────────────────────────────────────────

/**
 * Parse ``argv`` for a command spec ``{ booleans: [], strings: [], maxArgs }``.
 * Supports ``--flag``, ``--flag value``, ``--flag=value``, ``-h`` and ``--``.
 */
export function parseArgs(argv, spec) {
  const flags = {};
  const positionals = [];
  const rest = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--") {
      rest.push(...argv.slice(i + 1));
      break;
    }
    if (arg === "-h" || arg === "--help") {
      flags.help = true;
      continue;
    }
    if (arg.startsWith("--")) {
      const eq = arg.indexOf("=");
      const name = arg.slice(2, eq === -1 ? undefined : eq);
      if (spec.booleans?.includes(name)) {
        if (eq !== -1) throw new UsageError(`--${name} does not take a value`);
        flags[name] = true;
      } else if (spec.strings?.includes(name)) {
        let value = eq === -1 ? argv[i + 1] : arg.slice(eq + 1);
        if (eq === -1) i += 1;
        if (value === undefined || (eq === -1 && value.startsWith("--"))) {
          throw new UsageError(`--${name} needs a value`);
        }
        flags[name] = value;
      } else {
        throw new UsageError(`unknown option --${name}`);
      }
      continue;
    }
    if (arg.startsWith("-") && arg !== "-") throw new UsageError(`unknown option ${arg}`);
    positionals.push(arg);
  }
  if (positionals.length > (spec.maxArgs ?? 1)) {
    throw new UsageError(`unexpected argument ${positionals[spec.maxArgs ?? 1]}`);
  }
  return { flags, positionals, rest };
}

// ── output helpers ────────────────────────────────────────────────────────────

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function printValidation(result) {
  for (const error of result.errors) out(`error: ${error}`);
  for (const warning of result.warnings) out(`warning: ${warning}`);
  const id = result.manifest?.id ?? "plugin";
  const version = result.manifest?.version ? ` ${result.manifest.version}` : "";
  if (result.ok) {
    out(`${id}${version}: valid${result.warnings.length ? ` (${result.warnings.length} warning${result.warnings.length === 1 ? "" : "s"})` : ""}`);
  } else {
    out(`${id}${version}: ${result.errors.length} error${result.errors.length === 1 ? "" : "s"}`);
  }
}

function printBuild(report) {
  for (const warning of report.warnings) err(`warning: ${warning}`);
  if (!report.ok) {
    for (const error of report.errors) err(`error: ${error}`);
    err(`build failed: ${report.pluginId}`);
    return;
  }
  out(`built ${report.pluginId} from ${report.source}`);
  const width = Math.max(...report.outputs.map((o) => o.file.length));
  for (const output of report.outputs) {
    out(`  ${output.file.padEnd(width)}  ${formatSize(output.bytes)}`);
  }
}

const time = () => new Date().toLocaleTimeString();

// ── commands ──────────────────────────────────────────────────────────────────

const COMMANDS = {
  create: {
    summary: "Scaffold a new plugin",
    usage: "valuz-plugin create <dir> --id <publisher.name> [--name \"Display name\"]",
    help: [
      "Creates <dir> with valuz-plugin.json, src/index.tsx (a title-bar button and",
      "a page), locales/, a test, tsconfig.json, package.json, AGENTS.md / CLAUDE.md,",
      "README.md and .gitignore. <dir> must not exist or be empty.",
      "",
      "Options:",
      "  --id <publisher.name>  plugin id: lowercase letters, digits, '-', one '.'",
      "  --name <text>          display name (default: from the id)",
    ],
    spec: { strings: ["id", "name"], maxArgs: 1 },
    async run({ flags, positionals }) {
      if (!positionals[0]) throw new UsageError("missing <dir>");
      const result = createPlugin(positionals[0], { id: flags.id, name: flags.name });
      out(`created ${result.id} in ${result.dir}`);
      for (const file of result.files) out(`  ${path.relative(result.dir, file)}`);
      out("");
      out("next:");
      out(`  cd ${path.relative(process.cwd(), result.dir) || "."}`);
      out("  valuz-plugin build && valuz-plugin test && valuz-plugin validate");
      out("  valuz-plugin dev          # link into the running Valuz");
      return 0;
    },
  },

  build: {
    summary: "Bundle src/ into the manifest's frontend entry",
    usage: "valuz-plugin build [dir] [--watch] [--minify] [--json]",
    help: [
      "Source: package.json valuzPlugin.source, else src/index.tsx (.ts, .jsx, .js).",
      "Output: frontend.entry of valuz-plugin.json (default frontend/index.js) and",
      "<entry>.css when the source imports CSS (added to frontend.styles if missing).",
      "react, react-dom and @valuz/plugin-sdk are read from the host at runtime;",
      "everything else is bundled. CSS is scoped to [data-valuz-ext=\"<id>\"].",
      "",
      "Options:",
      "  --watch   rebuild on every change",
      "  --minify  minify the output",
      "  --json    print the build report as JSON",
    ],
    spec: { booleans: ["watch", "minify", "json"], maxArgs: 1 },
    async run({ flags, positionals }) {
      const root = path.resolve(positionals[0] ?? ".");
      if (!flags.watch) {
        const report = await buildPlugin(root, { minify: flags.minify });
        if (flags.json) out(JSON.stringify(report, null, 2));
        else printBuild(report);
        return report.ok ? 0 : 1;
      }
      await buildPlugin(root, {
        watch: true,
        minify: flags.minify,
        onBuild(report) {
          if (flags.json) return out(JSON.stringify(report));
          out(`[${time()}] ${report.ok ? "" : "failed "}build`);
          printBuild(report);
        },
      });
      out("watching for changes (Ctrl+C to stop)");
      return waitForever();
    },
  },

  dev: {
    summary: "Build in watch mode and link the plugin into the running Valuz",
    usage: "valuz-plugin dev [dir] [--backend URL] [--token TOKEN] [--minify]",
    help: [
      "Builds with --watch. After the first successful build it links the",
      "directory into the local Valuz (POST /v1/extensions/third-party/dev-link)",
      "and reloads the plugin after every later rebuild.",
      "",
      "Options:",
      "  --backend URL  the Valuz backend (default $VALUZ_BACKEND_BASE_URL, else http://127.0.0.1:8000)",
      "  --token TOKEN  bearer token (default $VALUZ_BACKEND_TOKEN)",
      "  --minify       minify the output",
    ],
    spec: { strings: ["backend", "token"], booleans: ["minify"], maxArgs: 1 },
    async run({ flags, positionals }) {
      const root = path.resolve(positionals[0] ?? ".");
      const backend = resolveBackend(flags.backend);
      const token = flags.token ?? process.env.VALUZ_BACKEND_TOKEN ?? undefined;
      let linkedId = null;
      let linking = false;
      let pending = Promise.resolve();

      const afterBuild = async (report) => {
        if (!report.ok) return;
        if (linkedId) {
          try {
            const result = await reloadPlugin(backend, linkedId, { token });
            out(`[${time()}] reloaded ${linkedId} (revision ${result?.plugin?.revision ?? "?"})`);
          } catch (error) {
            err(`[${time()}] reload failed: ${error.message}`);
          }
          return;
        }
        if (linking) return;
        linking = true;
        try {
          const result = await devLink(backend, root, { token });
          const plugin = result?.plugin ?? {};
          linkedId = plugin.id ?? report.pluginId;
          out(`linked ${linkedId} ${plugin.version ?? ""} (dev) into ${backend}`.replace(/\s+\(/, " ("));
          if (plugin.status) out(`  status   ${plugin.status}${plugin.status_reason ? ` — ${plugin.status_reason}` : ""}`);
          if (plugin.revision !== undefined) out(`  revision ${plugin.revision}`);
          if (plugin.entry_url) out(`  entry    ${plugin.entry_url}`);
        } catch (error) {
          if (error instanceof BackendError && error.unreachable) {
            err(`${error.message}.`);
            err("Is Valuz running? Start it, or pass --backend / set VALUZ_BACKEND_BASE_URL;");
            err("then restart `valuz-plugin dev` (or run `valuz plugin dev <dir>`). Still watching.");
          } else {
            err(`dev-link failed: ${error.message}`);
            err("Still watching; fix the problem and restart `valuz-plugin dev`.");
          }
        } finally {
          linking = false;
        }
      };

      await buildPlugin(root, {
        watch: true,
        minify: flags.minify,
        onBuild(report) {
          out(`[${time()}] ${report.ok ? "" : "failed "}build`);
          printBuild(report);
          pending = pending.then(() => afterBuild(report));
        },
      });
      await pending;
      out("watching for changes (Ctrl+C to stop)");
      return waitForever();
    },
  },

  test: {
    summary: "Run the plugin's tests with node --test",
    usage: "valuz-plugin test [dir] [-- <node --test options>]",
    help: [
      "Bundles every test/**/*.test.{ts,tsx,js,jsx,mjs} with esbuild for Node",
      "(@valuz/plugin-sdk and its /testing host from the SDK, React from the SDK)",
      "and runs them with node --test. No node_modules needed in the plugin.",
    ],
    spec: { maxArgs: 1 },
    async run({ positionals, rest }) {
      return runPluginTests(path.resolve(positionals[0] ?? "."), { nodeArgs: rest });
    },
  },

  validate: {
    summary: "Check the manifest, the package files and the built bundle",
    usage: "valuz-plugin validate [dir] [--json]",
    help: [
      "Applies valuz-plugin.schema.json and its x-valuz-rules, checks that the",
      "files the manifest names exist and would be packed, the locales parse, and",
      "that frontend.entry default-exports the plugin and imports nothing at runtime.",
      "Exit code 1 when there are errors.",
      "",
      "Options:",
      "  --json  print {ok, errors, warnings, manifest}",
    ],
    spec: { booleans: ["json"], maxArgs: 1 },
    async run({ flags, positionals }) {
      const result = validatePlugin(path.resolve(positionals[0] ?? "."));
      if (flags.json) out(JSON.stringify(result, null, 2));
      else printValidation(result);
      return result.ok ? 0 : 1;
    },
  },

  pack: {
    summary: "Validate and zip the plugin into <id>-<version>.zip",
    usage: "valuz-plugin pack [dir] [--out DIR] [--json]",
    help: [
      "Validates first, then writes a deterministic zip (sorted entries, fixed",
      "timestamps) of valuz-plugin.json, frontend/, the locales directory,",
      "automations/, the icon, README.md and LICENSE* to <out>/<id>-<version>.zip",
      "(default <dir>/dist) and prints its sha256.",
      "",
      "Options:",
      "  --out DIR  output directory (default <dir>/dist)",
      "  --json     print {path, sha256, size, id, version, files, warnings}",
    ],
    spec: { strings: ["out"], booleans: ["json"], maxArgs: 1 },
    async run({ flags, positionals }) {
      const root = path.resolve(positionals[0] ?? ".");
      try {
        const result = packPlugin(root, { outDir: flags.out ? path.resolve(flags.out) : undefined });
        if (flags.json) {
          out(JSON.stringify(result, null, 2));
        } else {
          for (const warning of result.warnings) out(`warning: ${warning}`);
          out(`packed ${result.id} ${result.version} (${result.files.length} files, ${formatSize(result.size)})`);
          out(`  ${result.path}`);
          out(`  sha256 ${result.sha256}`);
        }
        return 0;
      } catch (error) {
        if (!(error instanceof PackError)) throw error;
        if (flags.json) {
          out(JSON.stringify({ ok: false, error: error.message, validation: error.validation }, null, 2));
        } else if (error.validation && !error.validation.ok) {
          printValidation(error.validation);
          err("pack refused: the plugin is invalid");
        } else {
          err(`pack failed: ${error.message}`);
        }
        return 1;
      }
    },
  },
};

function waitForever() {
  return new Promise((resolve) => {
    const stop = () => resolve(0);
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
  });
}

function printMainHelp() {
  out("valuz-plugin — build, test and package third-party Valuz plugins");
  out("");
  out("Usage: valuz-plugin <command> [options]");
  out("");
  out("Commands:");
  for (const [name, command] of Object.entries(COMMANDS)) out(`  ${name.padEnd(9)} ${command.summary}`);
  out("");
  out("Run `valuz-plugin <command> --help` for a command's options.");
  out("Install into Valuz with `valuz plugin dev <dir>` or `valuz plugin install <zip>`.");
}

function printCommandHelp(name) {
  const command = COMMANDS[name];
  out(`${command.summary}.`);
  out("");
  out(`Usage: ${command.usage}`);
  out("");
  for (const line of command.help) out(line);
}

export async function main(argv) {
  const [name, ...args] = argv;
  if (!name || name === "help" || name === "--help" || name === "-h") {
    if (name === "help" && args[0] && COMMANDS[args[0]]) printCommandHelp(args[0]);
    else printMainHelp();
    return 0;
  }
  if (name === "--version" || name === "-v") {
    const pkg = JSON.parse(readFileSync(path.join(SDK_ROOT, "package.json"), "utf8"));
    out(pkg.version);
    return 0;
  }
  const command = COMMANDS[name];
  if (!command) {
    err(`valuz-plugin: unknown command "${name}"`);
    err("Run `valuz-plugin --help` for the list of commands.");
    return 2;
  }
  let parsed;
  try {
    parsed = parseArgs(args, command.spec);
  } catch (error) {
    if (!(error instanceof UsageError)) throw error;
    err(`valuz-plugin ${name}: ${error.message}`);
    err(`Usage: ${command.usage}`);
    return 2;
  }
  if (parsed.flags.help) {
    printCommandHelp(name);
    return 0;
  }
  try {
    return await command.run(parsed);
  } catch (error) {
    if (error instanceof UsageError) {
      err(`valuz-plugin ${name}: ${error.message}`);
      err(`Usage: ${command.usage}`);
      return 2;
    }
    if (error instanceof ManifestError || error instanceof CreateError) {
      err(`valuz-plugin ${name}: ${error.message}`);
      return 1;
    }
    err(`valuz-plugin ${name}: ${error?.stack ?? error}`);
    return 1;
  }
}
