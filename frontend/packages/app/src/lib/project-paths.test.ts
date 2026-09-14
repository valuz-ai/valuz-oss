import { describe, expect, it } from "vitest";

import {
  isAbsolutePath,
  isWindowsDrivePath,
  toAbsoluteProjectPath,
  toProjectRelativePath,
} from "./project-paths";

const ROOT = "/Users/u/proj";

describe("isWindowsDrivePath", () => {
  it("accepts both spellings of a drive specifier", () => {
    expect(isWindowsDrivePath("C:\\proj\\a.md")).toBe(true);
    expect(isWindowsDrivePath("c:/proj/a.md")).toBe(true);
  });

  it("requires a separator, so a real URI scheme is not a drive", () => {
    // The point of the predicate: callers use it to subtract drive letters
    // from a "does this still look like scheme:..." test. A multi-character
    // scheme must stay on the scheme side of that line.
    expect(isWindowsDrivePath("https://example.com/a.md")).toBe(false);
    expect(isWindowsDrivePath("mailto:ada@example.com")).toBe(false);
    expect(isWindowsDrivePath("/Users/u/proj/a.md")).toBe(false);
    expect(isWindowsDrivePath("reports/q3.md")).toBe(false);
  });
});

describe("isAbsolutePath", () => {
  it("accepts both Windows spellings", () => {
    // The drifted copy only matched "C:/", so "C:\\x" read as relative there.
    expect(isAbsolutePath("C:\\proj\\a.md")).toBe(true);
    expect(isAbsolutePath("C:/proj/a.md")).toBe(true);
  });

  it("treats a plain relative path as relative", () => {
    expect(isAbsolutePath("reports/q3.md")).toBe(false);
  });
});

describe("toAbsoluteProjectPath", () => {
  it("joins a relative path onto the project root", () => {
    expect(toAbsoluteProjectPath("reports/q3.md", ROOT)).toBe(
      "/Users/u/proj/reports/q3.md",
    );
  });

  it("does not double the separator when the root has a trailing one", () => {
    expect(toAbsoluteProjectPath("a.md", `${ROOT}/`)).toBe(
      "/Users/u/proj/a.md",
    );
  });

  it("uses backslashes for a Windows root", () => {
    expect(toAbsoluteProjectPath("a.md", "C:\\proj")).toBe("C:\\proj\\a.md");
  });

  it("leaves an absolute path alone", () => {
    expect(toAbsoluteProjectPath("/tmp/x.md", ROOT)).toBe("/tmp/x.md");
  });

  it("leaves the path alone when the root is unknown", () => {
    expect(toAbsoluteProjectPath("a.md", "")).toBe("a.md");
  });
});

describe("toProjectRelativePath", () => {
  it("strips the project root", () => {
    expect(toProjectRelativePath(`${ROOT}/reports/q3.md`, ROOT)).toBe(
      "reports/q3.md",
    );
  });

  it("returns null for a path outside the project", () => {
    expect(toProjectRelativePath("/tmp/x.md", ROOT)).toBeNull();
  });

  it("returns null for the root itself", () => {
    expect(toProjectRelativePath(ROOT, ROOT)).toBeNull();
  });

  it("is not fooled by a sibling root with the same prefix", () => {
    expect(toProjectRelativePath("/Users/u/proj-evil/a.md", ROOT)).toBeNull();
  });

  it("normalizes backslashes and strips a Windows root", () => {
    expect(toProjectRelativePath("C:\\proj\\a.md", "C:\\proj")).toBe("a.md");
  });

  it("passes an already-relative path through", () => {
    expect(toProjectRelativePath("reports/q3.md", ROOT)).toBe("reports/q3.md");
  });

  it("returns null when the root is unknown and the path is absolute", () => {
    expect(toProjectRelativePath("/tmp/x.md", "")).toBeNull();
  });
});
