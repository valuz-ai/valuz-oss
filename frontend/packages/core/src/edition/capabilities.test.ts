import { describe, expect, it } from "vitest";

import { DEFAULT_CAPABILITIES } from "./capabilities";
import { useRegistryStore } from "./registry-store";

describe("capabilities", () => {
  it("leaves runtime setup to the user by default", () => {
    // OSS must not claim the platform provisions channels or the built-in
    // assistant: the conversation banner reads this to decide between "finish
    // setup" and "it hasn't arrived yet", and a personal install owns its setup.
    expect(DEFAULT_CAPABILITIES.managedRuntimeSetup).toBe(false);
    expect(DEFAULT_CAPABILITIES.configureModelChannel).toBe(true);
  });

  it("lets an overlay turn managed setup on without touching the other flags", () => {
    const store = useRegistryStore.getState();
    store.setCapabilities({ managedRuntimeSetup: true });

    const capabilities = useRegistryStore.getState().capabilities;
    expect(capabilities.managedRuntimeSetup).toBe(true);
    expect(capabilities.configureModelChannel).toBe(true);

    store.setCapabilities({ managedRuntimeSetup: false });
  });
});
