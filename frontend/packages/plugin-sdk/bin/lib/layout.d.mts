/** Root of the SDK this bin/ belongs to (the package, or the distribution). */
export declare const SDK_ROOT: string;
export declare const PACKAGE_NAME: "@valuz/plugin-sdk";
export declare const SCHEMA_FILE: "valuz-plugin.schema.json";
/** Directory of the pre-bundled runtime inside a distribution. */
export declare const RUNTIME_DIR: "runtime";

export interface RuntimeEntry {
  /** The specifier a plugin imports, e.g. ``@valuz/plugin-sdk/testing``. */
  id: string;
  /** File under ``runtime/`` in a distribution. */
  bundle: string;
  /** Package-relative source the bundle is built from. */
  source: string;
}

/** The pre-bundled runtime entries a distribution carries. */
export declare const RUNTIME_ENTRIES: readonly RuntimeEntry[];

export interface SdkLayout {
  kind: "dist" | "source";
  root: string;
  /** Importable specifier → absolute file. */
  modules: Map<string, string>;
}

/** Whether ``root`` is a distribution (carries the pre-bundled runtime). */
export declare function isDistLayout(root?: string): boolean;

/** The SDK layout at ``root`` (default: this SDK). */
export declare function sdkLayout(root?: string): SdkLayout;
