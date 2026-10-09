export declare const MANIFEST_FILE: "valuz-plugin.json";
export declare const DEFAULT_FRONTEND_ENTRY: "frontend/index.js";
export declare const DEFAULT_LOCALES_DIR: "locales";
export declare const PLUGIN_API_VERSION: string;
/** Absolute path of the canonical manifest schema shipped with the SDK. */
export declare const SCHEMA_PATH: string;
/** Absolute path of the SDK package root. */
export declare const SDK_ROOT: string;
export type PluginManifest = Record<string, unknown> & {
  id?: string;
  version?: string;
  frontend?: { entry?: string; styles?: string[] };
};
export declare function loadSchema(): Record<string, unknown>;
export declare function reservedPrefixes(): string[];
export declare class ManifestError extends Error {}
export declare function readManifest(dir: string): PluginManifest;
export declare function writeManifest(dir: string, manifest: PluginManifest): void;
export declare function hasDotDot(rel: string): boolean;
export declare function cleanRel(rel: string): string;
export declare function frontendEntry(manifest: PluginManifest): string;
export declare function localesDir(manifest: PluginManifest): string;
export declare function localizedText(value: unknown): string;
