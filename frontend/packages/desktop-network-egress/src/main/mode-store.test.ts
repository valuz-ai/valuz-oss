import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  readPersistedEgressMode,
  writePersistedEgressMode,
} from "./mode-store";

describe("egress mode persistence", () => {
  it("distinguishes a new profile from an explicit client-managed choice", () => {
    const root = mkdtempSync(join(tmpdir(), "valuz-egress-mode-"));
    expect(readPersistedEgressMode(root)).toBeNull();

    writePersistedEgressMode(root, "off");
    expect(readPersistedEgressMode(root)).toBe("off");

    writePersistedEgressMode(root, "direct");
    expect(readPersistedEgressMode(root)).toBe("auto");
    expect(readFileSync(join(root, "network-egress.json"), "utf8")).not.toContain(
      "direct",
    );
  });

  it("reads the legacy compatibilityMode format during rollback-safe migration", () => {
    const root = mkdtempSync(join(tmpdir(), "valuz-egress-mode-"));
    const path = join(root, "network-egress.json");

    writeFileSync(path, JSON.stringify({ compatibilityMode: false }));
    expect(readPersistedEgressMode(root)).toBe("auto");

    writeFileSync(path, JSON.stringify({ compatibilityMode: true }));
    expect(readPersistedEgressMode(root)).toBe("off");

    writePersistedEgressMode(root, "auto");
    const written = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
    expect(written).toMatchObject({
      version: 1,
      mode: "auto",
      compatibilityMode: false,
    });
  });
});
