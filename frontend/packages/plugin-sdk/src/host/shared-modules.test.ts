import * as React from "react";
import { describe, expect, it } from "vitest";

import { definePlugin, SHARED_MODULE_IDS } from "../index";
import * as ui from "../ui";
import { createSharedModuleTable, installSharedModules, SHARED_MODULES_GLOBAL } from "./shared-modules";

describe("installSharedModules", () => {
  it("publishes the six shared modules on globalThis.__VALUZ_PLUGIN_SHARED__", () => {
    const target: Record<string, unknown> = {};
    installSharedModules(target);

    const table = target[SHARED_MODULES_GLOBAL] as Record<string, Record<string, unknown>>;
    expect(SHARED_MODULES_GLOBAL).toBe("__VALUZ_PLUGIN_SHARED__");
    expect(Object.keys(table).sort()).toEqual([...SHARED_MODULE_IDS].sort());
    expect(SHARED_MODULE_IDS).toHaveLength(6);
  });

  it("hands out the host's own React and SDK instance", () => {
    const table = createSharedModuleTable() as Record<string, Record<string, unknown>>;
    expect(table.react!.useState).toBe(React.useState);
    expect(table["react/jsx-runtime"]!.jsx).toBeTypeOf("function");
    expect(table["react-dom"]).toBeDefined();
    expect(table["react-dom/client"]!.createRoot).toBeTypeOf("function");
    expect(table["@valuz/plugin-sdk"]!.definePlugin).toBe(definePlugin);
    expect(table["@valuz/plugin-sdk/ui"]!.Button).toBe(ui.Button);
  });

  it("defaults to globalThis", () => {
    installSharedModules();
    expect((globalThis as Record<string, unknown>)[SHARED_MODULES_GLOBAL]).toBeDefined();
    delete (globalThis as Record<string, unknown>)[SHARED_MODULES_GLOBAL];
  });
});

describe("@valuz/plugin-sdk/ui", () => {
  it("exports the doc 12 §4 subset", () => {
    for (const name of [
      "Button", "Input", "Textarea", "Select", "Checkbox", "Switch", "Tabs", "Table", "Card",
      "Badge", "Dialog", "Popover", "Tooltip", "DropdownMenu", "Skeleton", "Spinner",
      "LoadingState", "Empty", "ChartContainer", "tokens",
    ]) {
      expect((ui as Record<string, unknown>)[name], name).toBeDefined();
    }
    expect(ui.tokens.colorPrimary).toBe("var(--color-primary)");
    expect(ui.tokenVariables).toContain("--color-primary");
  });
});
