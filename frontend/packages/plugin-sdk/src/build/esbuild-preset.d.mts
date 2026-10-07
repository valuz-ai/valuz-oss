import type { BuildOptions, Plugin } from "esbuild";

export interface ValuzEsbuildPluginOptions {
  /** Plugin id; CSS is scoped to ``[data-valuz-app-plugin="<pluginId>"]``. */
  pluginId?: string;
  /** Default true. */
  scopeStyles?: boolean;
}
export declare function valuzEsbuildPlugin(options?: ValuzEsbuildPluginOptions): Plugin;

export interface EsbuildPresetOptions {
  pluginId: string;
  /** Source entry (absolute or relative to the working directory). */
  entry: string;
  /** Output JS file, e.g. ``<plugin>/frontend/index.js``. */
  outfile: string;
  minify?: boolean;
  sourcemap?: BuildOptions["sourcemap"];
}
export declare function esbuildPreset(options: EsbuildPresetOptions): BuildOptions;
