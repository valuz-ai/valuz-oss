import { describe, expect, it } from "vitest";

import {
  buildSnapshot,
  classifyOutcome,
  emptyForMissingParams,
  foldToDelta,
  type FetchResult,
} from "./patch";

const PATH = "/data/test.source/abc12345";

describe("classifyOutcome", () => {
  it("classifies a successful non-null result as filled", () => {
    expect(classifyOutcome({ ok: true, data: { price: 100 } })).toEqual({
      state: "filled",
      data: { price: 100 },
    });
  });

  it("classifies a well-formed-but-empty result as empty, with no reason", () => {
    expect(classifyOutcome({ ok: true, data: null })).toEqual({
      state: "empty",
      data: null,
    });
  });

  it("classifies 424 (notConnected) as stale, not error", () => {
    const result: FetchResult<unknown> = {
      ok: false,
      error: "connector_not_connected",
      notConnected: true,
    };
    expect(classifyOutcome(result)).toEqual({
      state: "stale",
      data: null,
      reason: "connector_not_connected",
    });
  });

  it("classifies any other failure as error", () => {
    const result: FetchResult<unknown> = { ok: false, error: "HTTP 500" };
    expect(classifyOutcome(result)).toEqual({
      state: "error",
      data: null,
      reason: "HTTP 500",
    });
  });

  it("carries the last filled value forward through a stale or error transition", () => {
    const filled = classifyOutcome<{ price: number }>({
      ok: true,
      data: { price: 100 },
    });
    const stale = classifyOutcome<{ price: number }>(
      { ok: false, error: "connector_not_connected", notConnected: true },
      filled,
    );
    expect(stale).toEqual({
      state: "stale",
      data: { price: 100 },
      reason: "connector_not_connected",
    });
  });

  it("does not carry forward a value from a non-filled previous state", () => {
    const empty = classifyOutcome<unknown>({ ok: true, data: null });
    const error = classifyOutcome<unknown>(
      { ok: false, error: "HTTP 500" },
      empty,
    );
    expect(error.data).toBeNull();
  });
});

describe("emptyForMissingParams", () => {
  it("is empty, not error or stale, and carries the reason", () => {
    expect(emptyForMissingParams('symbol: missing $host "securityId"')).toEqual(
      {
        state: "empty",
        data: null,
        reason: 'symbol: missing $host "securityId"',
      },
    );
  });
});

describe("foldToDelta", () => {
  it("produces a STATE_DELTA whose patch path is the given slot path", () => {
    const { value, message } = foldToDelta(PATH, {
      ok: true,
      data: { price: 100 },
    });
    expect(value).toEqual({ state: "filled", data: { price: 100 } });
    expect(message).toEqual({
      type: "STATE_DELTA",
      delta: [{ op: "replace", path: PATH, value }],
    });
  });
});

describe("buildSnapshot", () => {
  it("keys every entry's value by its own path", () => {
    const snapshot = buildSnapshot([
      { path: "/data/a/1", value: { state: "filled", data: 1 } },
      { path: "/data/b/2", value: { state: "empty", data: null } },
    ]);
    expect(snapshot).toEqual({
      type: "STATE_SNAPSHOT",
      snapshot: {
        "/data/a/1": { state: "filled", data: 1 },
        "/data/b/2": { state: "empty", data: null },
      },
    });
  });
});
