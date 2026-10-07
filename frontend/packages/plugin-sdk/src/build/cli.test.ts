import "./test-env";

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";

import { cleanupTempDirs, tempDir } from "./test-fixtures";

afterAll(cleanupTempDirs);

const BIN = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "bin",
  "valuz-plugin.mjs",
);

function cli(args: string[], cwd: string) {
  const result = spawnSync(process.execPath, [BIN, ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, NODE_ENV: "", FORCE_COLOR: "0", NO_COLOR: "1" },
    timeout: 120_000,
  });
  return { code: result.status, stdout: result.stdout, stderr: result.stderr };
}

/** Run the dev command until ``done(output)`` is true; resolves with the output. */
function runDev(
  args: string[],
  cwd: string,
  done: (output: string) => boolean,
  onOutput?: (output: string) => void,
) {
  return new Promise<{ output: string; child: ChildProcess }>(
    (resolve, reject) => {
      const child = spawn(process.execPath, [BIN, "dev", ...args], {
        cwd,
        env: {
          ...process.env,
          VALUZ_BACKEND_BASE_URL: "",
          VALUZ_BACKEND_TOKEN: "",
        },
      });
      let output = "";
      const timer = setTimeout(() => {
        child.kill("SIGTERM");
        reject(
          new Error(`dev did not reach the expected state; output:\n${output}`),
        );
      }, 30_000);
      const onData = (chunk: Buffer) => {
        output += chunk.toString();
        onOutput?.(output);
        if (done(output)) {
          clearTimeout(timer);
          resolve({ output, child });
        }
      };
      child.stdout.on("data", onData);
      child.stderr.on("data", onData);
      child.on("exit", (code) => {
        clearTimeout(timer);
        if (!done(output))
          reject(new Error(`dev exited (${code}); output:\n${output}`));
      });
    },
  );
}

function stop(child: ChildProcess): Promise<void> {
  return new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    child.on("exit", () => resolve());
    child.kill("SIGTERM");
  });
}

