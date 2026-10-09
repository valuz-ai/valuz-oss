/** The global the host fills before it imports a plugin. */
export declare const SHARED_GLOBAL: "__VALUZ_PLUGIN_SHARED__";
/** Module ids the host provides to every plugin. */
export declare const SHARED_MODULE_IDS: readonly [
  "react",
  "react/jsx-runtime",
  "react-dom",
  "react-dom/client",
  "@valuz/plugin-sdk",
  "@valuz/plugin-sdk/ui",
];
export type SharedModuleId = (typeof SHARED_MODULE_IDS)[number];
/** id → the runtime expression a bundle reads it from. */
export declare const SHARED_MODULES: Readonly<Record<SharedModuleId, string>>;
export declare function isSharedModule(id: string): id is SharedModuleId;
export declare const SHARED_MODULE_FILTER: RegExp;
/** Why a bare import cannot be used by a plugin, or ``null`` when it may be bundled. */
export declare function forbiddenImportReason(id: string): string | null;
export declare function sharedModuleCommonJs(id: string): string;
export declare const JSX_DEV_RUNTIME_ID: "react/jsx-dev-runtime";
export declare function jsxDevRuntimeCommonJs(): string;
export declare function jsxDevRuntimeEsm(): string;
export declare const SYNTHETIC_EXPORT: "__valuzShared";
export declare function sharedModuleEsm(id: string): string;
