import type { PluginManifest } from "./manifest.mjs";

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  manifest: PluginManifest | null;
}
export declare function manifestPaths(manifest: PluginManifest): { field: string; value: string }[];
/** Validate a plugin directory (same shape as the backend's / Go CLI's validate). */
export declare function validatePlugin(dir: string): ValidationResult;
