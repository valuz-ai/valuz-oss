import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The module imports electron (app/protocol) at module scope; the pure
// function under test never touches it, so a minimal mock keeps vitest from
// loading the real electron module (which throws outside Electron).
vi.mock("electron", () => ({
  app: {},
  protocol: { handle: vi.fn() },
}));

import { resolveRendererTarget } from "./renderer-protocol";

describe("resolveRendererTarget", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), "vz-renderer-"));
    writeFileSync(path.join(root, "index.html"), "<html></html>");
    writeFileSync(path.join(root, "assets.js"), "// js");
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("serves the shell for the scheme root", () => {
    const hit = resolveRendererTarget("valuz-app://app/", root);
    expect(hit?.abs).toBe(path.join(root, "index.html"));
    expect(hit?.mime).toBe("text/html");
  });

  it("serves an exact file under the root", () => {
    const hit = resolveRendererTarget("valuz-app://app/assets.js", root);
    expect(hit?.abs).toBe(path.join(root, "assets.js"));
    expect(hit?.mime).toBe("text/javascript");
  });

  it("falls back to the shell for an extensionless path", () => {
    const hit = resolveRendererTarget("valuz-app://app/some/route", root);
    expect(hit?.abs).toBe(path.join(root, "index.html"));
  });

  it("never serves a file outside the root", () => {
    // The WHATWG URL parser collapses ``%2e%2e`` dot segments and ``path.join``
    // clamps absolute segments under the root — survivors stay inside the
    // bundle root (garbage in → the shell, never a host file). A path that
    // still escapes after normalization is refused outright.
    const inside = resolveRendererTarget(
      "valuz-app://app/%2e%2e/%2e%2e/etc/passwd",
      root,
    );
    expect(inside?.abs).toBe(path.join(root, "index.html"));
    expect(
      resolveRendererTarget("valuz-app://app//etc/passwd", root)?.abs,
    ).toBe(path.join(root, "index.html"));
    // Decodes to /../../etc/passwd — escapes the root → refused.
    expect(
      resolveRendererTarget("valuz-app://app/..%2f..%2fetc/passwd", root),
    ).toBeNull();
  });

  it("refuses a foreign host, credentials and NUL bytes", () => {
    expect(
      resolveRendererTarget("valuz-app://evil/assets.js", root),
    ).toBeNull();
    expect(
      resolveRendererTarget("valuz-app://u:p@app/assets.js", root),
    ).toBeNull();
    expect(resolveRendererTarget("valuz-app://app/a%00b.js", root)).toBeNull();
  });
});
