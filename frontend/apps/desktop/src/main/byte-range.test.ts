import { describe, expect, it } from "vitest";

import { parseByteRange } from "./byte-range";

describe("parseByteRange", () => {
  it("parses bounded and open-ended ranges", () => {
    expect(parseByteRange("bytes=10-19", 100)).toEqual({ start: 10, end: 19 });
    expect(parseByteRange("bytes=90-", 100)).toEqual({ start: 90, end: 99 });
  });

  it("parses suffix ranges", () => {
    expect(parseByteRange("bytes=-10", 100)).toEqual({ start: 90, end: 99 });
    expect(parseByteRange("bytes=-200", 100)).toEqual({ start: 0, end: 99 });
  });

  it("rejects invalid and multiple ranges", () => {
    expect(parseByteRange("bytes=100-120", 100)).toBe("invalid");
    expect(parseByteRange("bytes=20-10", 100)).toBe("invalid");
    expect(parseByteRange("bytes=0-1,4-5", 100)).toBe("invalid");
  });
});
