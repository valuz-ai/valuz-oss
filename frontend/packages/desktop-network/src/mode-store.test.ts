import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  readPersistedEgressMode,
  writePersistedEgressMode,
} from "./mode-store";

describe("egress mode persistence", () => {
  it("defaults to client management and persists the selected owner", () => {
    const root = mkdtempSync(join(tmpdir(), "valuz-egress-mode-"));
    expect(readPersistedEgressMode(root)).toBe("off");

    writePersistedEgressMode(root, "off");
    expect(readPersistedEgressMode(root)).toBe("off");

    writePersistedEgressMode(root, "direct");
    expect(readPersistedEgressMode(root)).toBe("auto");
    expect(readFileSync(join(root, "network-egress.json"), "utf8")).not.toContain(
      "direct",
    );
  });
});
