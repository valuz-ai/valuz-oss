import { describe, expect, it } from "vitest";
import { ApiError } from "@valuz/core";
import {
  buildConfigPayload,
  formatBytes,
  initialConfigDraft,
  isUnavailableError,
  localizedText,
  parseConfigSchema,
  serverErrorList,
  sourceLocation,
  validateConfigDraft,
} from "./app-plugin-helpers";

const SCHEMA = {
  type: "object",
  required: ["token"],
  properties: {
    token: { type: "string", title: { "zh-CN": "令牌", "en-US": "Token" } },
    retries: { type: "integer", default: 3 },
    ratio: { type: "number" },
    verbose: { type: "boolean", default: true },
    region: { type: "string", enum: ["cn", "hk"] },
    tags: { type: "array", items: { type: "string" } },
    nested: { type: "object", properties: {} },
    matrix: { type: "array", items: { type: "object" } },
  },
};

describe("localizedText", () => {
  it("prefers the exact locale, then the language, then English / Chinese", () => {
    const name = { "zh-CN": "看板", "en-US": "Dashboard" };
    expect(localizedText(name, "zh-CN")).toBe("看板");
    expect(localizedText(name, "en-US")).toBe("Dashboard");
    expect(localizedText({ "zh-TW": "看板", "en-US": "Dash" }, "zh-CN")).toBe(
      "看板",
    );
    expect(localizedText({ "fr-FR": "Tableau" }, "en-US")).toBe("Tableau");
    expect(localizedText("plain", "zh-CN")).toBe("plain");
    expect(localizedText(null, "zh-CN")).toBe("");
  });
});

describe("parseConfigSchema", () => {
  it("keeps the editable fields in order and skips the rest", () => {
    const fields = parseConfigSchema(SCHEMA, "zh-CN");
    expect(fields.map((f) => [f.key, f.kind])).toEqual([
      ["token", "string"],
      ["retries", "integer"],
      ["ratio", "number"],
      ["verbose", "boolean"],
      ["region", "enum"],
      ["tags", "string-list"],
    ]);
    expect(fields[0]).toMatchObject({ label: "令牌", required: true });
    expect(fields[1].defaultValue).toBe(3);
    expect(parseConfigSchema(null, "zh-CN")).toEqual([]);
  });
});

describe("config draft", () => {
  const fields = parseConfigSchema(SCHEMA, "en-US");

  it("starts from the stored value, then the schema default", () => {
    const draft = initialConfigDraft(fields, { token: "abc", tags: ["a", "b"] });
    expect(draft).toMatchObject({
      token: "abc",
      retries: "3",
      verbose: true,
      region: "",
      tags: "a\nb",
    });
  });

  it("validates required, numbers and integers", () => {
    const draft = initialConfigDraft(fields, {});
    expect(validateConfigDraft(fields, draft)).toEqual({ token: "required" });
    expect(
      validateConfigDraft(fields, {
        ...draft,
        token: "x",
        ratio: "abc",
        retries: "2.5",
      }),
    ).toEqual({ ratio: "invalidNumber", retries: "invalidInteger" });
    expect(validateConfigDraft(fields, { ...draft, token: "x" })).toEqual({});
  });

  it("builds the payload: typed values, dropped blanks, untouched extras", () => {
    const stored = { nested: { keep: true }, token: "old" };
    const draft = {
      ...initialConfigDraft(fields, stored),
      token: "new",
      retries: "5",
      ratio: "0.5",
      verbose: false,
      region: "hk",
      tags: " a \n\nb ",
    };
    expect(buildConfigPayload(fields, draft, stored)).toEqual({
      nested: { keep: true },
      token: "new",
      retries: 5,
      ratio: 0.5,
      verbose: false,
      region: "hk",
      tags: ["a", "b"],
    });
    // A cleared optional field is dropped, not sent empty.
    const cleared = { ...draft, ratio: "", tags: "", region: "" };
    const payload = buildConfigPayload(fields, cleared, stored);
    expect(payload).not.toHaveProperty("ratio");
    expect(payload).not.toHaveProperty("tags");
    expect(payload).not.toHaveProperty("region");
  });
});

describe("errors", () => {
  it("reads the validation list out of an ApiError body", () => {
    const manifest = new ApiError(
      "invalid manifest",
      400,
      JSON.stringify({ detail: { message: "x", errors: ["id missing", "bad"] } }),
    );
    expect(serverErrorList(manifest)).toEqual(["id missing", "bad"]);
    const fastapi = new ApiError(
      "x",
      422,
      JSON.stringify({
        detail: [{ loc: ["body", "values", "token"], msg: "required" }],
      }),
    );
    expect(serverErrorList(fastapi)).toEqual(["values.token: required"]);
    expect(serverErrorList(new Error("plain"))).toEqual([]);
    expect(serverErrorList(new ApiError("x", 400, "not json"))).toEqual([]);
  });

  it("treats 403 / 404 as unavailable", () => {
    expect(isUnavailableError(new ApiError("x", 403))).toBe(true);
    expect(isUnavailableError(new ApiError("x", 404))).toBe(true);
    expect(isUnavailableError(new ApiError("x", 500))).toBe(false);
    expect(isUnavailableError(new Error("offline"))).toBe(false);
  });
});

describe("misc", () => {
  it("formats sizes and source locations", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(null)).toBe("");
    expect(sourceLocation({ kind: "dev", path: "/a" }, "/b")).toBe("/b");
    expect(sourceLocation({ kind: "url", url: "https://x/y.zip" })).toBe(
      "https://x/y.zip",
    );
    expect(sourceLocation({ kind: "catalog", scope: "org" })).toBeNull();
  });
});
