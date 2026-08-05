import { describe, expect, it } from "vitest";

import { completeJsonFragment } from "./partial-json";

/**
 * A stream is cut at an arbitrary byte, so the only honest test is every byte.
 * These walk each prefix of a realistic payload and assert two things that must
 * hold at all of them: the result parses, and it never claims data the prefix
 * did not contain.
 */

const PAYLOAD = JSON.stringify({
  version: "v0.9",
  updateComponents: {
    surfaceId: "s",
    components: [
      { id: "root", component: "Stack", children: ["a", "b"] },
      { id: "a", component: "TextContent", text: "半导体反弹", size: "large-heavy" },
      { id: "b", component: "MiniCard", label: "NASDAQ", value: 26584.99, trend: "up" },
    ],
  },
});

describe("completeJsonFragment", () => {
  it("parses at every prefix", () => {
    const failures: string[] = [];
    for (let i = 1; i <= PAYLOAD.length; i += 1) {
      const completed = completeJsonFragment(PAYLOAD.slice(0, i));
      if (completed === null) continue; // nothing salvageable yet is fine
      try {
        JSON.parse(completed);
      } catch {
        failures.push(`prefix ${i}: ${completed.slice(-60)}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("never invents text that had not arrived", () => {
    // The guarantee that makes partial rendering safe to show a user: whatever
    // is on screen was actually sent.
    for (let i = 1; i <= PAYLOAD.length; i += 1) {
      const completed = completeJsonFragment(PAYLOAD.slice(0, i));
      if (!completed) continue;
      const text = JSON.stringify(JSON.parse(completed));
      if (text.includes("半导体反弹")) {
        expect(PAYLOAD.slice(0, i)).toContain("半导体反弹");
      }
      if (text.includes("26584.99")) {
        expect(PAYLOAD.slice(0, i)).toContain("26584.99");
      }
    }
  });

  it("grows a string value as it arrives", () => {
    const at = PAYLOAD.indexOf("半导体反弹");
    const partial = completeJsonFragment(PAYLOAD.slice(0, at + 3));
    expect(partial).not.toBeNull();
    expect(JSON.stringify(JSON.parse(partial as string))).toContain("半导");
  });

  it("returns a complete document unchanged", () => {
    expect(completeJsonFragment(PAYLOAD)).toBe(PAYLOAD);
  });

  it("drops a key that has no value yet", () => {
    const parsed = JSON.parse(completeJsonFragment('{"a":1,"b') as string);
    expect(parsed).toEqual({ a: 1 });
  });

  it("drops a dangling colon", () => {
    const parsed = JSON.parse(completeJsonFragment('{"a":1,"b":') as string);
    expect(parsed).toEqual({ a: 1 });
  });

  it("drops a half-written number rather than rounding it", () => {
    // "265" is a prefix of 26584.99, not a smaller reading of it. Showing it
    // would be worse than showing nothing.
    const parsed = JSON.parse(completeJsonFragment('{"a":1,"v":265') as string);
    expect(parsed).toEqual({ a: 1 });
  });

  it("keeps a brace that lives inside a string", () => {
    const parsed = JSON.parse(completeJsonFragment('{"t":"a { b') as string);
    expect(parsed).toEqual({ t: "a { b" });
  });

  it("does not let a trailing backslash escape the closing quote", () => {
    const completed = completeJsonFragment('{"t":"a\\\\');
    expect(completed).not.toBeNull();
    expect(() => JSON.parse(completed as string)).not.toThrow();
  });

  it("has nothing to offer for an empty or opening fragment", () => {
    expect(completeJsonFragment("")).toBeNull();
    expect(completeJsonFragment("   ")).toBeNull();
  });
});
