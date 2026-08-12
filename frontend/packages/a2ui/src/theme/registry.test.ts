import { afterEach, describe, expect, it } from "vitest";

import {
  registerA2UIThemeExtension,
  resolveA2UIThemeTokens,
} from "./registry";
import { resetA2UIThemeExtensionsForTests } from "./registry";

describe("A2UI theme extensions", () => {
  afterEach(resetA2UIThemeExtensionsForTests);

  it("resolves parent tokens before child overrides", () => {
    registerA2UIThemeExtension({
      id: "commercial",
      tokens: { light: { "--va2-commercial-accent": "#111111" } },
      overrides: { light: { "--va2-brand": "#222222" } },
    });
    registerA2UIThemeExtension({
      id: "finance",
      extends: ["commercial"],
      tokens: { light: { "--va2-finance-market-up": "#f54b4b" } },
      overrides: { light: { "--va2-brand": "#725cf9" } },
    });

    expect(resolveA2UIThemeTokens("light")).toEqual({
      "--va2-commercial-accent": "#111111",
      "--va2-brand": "#725cf9",
      "--va2-finance-market-up": "#f54b4b",
    });
  });

  it("rejects unnamespaced additions and missing parents", () => {
    expect(() => registerA2UIThemeExtension({
      id: "finance",
      tokens: { light: { "--va2-market-up": "red" } },
    })).toThrow(/namespace new token/);

    registerA2UIThemeExtension({ id: "finance", extends: ["commercial"] });
    expect(() => resolveA2UIThemeTokens("light")).toThrow(/missing theme/);
  });

  it("rejects cyclic extension inheritance", () => {
    registerA2UIThemeExtension({ id: "commercial", extends: ["finance"] });
    registerA2UIThemeExtension({ id: "finance", extends: ["commercial"] });
    expect(() => resolveA2UIThemeTokens("dark")).toThrow(/cycle/);
  });

  it("keeps a replacement when an older disposer runs", () => {
    const disposeOld = registerA2UIThemeExtension({ id: "finance" });
    registerA2UIThemeExtension({
      id: "finance",
      tokens: { light: { "--va2-finance-market-up": "#f54b4b" } },
    });
    disposeOld();
    expect(resolveA2UIThemeTokens("light")).toEqual({
      "--va2-finance-market-up": "#f54b4b",
    });
  });
});
