import "./test-env";

import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  symlinkSync,
} from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";

import {
  RUNTIME_ENTRIES,
  SDK_ROOT,
  isDistLayout,
  sdkLayout,
} from "../../bin/lib/layout.mjs";
import { testResolvePlugin } from "../../bin/lib/test-runner.mjs";
import { loadEsbuild } from "./esbuild.mjs";
import { cleanupTempDirs, tempDir, writeFiles } from "./test-fixtures";

afterAll(cleanupTempDirs);

const PACKAGE_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
const DIST_SCRIPT = path.resolve(
  PACKAGE_ROOT,
  "..",
  "..",
  "..",
  "scripts",
  "build-plugin-sdk-dist.mjs",
);

/** A distribution-shaped fixture: ``runtime/<bundle>`` for each name given. */
function distFixture(bundles: string[]): string {
  const root = tempDir("vzp-dist-");
  writeFiles(root, {
    "package.json": { name: "@valuz/plugin-sdk", version: "1.0.0" },
    "valuz-plugin.schema.json": {},
    ...Object.fromEntries(
      bundles.map((bundle) => [
        `runtime/${bundle}`,
        `export const which = ${JSON.stringify(bundle)};\n`,
      ]),
    ),
  });
  return root;
}

/** Bundle ``import ... from "<specifier>"`` with the test resolver of ``layout``. */
async function resolveWith(
  layout: ReturnType<typeof sdkLayout>,
  specifier: string,
) {
  const dir = tempDir("vzp-resolve-");
  writeFiles(dir, {
    "entry.mjs": `import * as m from ${JSON.stringify(specifier)};\nconsole.log(m);\n`,
  });
  try {
    const result = await loadEsbuild().build({
      entryPoints: [path.join(dir, "entry.mjs")],
      bundle: true,
      write: false,
      format: "esm",
      platform: "node",
      logLevel: "silent",
      plugins: [testResolvePlugin(layout)],
    });
    return { text: result.outputFiles[0].text, errors: [] as string[] };
  } catch (error) {
    const errors = (error as { errors?: Array<{ text: string }> }).errors ?? [];
    return { text: "", errors: errors.map((e) => e.text) };
  }
}

describe("SDK layout", () => {
  it("is the monorepo source layout for the package itself", () => {
    expect(SDK_ROOT).toBe(PACKAGE_ROOT);
    expect(isDistLayout()).toBe(false);
    const layout = sdkLayout();
    expect(layout.kind).toBe("source");
    expect(layout.modules.get("@valuz/plugin-sdk")).toBe(
      path.join(PACKAGE_ROOT, "src", "index.ts"),
    );
    expect(layout.modules.get("@valuz/plugin-sdk/testing")).toBe(
      path.join(PACKAGE_ROOT, "src", "testing", "index.ts"),
    );
    expect(layout.modules.get("@valuz/plugin-sdk/ui")).toBe(
      path.join(PACKAGE_ROOT, "src", "ui", "index.ts"),
    );
    // Every pre-bundled runtime entry is built from a source that exists.
    for (const entry of RUNTIME_ENTRIES) {
      expect(
        existsSync(path.join(PACKAGE_ROOT, entry.source)),
        entry.source,
      ).toBe(true);
      expect(layout.modules.get(entry.id)).toBe(
        path.join(PACKAGE_ROOT, entry.source),
      );
    }
  });

  it("prefers the pre-bundled runtime of a distribution", () => {
    const root = distFixture(["index.mjs", "testing.mjs", "ui.mjs"]);
    const layout = sdkLayout(root);
    expect(layout.kind).toBe("dist");
    expect(Object.fromEntries(layout.modules)).toEqual({
      "@valuz/plugin-sdk": path.join(root, "runtime", "index.mjs"),
      "@valuz/plugin-sdk/testing": path.join(root, "runtime", "testing.mjs"),
      "@valuz/plugin-sdk/ui": path.join(root, "runtime", "ui.mjs"),
      "@valuz/plugin-sdk/valuz-plugin.schema.json": path.join(
        root,
        "valuz-plugin.schema.json",
      ),
    });
  });

  it("falls back to package.json exports without runtime/index.mjs", () => {
    const root = distFixture(["ui.mjs"]);
    writeFiles(root, {
      "package.json": {
        name: "@valuz/plugin-sdk",
        exports: {
          ".": "./src/index.ts",
          "./testing": { import: "./src/t.ts" },
        },
      },
    });
    const layout = sdkLayout(root);
    expect(layout.kind).toBe("source");
    expect(Object.fromEntries(layout.modules)).toEqual({
      "@valuz/plugin-sdk": path.join(root, "src", "index.ts"),
      "@valuz/plugin-sdk/testing": path.join(root, "src", "t.ts"),
    });
  });

  it("valuz-plugin test resolves the SDK from the layout", async () => {
    const dist = sdkLayout(distFixture(["index.mjs", "testing.mjs"]));
    expect(
      (await resolveWith(dist, "@valuz/plugin-sdk/testing")).text,
    ).toContain('"testing.mjs"');
    expect((await resolveWith(dist, "@valuz/plugin-sdk/ui")).errors).toEqual([
      expect.stringMatching(
        /"@valuz\/plugin-sdk\/ui" is missing from the SDK at .*ui\.mjs/,
      ),
    ]);
    expect((await resolveWith(dist, "@valuz/plugin-sdk/host")).errors).toEqual([
      '"@valuz/plugin-sdk/host" is not an export of the packaged @valuz/plugin-sdk',
    ]);
  });
});

