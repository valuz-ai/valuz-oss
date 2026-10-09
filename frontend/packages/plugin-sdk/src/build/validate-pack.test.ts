import "./test-env";

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { inflateRawSync } from "node:zlib";
import { afterAll, describe, expect, it } from "vitest";

import { buildPlugin } from "./build-plugin.mjs";
import { loadSchema } from "./manifest.mjs";
import { PackError, packFileSet, packPlugin } from "./pack.mjs";
import { validateSchema } from "./schema.mjs";
import { compareVersions, rangeIncludes } from "./semver.mjs";
import { validatePlugin } from "./validate.mjs";
import { crc32, createZip } from "./zip.mjs";
import {
  cleanupTempDirs,
  fixturePlugin,
  manifest,
  tempDir,
  writeFiles,
} from "./test-fixtures";

afterAll(cleanupTempDirs);

async function builtPlugin(
  extra: Record<string, unknown> = {},
): Promise<string> {
  const dir = fixturePlugin(tempDir(), extra);
  const report = await buildPlugin(dir);
  expect(report.errors).toEqual([]);
  return dir;
}

function setManifest(dir: string, patch: Record<string, unknown>): void {
  const file = path.join(dir, "valuz-plugin.json");
  const current = JSON.parse(readFileSync(file, "utf8"));
  writeFileSync(file, JSON.stringify({ ...current, ...patch }, null, 2));
}

