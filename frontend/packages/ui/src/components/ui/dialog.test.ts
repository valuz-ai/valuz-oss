import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("DialogContent design contract", () => {
  it("uses the surface token reserved for cards and overlays", () => {
    const sourcePath = [
      resolve(process.cwd(), "packages/ui/src/components/ui/dialog.tsx"),
      resolve(process.cwd(), "src/components/ui/dialog.tsx"),
    ].find(existsSync);
    expect(sourcePath).toBeDefined();
    const source = readFileSync(sourcePath!, "utf8");
    const defaultClasses = source.match(
      /fixed top-\[50%\][\s\S]*?sm:max-w-lg/,
    )?.[0];

    expect(defaultClasses).toContain("bg-surface");
    expect(defaultClasses).not.toContain("bg-background");
  });
});
