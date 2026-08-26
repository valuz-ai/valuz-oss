import { describe, expect, it } from "vitest";

import {
  describeMissingParams,
  isHostParam,
  isStateParam,
  parseDataRef,
  resolveParams,
} from "./dataRef";

describe("resolveParams", () => {
  it("passes literal params through unchanged", () => {
    const result = resolveParams(
      { symbol: "US:NVDA", limit: 8, all: true },
      {},
    );
    expect(result).toEqual({
      ok: true,
      params: { symbol: "US:NVDA", limit: 8, all: true },
    });
  });

  it("resolves $host params from the host render context", () => {
    const result = resolveParams(
      { symbol: { $host: "securityId" } },
      { host: { securityId: "US:NVDA" } },
    );
    expect(result).toEqual({ ok: true, params: { symbol: "US:NVDA" } });
  });

  it("resolves $state params from surface-local state", () => {
    const result = resolveParams(
      { formula: { $state: "screener.formula" } },
      { state: { "screener.formula": "roe > 0.15" } },
    );
    expect(result).toEqual({ ok: true, params: { formula: "roe > 0.15" } });
  });

  it("never guesses a literal for a missing $host param — it reports what's missing instead", () => {
    const result = resolveParams(
      { symbol: { $host: "securityId" }, range: "1Y" },
      { host: {} },
    );
    expect(result).toEqual({
      ok: false,
      missing: [{ key: "symbol", layer: "host", path: "securityId" }],
    });
  });

  it("collapses to ok:false if any single param is missing, even with others resolved", () => {
    const result = resolveParams(
      {
        symbol: { $host: "securityId" },
        formula: { $state: "screener.formula" },
        range: "1Y",
      },
      { host: { securityId: "US:NVDA" }, state: {} },
    );
    expect(result).toEqual({
      ok: false,
      missing: [{ key: "formula", layer: "state", path: "screener.formula" }],
    });
  });

  it("describeMissingParams formats a human-readable reason", () => {
    expect(
      describeMissingParams([
        { key: "symbol", layer: "host", path: "securityId" },
      ]),
    ).toBe('symbol: missing $host "securityId"');
  });
});

describe("isHostParam / isStateParam", () => {
  it("distinguishes the three param layers", () => {
    expect(isHostParam({ $host: "securityId" })).toBe(true);
    expect(isHostParam({ $state: "screener.formula" })).toBe(false);
    expect(isHostParam("US:NVDA")).toBe(false);
    expect(isStateParam({ $state: "screener.formula" })).toBe(true);
    expect(isStateParam("US:NVDA")).toBe(false);
  });
});

describe("parseDataRef", () => {
  it("parses a ref with all three param layers and a refresh declaration", () => {
    const ref = parseDataRef({
      source: "test.source",
      params: {
        symbol: { $host: "securityId" },
        formula: { $state: "screener.formula" },
        range: "1Y",
        limit: 8,
        adjusted: true,
      },
      refresh: { interval: 30 },
    });
    expect(ref).toEqual({
      source: "test.source",
      params: {
        symbol: { $host: "securityId" },
        formula: { $state: "screener.formula" },
        range: "1Y",
        limit: 8,
        adjusted: true,
      },
      refresh: { interval: 30 },
    });
  });

  it("parses a ref with no refresh declaration", () => {
    expect(parseDataRef({ source: "test.source", params: {} })).toEqual({
      source: "test.source",
      params: {},
    });
  });

  it("rejects a missing or empty source", () => {
    expect(parseDataRef({ params: {} })).toBeNull();
    expect(parseDataRef({ source: "", params: {} })).toBeNull();
  });

  it("rejects params that aren't an object", () => {
    expect(parseDataRef({ source: "test.source", params: null })).toBeNull();
    expect(parseDataRef({ source: "test.source", params: [] })).toBeNull();
    expect(parseDataRef({ source: "test.source" })).toBeNull();
  });

  it("rejects a malformed $host/$state entry (extra keys) rather than treating it as a literal object param", () => {
    expect(
      parseDataRef({
        source: "test.source",
        params: { symbol: { $host: "securityId", extra: 1 } },
      }),
    ).toBeNull();
  });

  it("rejects a non-positive or non-finite refresh.interval", () => {
    expect(
      parseDataRef({
        source: "test.source",
        params: {},
        refresh: { interval: 0 },
      }),
    ).toBeNull();
    expect(
      parseDataRef({
        source: "test.source",
        params: {},
        refresh: { interval: -5 },
      }),
    ).toBeNull();
    expect(
      parseDataRef({
        source: "test.source",
        params: {},
        refresh: { interval: Number.NaN },
      }),
    ).toBeNull();
  });

  it("rejects non-object input", () => {
    expect(parseDataRef(null)).toBeNull();
    expect(parseDataRef("finance.market.quote")).toBeNull();
    expect(parseDataRef(42)).toBeNull();
  });
});

describe("parseDataRef — shape (multi-shape source disambiguation)", () => {
  it("carries a valid shape through", () => {
    expect(
      parseDataRef({
        source: "test.kline",
        params: { symbol: "US:NVDA" },
        shape: "ChartData",
      }),
    ).toEqual({
      source: "test.kline",
      params: { symbol: "US:NVDA" },
      shape: "ChartData",
    });
  });

  it("omits the field entirely when absent — not shape:undefined", () => {
    const ref = parseDataRef({ source: "test.quote", params: {} });
    expect(ref).not.toBeNull();
    expect(ref && "shape" in ref).toBe(false);
  });

  it("rejects a non-string or empty shape instead of ignoring it", () => {
    expect(
      parseDataRef({ source: "test.quote", params: {}, shape: 3 }),
    ).toBeNull();
    expect(
      parseDataRef({ source: "test.quote", params: {}, shape: "" }),
    ).toBeNull();
  });

  it("keeps shape alongside refresh", () => {
    expect(
      parseDataRef({
        source: "test.kline",
        params: {},
        shape: "ChartData",
        refresh: { interval: 60 },
      }),
    ).toEqual({
      source: "test.kline",
      params: {},
      refresh: { interval: 60 },
      shape: "ChartData",
    });
  });
});
