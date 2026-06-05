import { describe, expect, it } from "vitest";
import { parseDeepLink } from "./deep-link-utils";

describe("parseDeepLink", () => {
  it("parses valuz-oss protocol links", () => {
    const parsed = parseDeepLink("valuz-oss://open/workspace?project=abc");

    expect(parsed).toEqual({
      raw: "valuz-oss://open/workspace?project=abc",
      host: "open",
      pathname: "/workspace",
      search: "?project=abc",
    });
  });

  it("returns null for non valuz-oss links", () => {
    expect(parseDeepLink("https://example.com")).toBeNull();
  });
});
