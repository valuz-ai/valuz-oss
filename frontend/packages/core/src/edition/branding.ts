import type { BrandingProfile } from "./profile";
import { useRegistryStore } from "./registry-store";

export type { BrandingProfile };

export const defaultBranding: BrandingProfile = {
  appName: "Valuz Agent",
};

export function useBranding(): BrandingProfile {
  return useRegistryStore((state) => state.branding);
}

const CSS_MAP: Record<string, keyof BrandingProfile> = {
  "--color-brand": "brandColor",
  "--color-brand-hover": "brandColorHover",
  "--color-brand-light": "brandColorLight",
};

export function applyBrandColors(branding: BrandingProfile): void {
  for (const [cssVar, key] of Object.entries(CSS_MAP)) {
    const value = branding[key];
    if (value) {
      document.documentElement.style.setProperty(cssVar, value);
    }
  }
}
