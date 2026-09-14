import { describe, expect, it } from "vitest";

import {
  hasUriScheme,
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

  it("requires a separator, so a multi-character scheme is not a drive", () => {
    expect(isWindowsDrivePath("https://example.com/a.md")).toBe(false);
    expect(isWindowsDrivePath("mailto:ada@example.com")).toBe(false);
    expect(isWindowsDrivePath("/Users/u/proj/a.md")).toBe(false);
    expect(isWindowsDrivePath("reports/q3.md")).toBe(false);
  });
});

describe("hasUriScheme", () => {
  it("reports a scheme for every URL shape a link can carry", () => {
    for (const value of [
      "https://example.com/a.md",
      "http://example.com/a.md",
      "mailto:ada@example.com",
      "data:text/plain,hello",
      "javascript:alert(1)",
      "vbscript:msgbox",
      "file:///Users/u/proj/a.md",
      "valuz-file:///Users/u/proj/a.md",
      "valuz-local://f/Users/u/proj/a.md",
      "evidence://ev_mcp_abc123",
    ]) {
      expect(hasUriScheme(value)).toBe(true);
    }
  });

  it("reports no scheme for a filesystem path", () => {
    for (const value of [
      "C:\\Users\\u\\proj\\a.md",
      "C:/Users/u/proj/a.md",
      "c:/users/u/proj/a.md",
      "/Users/u/proj/a.md",
      "reports/q3.md",
      "./reports/q3.md",
      "",
    ]) {
      expect(hasUriScheme(value)).toBe(false);
    }
  });

  it("calls a drive-shaped prefix with an authority a scheme", () => {
    // The regression this predicate exists to prevent: subtracting every
    // drive-shaped prefix from the scheme test handed `s://evil.example/x`
    // back as a local file on EVERY platform, macOS and Linux included.
    expect(hasUriScheme("s://evil.example/x")).toBe(true);
    expect(hasUriScheme("a://host/x")).toBe(true);
    expect(hasUriScheme("x:\\\\server\\share")).toBe(true);
    // A drive root never doubles its separator, so this stays a scheme too —
    // matching the behaviour before drive letters were considered at all.
    expect(hasUriScheme("C://weird/double")).toBe(true);
  });

  it("resolves the undecidable one-letter case in favour of the path", () => {
    // `a:/x` is character-for-character a drive specifier AND a one-letter
    // scheme with no authority. `a:` is a real Windows drive, so the path
    // reading wins — documented, deliberate, and matching path.win32.
    expect(hasUriScheme("a:/host/x")).toBe(false);
    expect(hasUriScheme("a:\\host\\x")).toBe(false);
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

  it("matches a Windows root regardless of case", () => {
    // A model that lowercases the drive or a segment in prose must not make a
    // file inside the project read as outside it.
    expect(toProjectRelativePath("c:/proj/a.md", "C:\\proj")).toBe("a.md");
    expect(toProjectRelativePath("C:\\PROJ\\a.md", "c:/proj")).toBe("a.md");
    expect(toProjectRelativePath("C:/Proj/Sub/A.md", "C:\\proj")).toBe(
      "Sub/A.md",
    );
  });

  it("preserves the real casing of the returned relative path", () => {
    expect(toProjectRelativePath("c:/proj/Reports/Q3.md", "C:\\Proj")).toBe(
      "Reports/Q3.md",
    );
  });

  it("keeps a POSIX root case-sensitive", () => {
    // Linux really does have two directories here; folding would merge them.
    expect(toProjectRelativePath("/Users/u/PROJ/a.md", ROOT)).toBeNull();
    expect(toProjectRelativePath("/users/u/proj/a.md", ROOT)).toBeNull();
  });

  it("is not fooled by a Windows sibling with the same prefix", () => {
    expect(toProjectRelativePath("c:/proj-evil/a.md", "C:\\proj")).toBeNull();
  });

  it("returns null for a Windows root itself, in any case", () => {
    expect(toProjectRelativePath("c:/proj", "C:\\Proj")).toBeNull();
  });

  it("passes an already-relative path through", () => {
    expect(toProjectRelativePath("reports/q3.md", ROOT)).toBe("reports/q3.md");
  });

  it("returns null when the root is unknown and the path is absolute", () => {
    expect(toProjectRelativePath("/tmp/x.md", "")).toBeNull();
  });
});
