import { describe, expect, it } from "vitest";

import {
  buildFileRef,
  buildLocalFileUrl,
  isFileRef,
  parseFileRef,
  parseLocalFileUrl,
} from "./file-uri";

// The cross-layer contract: these exact paths are mirrored in the backend's
// tests/modules/files/test_file_resolve.py::TestUri so the TS and Python codecs
// stay in lockstep. Add a nasty path here → add it there too.
const CONTRACT_PATHS = [
  "/data/valuz_data/workspace/u/proj/a.md",
  "/Users/u/My Proj/r.pdf", // space
  "/tmp/name+with&chars.txt", // + and &
  "/tmp/a#b.txt", // # (fragment delimiter if not encoded)
  "/tmp/a%b.txt", // literal percent
  "/Users/river/Valuz/示例项目/晶合集成_688249_财务预测模型.xlsx", // CJK — the real 404 case
];

describe("file-uri codec", () => {
  describe("valuz-file:// round-trip", () => {
    it.each(CONTRACT_PATHS)("build→parse is identity for %s", (path) => {
      const ref = buildFileRef(path);
      expect(isFileRef(ref)).toBe(true);
      expect(ref.startsWith("valuz-file:///")).toBe(true); // canonical three-slash
      expect(parseFileRef(ref)).toBe(path);
    });
  });

  describe("valuz-local:// round-trip", () => {
    it.each(CONTRACT_PATHS)("build→parse is identity for %s", (path) => {
      const url = buildLocalFileUrl(path);
      // Fixed ``f`` authority + real path in the path component, so Chromium's
      // standard-scheme parser can't promote the first segment to the host.
      expect(url.startsWith("valuz-local://f/")).toBe(true);
      expect(parseLocalFileUrl(url)).toBe(path);
    });

    it("keeps the leading segment out of the host (the 404 root cause)", () => {
      // The real path must sit AFTER the pinned host so `/Users` survives —
      // buildLocalFileUrl("/Users/x") must NOT put "Users" in the authority.
      const url = buildLocalFileUrl("/Users/River/a.xlsx");
      expect(new URL(url).host).toBe("f");
      expect(parseLocalFileUrl(url)).toBe("/Users/River/a.xlsx"); // case preserved
    });
  });

  describe("valuz-file:// is TOLERANT (models may drop a slash)", () => {
    it("repairs a two-slash ref so //abs === ///abs", () => {
      expect(parseFileRef("valuz-file://Users/u/a.md")).toBe("/Users/u/a.md");
      expect(parseFileRef("valuz-file:///Users/u/a.md")).toBe("/Users/u/a.md");
    });

    it("preserves case and percent-encoding in the first segment", () => {
      // The first segment used to land in ``url.host``, so anything the
      // authority grammar touches had to survive a round through it.
      expect(parseFileRef("valuz-file://Users/River/a.md")).toBe(
        "/Users/River/a.md",
      );
      expect(parseFileRef("valuz-file://Us%65rs/u/a.md")).toBe("/Users/u/a.md");
    });

    it("keeps the drive colon on a two-slash Windows ref", () => {
      // ``C:`` parses as host ``C`` plus an empty port, so folding ``url.host``
      // back on produced ``/C/Users/u/x.txt`` — a path that exists nowhere, and
      // one the Python mirror never produced. Both spellings must agree.
      expect(parseFileRef("valuz-file://C:/Users/u/x.txt")).toBe(
        "C:/Users/u/x.txt",
      );
      expect(parseFileRef("valuz-file:///C:/Users/u/x.txt")).toBe(
        "C:/Users/u/x.txt",
      );
    });
  });

  describe("windows drive", () => {
    it("round-trips C:/…", () => {
      expect(parseFileRef(buildFileRef("C:/Users/u/x.txt"))).toBe(
        "C:/Users/u/x.txt",
      );
    });

    it("round-trips a backslash-spelled path", () => {
      expect(parseFileRef(buildFileRef("C:\\Users\\u\\x.txt"))).toBe(
        "C:/Users/u/x.txt",
      );
    });

    it("accepts the raw drive spelling a model writes by hand", () => {
      // ``buildFileRef`` percent-encodes the colon (``C%3A``); the system
      // prompt teaches the literal form. Both must parse to the same path.
      expect(parseFileRef("valuz-file:///C%3A/Users/u/x.txt")).toBe(
        "C:/Users/u/x.txt",
      );
      expect(parseFileRef("valuz-file:///C:/Users/u/x.txt")).toBe(
        "C:/Users/u/x.txt",
      );
    });
  });

  describe("rejects foreign schemes", () => {
    it("returns null", () => {
      expect(isFileRef("https://x/y")).toBe(false);
      expect(parseFileRef("https://x/y")).toBeNull();
      expect(parseLocalFileUrl("https://x/y")).toBeNull();
    });
  });
});
