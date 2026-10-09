import {
  createPluginHost,
  personalProfile,
  useRegistryStore,
} from "@valuz/core";
import { describe, expect, it } from "vitest";

import { loadOssPlugins } from "./boot";
import {
  captureOssComposition,
  undeclaredOssSlots,
} from "./testing/composition-snapshot";

/**
 * Pins what the bare personal edition (no overlay) puts in the shared
 * registries: routes, nav, settings sections, project panels, services,
 * capabilities and slots, in order. See testing/composition-snapshot.ts.
 *
 * The snapshot was recorded BEFORE the OSS features became plugins and has not
 * changed since: moving a feature into a plugin changes how it is registered,
 * never what the user sees.
 */
describe("OSS bare registry composition", () => {
  it("registers the personal edition's routes, nav, settings, panels and services", async () => {
    const store = useRegistryStore.getState();
    store.clearLayers();
    store.hydrate(personalProfile);
    await loadOssPlugins(createPluginHost(), { inactive: [] });

    expect(captureOssComposition()).toMatchSnapshot();
    expect(undeclaredOssSlots()).toEqual([]);
  });
});
