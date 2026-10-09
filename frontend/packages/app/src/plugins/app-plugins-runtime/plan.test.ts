import type { AppPlugin } from "@valuz/core";
import { describe, expect, it } from "vitest";

import { planAppPluginChanges, wantsLoad, type LoadedPluginRecord } from "./plan";

const item = (id: string, patch: Partial<AppPlugin> = {}): AppPlugin => ({
  id,
  version: "1.0.0",
  name: id,
  publisher: { name: "Acme" },
  source: { kind: "file" },
  status: "enabled",
  status_reason: null,
  enabled: true,
  permissions: [],
  requires: [],
  unmet_requires: [],
  engines: {},
  entry_url: `/v1/app-plugin-assets/${id}/1/frontend/index.js`,
  style_urls: [],
  locales: {},
  config_schema: null,
  automations: [],
  revision: 1,
  dev_path: null,
  sha256: "abc",
  installed_at: 1,
  ...patch,
});

const loaded = (...records: Array<Partial<LoadedPluginRecord> & { id: string }>) =>
  new Map(
    records.map((record) => [
      record.id,
      { version: "1.0.0", revision: 1, sha256: "abc", ...record },
    ]),
  ) as Map<string, LoadedPluginRecord>;

describe("wantsLoad", () => {
  it("needs enabled + status enabled + an entry", () => {
    expect(wantsLoad(item("a.b"))).toBe(true);
    expect(wantsLoad(item("a.b", { enabled: false }))).toBe(false);
    expect(wantsLoad(item("a.b", { status: "disabled" }))).toBe(false);
    expect(wantsLoad(item("a.b", { status: "requires-unmet" }))).toBe(false);
    expect(wantsLoad(item("a.b", { status: "incompatible" }))).toBe(false);
    expect(wantsLoad(item("a.b", { entry_url: null }))).toBe(false);
  });
});

describe("planAppPluginChanges", () => {
  it("loads new and enabled plugins, in list order", () => {
    const changes = planAppPluginChanges(loaded(), {
      safe_mode: false,
      plugins: [item("a.one"), item("a.two", { enabled: false, status: "disabled" }), item("a.three")],
    });
    expect(changes.load.map((p) => p.id)).toEqual(["a.one", "a.three"]);
    expect(changes.skipped).toEqual([{ id: "a.two", reason: "disabled" }]);
    expect(changes.unload).toEqual([]);
    expect(changes.reload).toEqual([]);
  });

  it("unloads plugins that were removed, disabled or went unmet", () => {
    const changes = planAppPluginChanges(loaded({ id: "a.one" }, { id: "a.two" }, { id: "a.three" }), {
      safe_mode: false,
      plugins: [
        item("a.two", { enabled: false, status: "disabled" }),
        item("a.three", { status: "requires-unmet" }),
      ],
    });
    expect(changes.unload).toEqual(["a.one", "a.two", "a.three"]);
    expect(changes.skipped).toEqual([
      { id: "a.two", reason: "disabled" },
      { id: "a.three", reason: "requires-unmet" },
    ]);
  });

  it("reloads a plugin whose revision, version or sha256 changed, and leaves the rest", () => {
    const changes = planAppPluginChanges(
      loaded({ id: "a.rev" }, { id: "a.ver" }, { id: "a.sha" }, { id: "a.same" }),
      {
        safe_mode: false,
        plugins: [
          item("a.rev", { revision: 2 }),
          item("a.ver", { version: "1.1.0" }),
          item("a.sha", { sha256: "def" }),
          item("a.same"),
        ],
      },
    );
    expect(changes.reload.map((p) => p.id)).toEqual(["a.rev", "a.ver", "a.sha"]);
    expect(changes.load).toEqual([]);
    expect(changes.unload).toEqual([]);
  });

  it("loads nothing and unloads everything in safe mode", () => {
    const changes = planAppPluginChanges(loaded({ id: "a.one" }), {
      safe_mode: true,
      plugins: [item("a.one"), item("a.two")],
    });
    expect(changes.unload).toEqual(["a.one"]);
    expect(changes.load).toEqual([]);
    expect(changes.skipped).toEqual([
      { id: "a.one", reason: "safe-mode" },
      { id: "a.two", reason: "safe-mode" },
    ]);
  });
});
