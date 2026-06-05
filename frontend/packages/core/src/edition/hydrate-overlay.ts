import type { EditionProfile } from "./profile";
import { applyBrandColors } from "./branding";
import { useRegistryStore } from "./registry-store";

/**
 * Import the virtual overlay module and hydrate the registry store if an
 * overlay profile is present. Called once during app bootstrap, before React
 * mounts, so the router and layout see the correct routes from the start.
 *
 * No-op when no overlay package is configured (personal edition).
 */
export async function hydrateOverlayIfPresent(): Promise<void> {
  const overlayProfile = await loadOverlayProfile();
  if (overlayProfile) {
    useRegistryStore.getState().hydrate(overlayProfile);
    applyBrandColors(overlayProfile.branding);
  }
}

/**
 * Attempt to load an overlay profile from the virtual module injected by the
 * Vite plugin. Returns null when no overlay is configured.
 *
 * Split into its own function so hydrateOverlayIfPresent can be tested without
 * triggering the dynamic import in vitest (which can't resolve virtual modules).
 */
async function loadOverlayProfile(): Promise<EditionProfile | null> {
  try {
    // @ts-ignore — virtual module resolved by Vite at build time; not present in TS resolution
    const mod = await import(/* @vite-ignore */ "virtual:edition-overlay");
    return (mod.overlayProfile as EditionProfile | null) ?? null;
  } catch {
    return null;
  }
}
