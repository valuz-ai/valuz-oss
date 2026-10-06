import type { Plugin } from "esbuild";

import type { SdkLayout } from "./layout.mjs";

/** test/**\/*.test.* files of the plugin (absolute, sorted). */
export declare function findTestFiles(root: string): string[];

/** The esbuild plugin of ``valuz-plugin test`` for ``layout`` (default: this SDK's). */
export declare function testResolvePlugin(layout?: SdkLayout): Plugin;

export interface BundledTests {
  outDir: string;
  files: Array<{ source: string; bundle: string }>;
  errors: string[];
  warnings: string[];
}

/** Bundle the tests of ``dir`` into a temp directory. */
export declare function bundleTests(dir: string, testFiles: string[]): Promise<BundledTests>;

export interface RunPluginTestsOptions {
  log?: (line: string) => void;
  error?: (line: string) => void;
  nodeArgs?: string[];
  stdio?: "inherit" | "pipe" | "ignore";
  keep?: boolean;
}

/** Run the plugin's tests. Resolves to the exit code (0 = all passed). */
export declare function runPluginTests(dir: string, options?: RunPluginTestsOptions): Promise<number>;
