export * from "./profile";
export * from "./capabilities";
export * from "./personal-profile";
export * from "./registries";
export * from "./resolve";
export * from "./branding";
export { hydrateOverlayIfPresent } from "./hydrate-overlay";

import type { EditionProfile } from "./profile";
import { getActiveProfile } from "./resolve";

/**
 * Build-time activeProfile snapshot. Prefer `useRegistryStore` for
 * reactive consumption; this constant is fine for non-reactive callers
 * that only need the seed value (tests, one-shot CLI bootstraps).
 */
export const activeProfile: EditionProfile = getActiveProfile();

export * from "./registry-store";
export * from "./plugin";
