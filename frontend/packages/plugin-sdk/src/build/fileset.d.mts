import type { PluginManifest } from "./manifest.mjs";
import type { ValidationResult } from "./validate.mjs";

export declare class PackError extends Error {
  constructor(message: string, validation?: ValidationResult | null);
  validation: ValidationResult | null;
}
/** The package files of ``dir`` (sorted slash paths). */
export declare function packFileSet(dir: string, manifest: PluginManifest, exclude?: string | null): string[];
