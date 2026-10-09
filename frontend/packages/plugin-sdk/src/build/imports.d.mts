export interface BundleImport {
  specifier: string;
  kind: "import-statement" | "dynamic-import" | "require-call";
}
/** Every import record of an ES module (throws on a syntax error). */
export declare function listBundleImports(code: string): BundleImport[];
/** Disallowed import specifiers of a built plugin bundle; empty = OK. */
export declare function checkBundleImports(code: string): string[];
/** Export names of an ES module. */
export declare function bundleExports(code: string): string[];
