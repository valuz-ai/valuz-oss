import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = path.resolve(__dirname, "../../../../..");
const frontendRoot = path.join(repoRoot, "frontend");
const ignoredDirs = new Set([
  ".turbo",
  "dist",
  "dist-electron",
  "node_modules",
  "release",
]);

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir)) {
    if (ignoredDirs.has(entry)) continue;
    const fullPath = path.join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      collectSourceFiles(fullPath, out);
    } else if (/\.(ts|tsx)$/.test(entry)) {
      out.push(fullPath);
    }
  }
  return out;
}

function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("frontend backend API transport", () => {
  it("routes /v1 API requests through the shared request layer", () => {
    const offenders = collectSourceFiles(frontendRoot)
      .filter((file) => !file.endsWith("/packages/core/src/api/request.ts"))
      .filter((file) => !file.includes("/apps/desktop/src/main/"))
      .filter((file) => {
        const source = stripComments(readFileSync(file, "utf8"));
        return /\bfetch\s*\(/.test(source) && /\/v1\//.test(source);
      })
      .map((file) => path.relative(repoRoot, file));

    expect(offenders).toEqual([]);
  });
});
