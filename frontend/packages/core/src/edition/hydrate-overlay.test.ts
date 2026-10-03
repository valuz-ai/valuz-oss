import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * ``hydrateOverlayIfPresent`` must tell "no overlay in this build" apart from
 * "the configured overlay failed to load". The first is the personal edition
 * and stays a silent no-op; the second used to be swallowed by the same
 * ``catch``, so a broken commercial overlay booted the app as the personal
 * edition with nothing logged anywhere.
 */

const failingOverlay = () => {
  throw new Error("overlay renderer exploded");
};

afterEach(() => {
  vi.doUnmock("virtual:edition-overlay");
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function loadFresh() {
  vi.resetModules();
  const hydrate = await import("./hydrate-overlay");
  const { useRegistryStore } = await import("./registry-store");
  return { ...hydrate, useRegistryStore };
}

describe("hydrateOverlayIfPresent", () => {
  it("is a no-op when the build configures no overlay", async () => {
    const { hydrateOverlayIfPresent, useRegistryStore } = await loadFresh();
    const before = useRegistryStore.getState().desktopRoutes;
    await expect(hydrateOverlayIfPresent()).resolves.toBeUndefined();
    expect(useRegistryStore.getState().desktopRoutes).toBe(before);
  });

  it("stays a no-op when the virtual module is unavailable and no overlay is configured", async () => {
    vi.doMock("virtual:edition-overlay", failingOverlay);
    const { hydrateOverlayIfPresent } = await loadFresh();
    await expect(hydrateOverlayIfPresent()).resolves.toBeUndefined();
  });

  it("rejects with EditionOverlayLoadError when the configured overlay fails to load", async () => {
    vi.stubGlobal("__VALUZ_EDITION_OVERLAY__", "@valuz/commercial");
    vi.doMock("virtual:edition-overlay", failingOverlay);
    const { hydrateOverlayIfPresent, EditionOverlayLoadError } =
      await loadFresh();

    const outcome = hydrateOverlayIfPresent();
    await expect(outcome).rejects.toBeInstanceOf(EditionOverlayLoadError);
    await expect(outcome).rejects.toMatchObject({
      overlayPackage: "@valuz/commercial",
      message: expect.stringContaining("@valuz/commercial"),
    });
  });

  it("hydrates the registry from a configured overlay that loads", async () => {
    vi.stubGlobal("__VALUZ_EDITION_OVERLAY__", "@valuz/commercial");
    const { personalProfile } = await import("./personal-profile");
    vi.doMock("virtual:edition-overlay", () => ({
      overlayProfile: {
        ...personalProfile,
        desktopRoutes: [
          ...personalProfile.desktopRoutes,
          {
            ...personalProfile.desktopRoutes[0],
            id: "overlay-route",
            path: "/overlay",
          },
        ],
      },
    }));
    const { hydrateOverlayIfPresent, useRegistryStore } = await loadFresh();
    await hydrateOverlayIfPresent();
    expect(
      useRegistryStore.getState().desktopRoutes.map((r) => r.id),
    ).toContain("overlay-route");
  });
});
