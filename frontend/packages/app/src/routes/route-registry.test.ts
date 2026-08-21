import { describe, expect, it } from "vitest";

import { resolvedDesktopRoutes } from "./route-registry";

describe("route registry", () => {
  it("resolves the shared hidden component Gallery route", () => {
    const route = resolvedDesktopRoutes.find(({ id }) => id === "component-gallery");

    expect(route).toMatchObject({
      path: "/developer/components",
      showInNav: false,
    });
    expect(route?.Component).toBeDefined();
  });
});