/** Read a zip back: ``{ name: { data, method, externalAttr, madeBy, dosDate } }``. */
function readZip(buffer: Buffer) {
  const end = buffer.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  const count = buffer.readUInt16LE(end + 10);
  let offset = buffer.readUInt32LE(end + 16);
  const entries: Record<
    string,
    {
      data: Buffer;
      method: number;
      externalAttr: number;
      madeBy: number;
      dosDate: number;
      dosTime: number;
    }
  > = {};
  for (let i = 0; i < count; i += 1) {
    expect(buffer.readUInt32LE(offset)).toBe(0x02014b50);
    const madeBy = buffer.readUInt16LE(offset + 4);
    const method = buffer.readUInt16LE(offset + 10);
    const dosTime = buffer.readUInt16LE(offset + 12);
    const dosDate = buffer.readUInt16LE(offset + 14);
    const crc = buffer.readUInt32LE(offset + 16);
    const compressed = buffer.readUInt32LE(offset + 20);
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const externalAttr = buffer.readUInt32LE(offset + 38);
    const local = buffer.readUInt32LE(offset + 42);
    const name = buffer.toString("utf8", offset + 46, offset + 46 + nameLength);
    const localNameLength = buffer.readUInt16LE(local + 26);
    const localExtra = buffer.readUInt16LE(local + 28);
    const start = local + 30 + localNameLength + localExtra;
    const body = buffer.subarray(start, start + compressed);
    const data = method === 8 ? inflateRawSync(body) : Buffer.from(body);
    expect(crc32(data)).toBe(crc);
    expect(extraLength).toBe(0);
    entries[name] = { data, method, externalAttr, madeBy, dosDate, dosTime };
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

describe("validateSchema", () => {
  const schema = loadSchema();

  it("accepts a complete manifest", () => {
    expect(
      validateSchema(
        schema,
        manifest({
          description: "x",
          requires: ["connector:acme-data"],
          config: { type: "object" },
          automations: [
            {
              name: "risk",
              runtime: "python",
              entry: "automations/risk.py",
              trigger: { cron: "0 8 * * 1-5" },
            },
          ],
          locales: "locales",
          icon: "icon.svg",
        }),
      ),
    ).toEqual([]);
  });

  it("reports schema violations with paths", () => {
    const errors = validateSchema(schema, {
      manifestVersion: 2,
      id: "Acme",
      version: "1.0",
      name: {},
      publisher: {},
      engines: {},
      frontend: { entry: "../x.js" },
      permissions: ["projects:read", "projects:read", "root"],
      automations: [
        { name: "x", runtime: "node", entry: "a.py", trigger: "daily" },
      ],
      extra: true,
    });
    expect(errors).toEqual(
      expect.arrayContaining([
        "manifestVersion: must be 1",
        expect.stringMatching(/^id: "Acme" does not match/),
        expect.stringMatching(/^version: "1.0" does not match/),
        "name: must have at least 1 entry",
        "publisher.name: is required",
        "engines.valuz-plugin-api: is required",
        expect.stringMatching(/^frontend\.entry: "\.\.\/x\.js" does not match/),
        'permissions: must not contain duplicates ("projects:read")',
        expect.stringMatching(/^permissions\[2\]: "root" is not one of/),
        expect.stringMatching(
          /^automations\[0\]\.runtime: "node" is not one of/,
        ),
        'automations[0].trigger: must be "manual"',
        "extra: is not a known field",
      ]),
    );
  });
});

describe("semver", () => {
  it("compares versions and ranges", () => {
    expect(compareVersions("1.2.3", "1.10.0")).toBe(-1);
    expect(compareVersions("1.0.0-beta", "1.0.0")).toBe(-1);
    expect(rangeIncludes("^1.0.0", "1.0.0")).toBe(true);
    expect(rangeIncludes("^1.2.0", "1.0.0")).toBe(false);
    expect(rangeIncludes(">=0.9 <2", "1.0.0")).toBe(true);
    expect(rangeIncludes("~0.9.0 || 1.x", "1.0.0")).toBe(true);
    expect(rangeIncludes("^2.0.0", "1.0.0")).toBe(false);
    expect(rangeIncludes("1.0.0 - 1.5", "1.0.0")).toBe(true);
    expect(rangeIncludes("*", "1.0.0")).toBe(true);
    expect(() => rangeIncludes("^a.b", "1.0.0")).toThrow();
  });
});

describe("validatePlugin", () => {
  it("passes a built plugin", async () => {
    const dir = await builtPlugin();
    const result = validatePlugin(dir);
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.manifest?.id).toBe("acme.fixture");
  });

  it("applies the x-valuz-rules", async () => {
    const dir = await builtPlugin();
    setManifest(dir, {
      id: "valuz.fixture",
      backend: { runtime: "node" },
      icon: "missing.svg",
      config: { type: "array" },
      engines: { "valuz-plugin-api": "^2.0.0" },
      automations: [
        { name: "a", runtime: "python", entry: "automations/missing.py" },
        {
          name: "a",
          runtime: "shell",
          entry: "automations/../x.sh",
          input: { type: "string" },
        },
      ],
    });
    const { ok, errors, warnings } = validatePlugin(dir);
    expect(ok).toBe(false);
    expect(errors).toEqual(
      expect.arrayContaining([
        'id: "valuz.fixture" uses the reserved prefix "valuz."',
        "backend: B-level plugins are not supported by plugin API 1.x",
        "automations[1].entry: \"automations/../x.sh\" must not contain a '..' segment",
        'automations[0].entry: "automations/missing.py" does not exist in the package',
        'automations[1].name: "a" is used by more than one automation',
        'automations[1].input: must be a JSON Schema whose type is "object"',
        'config: must be a JSON Schema whose type is "object"',
      ]),
    );
    expect(warnings).toEqual(
      expect.arrayContaining([
        'icon: "missing.svg" does not exist in the package',
        expect.stringMatching(
          /^engines\.valuz-plugin-api: "\^2\.0\.0" does not include plugin API 1\.0\.0/,
        ),
      ]),
    );
  });

  it("checks the built entry: exists, ES module, default export, no runtime imports", async () => {
    const dir = await builtPlugin();
    writeFiles(dir, {
      "frontend/index.js":
        'import React from "react";\nexport const x = React;\n',
    });
    const result = validatePlugin(dir);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^frontend\.entry: "frontend\/index\.js" imports "react" at runtime/,
        ),
        expect.stringMatching(/has no default export/),
      ]),
    );
    expect(result.warnings).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/does not mention the id "acme\.fixture"/),
      ]),
    );

    writeFiles(dir, { "frontend/index.js": "export default {" });
    expect(validatePlugin(dir).errors).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/is not a valid ES module/),
      ]),
    );

    setManifest(dir, { frontend: { entry: "frontend/index.css" } });
    expect(validatePlugin(dir).errors).toContain(
      'frontend.entry: "frontend/index.css" must be an ES module (.js or .mjs)',
    );

    setManifest(dir, {
      frontend: {
        entry: "frontend/missing.js",
        styles: ["frontend/missing.css"],
      },
    });
    expect(validatePlugin(dir).errors).toEqual(
      expect.arrayContaining([
        'frontend.entry: "frontend/missing.js" does not exist in the package',
        'frontend.styles[0]: "frontend/missing.css" does not exist in the package',
      ]),
    );
  });

  it("checks locales and warns about files outside the packed set", async () => {
    const dir = await builtPlugin({
      "locales/en-US.json": { hello: "Hello", only: { en: "x" } },
      "locales/zh-CN.json": "{ not json",
      "locales/english.json": {},
      "dist/plugin.js": 'export default { id: "acme.fixture" };\n',
    });
    setManifest(dir, {
      locales: "locales",
      frontend: { entry: "dist/plugin.js" },
    });
    const { errors, warnings } = validatePlugin(dir);
    expect(errors).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/^locales\/zh-CN\.json: invalid JSON/),
      ]),
    );
    expect(warnings).toEqual(
      expect.arrayContaining([
        "locales/english.json: locale files are named <lang>.json (e.g. zh-CN.json, en-US.json)",
        expect.stringMatching(
          /^frontend\.entry: "dist\/plugin\.js" is outside the packed file set/,
        ),
      ]),
    );

    writeFiles(dir, { "locales/zh-CN.json": { hello: "你好" } });
    expect(validatePlugin(dir).warnings).toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^locales\/zh-CN\.json: missing 1 key other locales have \(only\.en\)/,
        ),
      ]),
    );

    setManifest(dir, { locales: "lang" });
    expect(validatePlugin(dir).warnings).toContain(
      'locales: "lang" does not exist in the package',
    );
  });

  it("reports a missing or broken manifest", () => {
    const dir = tempDir();
    expect(validatePlugin(dir).errors).toEqual([
      "valuz-plugin.json not found at the package root",
    ]);
    writeFiles(dir, { "valuz-plugin.json": "[1]" });
    expect(validatePlugin(dir).errors).toEqual([
      "valuz-plugin.json: must be a JSON object",
    ]);
  });
});

