import type { EditionProfile } from "./profile";
import { applyBrandColors } from "./branding";
import { useRegistryStore } from "./registry-store";

/**
 * Overlay package this build was configured with — defined by
 * ``editionOverlayPlugin`` from ``VALUZ_OVERLAY_PACKAGE`` ("" when none).
 * Undefined outside a Vite build that uses the plugin (e.g. vitest).
 */
declare const __VALUZ_EDITION_OVERLAY__: string | undefined;

function configuredOverlayPackage(): string {
  return typeof __VALUZ_EDITION_OVERLAY__ === "string"
    ? __VALUZ_EDITION_OVERLAY__
    : "";
}

/**
 * The build was configured with an edition overlay, but importing it threw.
 *
 * Distinct from "no overlay configured" (the personal edition, a silent no-op):
 * hosts decide how to surface it, but must not mistake it for the personal
 * edition.
 */
export class EditionOverlayLoadError extends Error {
  readonly overlayPackage: string;

  constructor(overlayPackage: string, cause: unknown) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    super(`Edition overlay "${overlayPackage}" failed to load: ${reason}`, {
      cause,
    });
    this.name = "EditionOverlayLoadError";
    this.overlayPackage = overlayPackage;
  }
}

/**
 * Import the virtual overlay module and hydrate the registry store if an
 * overlay profile is present. Called once during app bootstrap, before React
 * mounts, so the router and layout see the correct routes from the start.
 *
 * No-op when no overlay package is configured (personal edition). Rejects with
 * {@link EditionOverlayLoadError} when an overlay IS configured but fails to
 * load — the host decides whether to continue on the base profile or stop.
 */
export async function hydrateOverlayIfPresent(): Promise<void> {
  const overlayProfile = await loadOverlayProfile();
  if (overlayProfile) {
    useRegistryStore.getState().hydrate(overlayProfile);
    applyBrandColors(overlayProfile.branding);
  }
}

/**
 * Load the overlay profile from the virtual module injected by the Vite plugin.
 * Returns null when no overlay is configured.
 *
 * Split into its own function so hydrateOverlayIfPresent can be tested without
 * triggering the dynamic import in vitest (which can't resolve virtual modules).
 */
async function loadOverlayProfile(): Promise<EditionProfile | null> {
  try {
    // @ts-expect-error — virtual module resolved by Vite at build time; not present in TS resolution
    const mod = await import(/* @vite-ignore */ "virtual:edition-overlay");
    return (mod.overlayProfile as EditionProfile | null) ?? null;
  } catch (cause) {
    const overlayPackage = configuredOverlayPackage();
    // No overlay configured: the virtual module is simply unavailable (a test
    // runner without the plugin) — the personal edition, nothing to report.
    if (!overlayPackage) return null;
    // An overlay WAS configured and its import threw. Swallowing this used to
    // boot a commercial build as the personal edition with nothing logged.
    throw new EditionOverlayLoadError(overlayPackage, cause);
  }
}