describe("the self-contained CLI distribution", () => {
  const sdkRequire = createRequire(path.join(PACKAGE_ROOT, "package.json"));

  it("builds and runs create → build → test → validate → pack outside the monorepo", () => {
    // Real path: the CLI reports real paths (macOS /var → /private/var).
    const work = realpathSync(tempDir("vzp-dist-e2e-"));
    const out = path.join(work, "plugin-sdk");
    const built = spawnSync(process.execPath, [DIST_SCRIPT, "--out", out], {
      encoding: "utf8",
      timeout: 120_000,
    });
    expect(built.status, built.stdout + built.stderr).toBe(0);
    expect(isDistLayout(out)).toBe(true);
    for (const entry of RUNTIME_ENTRIES) {
      expect(
        existsSync(path.join(out, "runtime", entry.bundle)),
        entry.bundle,
      ).toBe(true);
    }
    expect(existsSync(path.join(out, "src", "index.ts"))).toBe(false);
    const pkg = JSON.parse(
      readFileSync(path.join(out, "package.json"), "utf8"),
    );
    expect(pkg.exports["."]).toBe("./runtime/index.mjs");
    expect(Object.keys(pkg.dependencies).sort()).toEqual([
      "esbuild",
      "react",
      "react-dom",
    ]);

    // What `--install` (npm ci of the pins) provides, without the network:
    // the workspace's own copies, which the pins must equal.
    mkdirSync(path.join(out, "node_modules"));
    for (const name of ["esbuild", "react", "react-dom"]) {
      const dir = path.dirname(
        realpathSync(sdkRequire.resolve(`${name}/package.json`)),
      );
      expect(
        JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"))
          .version,
      ).toBe(pkg.dependencies[name]);
      symlinkSync(dir, path.join(out, "node_modules", name), "dir");
    }

    const bin = path.join(out, "bin", "valuz-plugin.mjs");
    const cli = (args: string[], cwd: string) => {
      const result = spawnSync(process.execPath, [bin, ...args], {
        cwd,
        encoding: "utf8",
        env: { ...process.env, NODE_ENV: "", FORCE_COLOR: "0", NO_COLOR: "1" },
        timeout: 120_000,
      });
      return { code: result.status, out: result.stdout + result.stderr };
    };

    expect(cli(["create", "hello", "--id", "acme.dist"], work).code).toBe(0);
    const dir = path.join(work, "hello");
    const manifest = JSON.parse(
      readFileSync(path.join(dir, "valuz-plugin.json"), "utf8"),
    );
    expect(manifest.$schema).toBe(path.join(out, "valuz-plugin.schema.json"));
    expect(readFileSync(path.join(dir, "AGENTS.md"), "utf8")).toContain(
      `node ${bin}`,
    );
    // No TypeScript sources ship, so the scaffold names none.
    const tsconfig = JSON.parse(
      readFileSync(path.join(dir, "tsconfig.json"), "utf8"),
    );
    expect(Object.keys(tsconfig.compilerOptions.paths)).not.toContain(
      "@valuz/plugin-sdk",
    );

    const build = cli(["build"], dir);
    expect(build.code, build.out).toBe(0);
    const test = cli(["test"], dir);
    expect(test.code, test.out).toBe(0);
    expect(test.out).toMatch(/pass 4/);
    const validate = cli(["validate"], dir);
    expect(validate.code, validate.out).toBe(0);
    const pack = cli(["pack", "--json"], dir);
    expect(pack.code, pack.out).toBe(0);
    expect(JSON.parse(pack.out).files).toContain("frontend/index.js");
  }, 180_000);
});