describe("packPlugin", () => {
  it("packs the file set deterministically", async () => {
    const dir = await builtPlugin({
      "LICENSE.md": "MIT\n",
      "icon.svg": "<svg/>",
      "automations/run.py": "print('hi')\n",
      "automations/node_modules/x.js": "skip",
      "automations/.env": "SECRET=1",
      "automations/src/skip.py": "skip",
      "frontend/.DS_Store": "junk",
      "test/plugin.test.tsx": "skip",
      "notes.txt": "skip",
    });
    setManifest(dir, {
      icon: "icon.svg",
      automations: [
        { name: "run", runtime: "python", entry: "automations/run.py" },
      ],
    });

    const first = packPlugin(dir);
    expect(first.path).toBe(path.join(dir, "dist", "acme.fixture-1.2.3.zip"));
    expect(first.files).toEqual([
      "LICENSE.md",
      "README.md",
      "automations/run.py",
      "frontend/index.css",
      "frontend/index.js",
      "icon.svg",
      "locales/en-US.json",
      "locales/zh-CN.json",
      "valuz-plugin.json",
    ]);
    const bytes = readFileSync(first.path);
    expect(first.sha256).toBe(createHash("sha256").update(bytes).digest("hex"));
    expect(first.size).toBe(bytes.length);

    const second = packPlugin(dir);
    expect(second.sha256).toBe(first.sha256);
    expect(
      packFileSet(
        dir,
        JSON.parse(readFileSync(path.join(dir, "valuz-plugin.json"), "utf8")),
      ),
    ).toEqual(first.files);

    const entries = readZip(bytes);
    expect(Object.keys(entries)).toEqual(first.files);
    expect(entries["frontend/index.js"].data.toString("utf8")).toBe(
      readFileSync(path.join(dir, "frontend/index.js"), "utf8"),
    );
    for (const entry of Object.values(entries)) {
      expect(entry.dosDate).toBe(0x21); // 1980-01-01
      expect(entry.dosTime).toBe(0);
      expect(entry.externalAttr >>> 16).toBe(0o100644);
      expect(entry.madeBy >> 8).toBe(3); // unix
    }

    const out = tempDir();
    expect(packPlugin(dir, { outDir: out }).path).toBe(
      path.join(out, "acme.fixture-1.2.3.zip"),
    );
  });

  it("refuses an invalid plugin or a manifest file outside the packed set", async () => {
    const dir = await builtPlugin();
    setManifest(dir, { id: "Bad" });
    expect(() => packPlugin(dir)).toThrow(PackError);
    try {
      packPlugin(dir);
    } catch (error) {
      expect((error as PackError).validation?.ok).toBe(false);
    }

    setManifest(dir, {
      id: "acme.fixture",
      frontend: { entry: "dist/plugin.js" },
    });
    writeFiles(dir, {
      "dist/plugin.js": 'export default { id: "acme.fixture" };\n',
    });
    expect(() => packPlugin(dir)).toThrow(
      /the packed file set is incomplete.*frontend\.entry: "dist\/plugin\.js"/,
    );
  });

  it("refuses symlinks that leave the plugin directory", async () => {
    const dir = await builtPlugin();
    const outside = tempDir();
    writeFiles(outside, { "secret.txt": "secret" });
    mkdirSync(path.join(dir, "automations"), { recursive: true });
    symlinkSync(
      path.join(outside, "secret.txt"),
      path.join(dir, "automations", "secret.txt"),
    );
    expect(() => packPlugin(dir)).toThrow(
      /symlink pointing outside the plugin directory/,
    );
  });
});

describe("createZip", () => {
  it("stores incompressible / empty files and flags UTF-8 names", () => {
    const zip = createZip([
      { name: "empty.txt", data: Buffer.alloc(0) },
      {
        name: "目录/文件.txt",
        data: Buffer.from("hello hello hello hello hello"),
      },
    ]);
    const entries = readZip(zip);
    expect(entries["empty.txt"].method).toBe(0);
    expect(entries["目录/文件.txt"].data.toString()).toBe(
      "hello hello hello hello hello",
    );
    expect(() => createZip([{ name: "../x", data: Buffer.alloc(1) }])).toThrow(
      /invalid entry name/,
    );
  });
});
