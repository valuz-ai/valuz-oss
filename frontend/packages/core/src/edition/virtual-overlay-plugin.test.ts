import { describe, expect, it } from "vitest";
import { editionOverlayPlugin } from "./virtual-overlay-plugin";

describe("editionOverlayPlugin", () => {
  const VIRTUAL_ID = "virtual:edition-overlay";
  const RESOLVED_ID = "\0virtual:edition-overlay";

  it("resolves virtual:edition-overlay to the resolved ID", () => {
    const plugin = editionOverlayPlugin();
    const resolveId = plugin.resolveId as (id: string) => string | void;
    expect(resolveId(VIRTUAL_ID)).toBe(RESOLVED_ID);
  });

  it("skips unrelated IDs", () => {
    const plugin = editionOverlayPlugin();
    const resolveId = plugin.resolveId as (id: string) => string | void;
    expect(resolveId("something-else")).toBeUndefined();
  });

  it("returns null overlay when VALUZ_OVERLAY_PACKAGE is not set", () => {
    const original = process.env.VALUZ_OVERLAY_PACKAGE;
    delete process.env.VALUZ_OVERLAY_PACKAGE;

    const plugin = editionOverlayPlugin();
    const load = plugin.load as (id: string) => string | void;
    const result = load(RESOLVED_ID);

    expect(result).toBe("export const overlayProfile = null;");

    if (original !== undefined) process.env.VALUZ_OVERLAY_PACKAGE = original;
  });

  it("returns import from overlay package when VALUZ_OVERLAY_PACKAGE is set", () => {
    const original = process.env.VALUZ_OVERLAY_PACKAGE;
    process.env.VALUZ_OVERLAY_PACKAGE = "@valuz/enterprise";

    const plugin = editionOverlayPlugin();
    const load = plugin.load as (id: string) => string | void;
    const result = load(RESOLVED_ID);

    expect(result).toContain(
      `export { overlayProfile } from "@valuz/enterprise/renderer"`,
    );

    if (original !== undefined) process.env.VALUZ_OVERLAY_PACKAGE = original;
    else delete process.env.VALUZ_OVERLAY_PACKAGE;
  });

  it("skips load for unrelated IDs", () => {
    const plugin = editionOverlayPlugin();
    const load = plugin.load as (id: string) => string | void;
    expect(load("some-other-id")).toBeUndefined();
  });
});