describe("valuz-plugin CLI", () => {
  it("prints help and rejects bad usage", () => {
    const dir = tempDir();
    const main = cli(["--help"], dir);
    expect(main.code).toBe(0);
    expect(main.stdout).toContain("Valuz App Plugins");
    expect(main.stdout).toContain("valuz plugin app dev <dir>");
    expect(main.stdout).toContain("valuz plugin app install <zip>");
    for (const command of [
      "create",
      "build",
      "dev",
      "test",
      "validate",
      "pack",
    ]) {
      expect(main.stdout).toContain(command);
      const help = cli([command, "--help"], dir);
      expect(help.code).toBe(0);
      expect(help.stdout).toContain(`Usage: valuz-plugin ${command}`);
    }
    expect(cli(["nope"], dir).code).toBe(2);
    expect(cli(["build", "--nope"], dir).code).toBe(2);
    expect(cli(["create", "x"], dir).stderr).toMatch(/--id is required/);
    expect(cli(["create", "x", "--id", "valuz.thing"], dir).stderr).toMatch(
      /reserved/,
    );
    expect(cli(["build"], dir).stderr).toMatch(/valuz-plugin\.json not found/);
  });

  it("create → build → test → validate → pack (deterministic)", () => {
    const root = tempDir();
    const created = cli(
      ["create", "hello", "--id", "acme.hello", "--name", "Acme Hello"],
      root,
    );
    expect(created.code).toBe(0);
    const dir = realpathSync(path.join(root, "hello")); // the CLI reports real paths (macOS /private/var)
    for (const file of [
      "valuz-plugin.json",
      "src/index.tsx",
      "src/styles.css",
      "locales/zh-CN.json",
      "locales/en-US.json",
      "test/plugin.test.tsx",
      "tsconfig.json",
      "package.json",
      "AGENTS.md",
      "CLAUDE.md",
      "README.md",
      ".gitignore",
    ]) {
      expect(existsSync(path.join(dir, file)), file).toBe(true);
    }
    const manifest = JSON.parse(
      readFileSync(path.join(dir, "valuz-plugin.json"), "utf8"),
    );
    expect(manifest).toMatchObject({
      manifestVersion: 1,
      id: "acme.hello",
      version: "0.1.0",
      name: { "zh-CN": "Acme Hello", "en-US": "Acme Hello" },
      publisher: { name: "acme" },
      engines: { "valuz-plugin-api": "^1.0.0" },
      frontend: { entry: "frontend/index.js" },
      permissions: ["projects:read"],
      locales: "locales",
    });
    expect(existsSync(manifest.$schema)).toBe(true);
    const readme = readFileSync(path.join(dir, "README.md"), "utf8");
    expect(readme).toContain("Valuz App Plugin");
    expect(readme).toContain("valuz plugin app install");
    expect(readme).toContain("Settings → Plugins → App Plugins");
    const guidance = readFileSync(path.join(dir, "AGENTS.md"), "utf8");
    expect(guidance).toContain("valuz plugin app status acme.hello");
    expect(guidance).toContain("valuz plugin agent …");
    expect(readFileSync(path.join(dir, "CLAUDE.md"), "utf8").trim()).toBe(
      "@AGENTS.md",
    );
    expect(readFileSync(path.join(dir, "AGENTS.md"), "utf8")).toContain(
      "x:acme.hello:hello",
    );
    expect(cli(["create", "hello", "--id", "acme.hello"], root).stderr).toMatch(
      /not empty/,
    );

    const built = cli(["build"], dir);
    expect(built.stderr).toBe("");
    expect(built.code).toBe(0);
    expect(built.stdout).toMatch(/frontend\/index\.js\s+[\d.]+ KB/);
    expect(built.stdout).toMatch(/frontend\/index\.css/);

    const tested = cli(["test"], dir);
    expect(tested.code, tested.stdout + tested.stderr).toBe(0);
    expect(tested.stdout).toMatch(/pass 4/);

    const validated = cli(["validate"], dir);
    expect(validated.code, validated.stdout).toBe(0);
    expect(validated.stdout).toContain("acme.hello 0.1.0: valid");
    const json = JSON.parse(cli(["validate", "--json"], dir).stdout);
    expect(json).toMatchObject({ ok: true, errors: [], warnings: [] });

    const first = JSON.parse(cli(["pack", "--json"], dir).stdout);
    expect(first.path).toBe(path.join(dir, "dist", "acme.hello-0.1.0.zip"));
    expect(first.files).toEqual([
      "README.md",
      "frontend/index.css",
      "frontend/index.js",
      "locales/en-US.json",
      "locales/zh-CN.json",
      "valuz-plugin.json",
    ]);
    const second = cli(["pack"], dir);
    expect(second.code).toBe(0);
    expect(second.stdout).toContain(`sha256 ${first.sha256}`);
    expect(
      createHash("sha256").update(readFileSync(first.path)).digest("hex"),
    ).toBe(first.sha256);

    // A failing test fails the command.
    writeFileSync(
      path.join(dir, "test", "broken.test.ts"),
      'import { test } from "node:test";\nimport assert from "node:assert/strict";\ntest("broken", () => assert.equal(1, 2));\n',
    );
    expect(cli(["test"], dir).code).toBe(1);

    // An invalid plugin is refused by validate and pack.
    writeFileSync(
      path.join(dir, "valuz-plugin.json"),
      JSON.stringify({ ...manifest, version: "one" }),
    );
    expect(cli(["validate"], dir).code).toBe(1);
    const refused = cli(["pack"], dir);
    expect(refused.code).toBe(1);
    expect(refused.stdout).toMatch(/error: version: "one" does not match/);
  }, 120_000);

  it("dev keeps watching when the backend is unreachable", async () => {
    const root = tempDir();
    expect(cli(["create", "p", "--id", "acme.devdown"], root).code).toBe(0);
    const { output, child } = await runDev(
      ["--backend", "http://127.0.0.1:9"],
      path.join(root, "p"),
      (text) => text.includes("watching for changes"),
    );
    await stop(child);
    expect(output).toMatch(
      /cannot reach the Valuz backend at http:\/\/127\.0\.0\.1:9/,
    );
    expect(output).toMatch(/Still watching/);
  }, 60_000);

  it("dev links the directory and reloads after a rebuild", async () => {
    const root = tempDir();
    expect(cli(["create", "p", "--id", "acme.devup"], root).code).toBe(0);
    const dir = path.join(root, "p");
    const calls: Array<{
      method: string;
      url: string;
      body: unknown;
      auth: string | undefined;
    }> = [];
    let revision = 1;
    const server: Server = createServer((req, res) => {
      let raw = "";
      req.on("data", (chunk) => (raw += chunk));
      req.on("end", () => {
        calls.push({
          method: req.method ?? "",
          url: req.url ?? "",
          body: raw ? JSON.parse(raw) : null,
          auth: req.headers.authorization,
        });
        res.setHeader("content-type", "application/json");
        if (req.url === "/v1/extensions/third-party/dev-link") {
          res.end(
            JSON.stringify({
              plugin: {
                id: "acme.devup",
                version: "0.1.0",
                status: "enabled",
                revision,
              },
            }),
          );
        } else if (req.url === "/v1/extensions/third-party/acme.devup/reload") {
          revision += 1;
          res.end(JSON.stringify({ plugin: { id: "acme.devup", revision } }));
        } else {
          res.statusCode = 404;
          res.end(JSON.stringify({ detail: "not found" }));
        }
      });
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    let edited = false;
    try {
      const { output, child } = await runDev(
        ["--backend", base, "--token", "t0k"],
        dir,
        (text) => text.includes("reloaded acme.devup"),
        (text) => {
          if (!edited && text.includes("watching for changes")) {
            edited = true;
            const file = path.join(dir, "src", "index.tsx");
            writeFileSync(file, `${readFileSync(file, "utf8")}\n// edited\n`);
          }
        },
      );
      await stop(child);
      expect(output).toMatch(/linked acme\.devup 0\.1\.0 \(dev\)/);
      expect(output).toMatch(/reloaded acme\.devup \(revision 2\)/);
      expect(calls[0]).toEqual({
        method: "POST",
        url: "/v1/extensions/third-party/dev-link",
        body: { path: realpathSync(dir) },
        auth: "Bearer t0k",
      });
      expect(calls[1]).toMatchObject({
        method: "POST",
        url: "/v1/extensions/third-party/acme.devup/reload",
      });
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  }, 60_000);
});
