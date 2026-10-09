import type { BuildOptions } from "esbuild";

export interface BuildOutput {
  /** Absolute path. */
  path: string;
  /** Path relative to the plugin directory (slash separated). */
  file: string;
  bytes: number;
}
export interface BuildReport {
  ok: boolean;
  pluginId: string;
  /** Source entry, relative to the plugin directory. */
  source: string;
  outputs: BuildOutput[];
  errors: string[];
  warnings: string[];
  /** ``frontend.styles`` was updated to list the produced CSS. */
  manifestUpdated: boolean;
}
export interface BuildPluginOptions {
  watch?: boolean;
  minify?: boolean;
  /** Default: inline in watch mode, none otherwise. */
  sourcemap?: BuildOptions["sourcemap"];
  /** Called after every build (watch mode: every rebuild). */
  onBuild?: (report: BuildReport) => void;
}
export interface WatchHandle {
  /** The first build's report. */
  report: BuildReport;
  dispose(): Promise<void>;
}
export declare function resolveSourceEntry(dir: string): string;
export declare function buildPlugin(
  dir: string,
  options: BuildPluginOptions & { watch: true },
): Promise<WatchHandle>;
export declare function buildPlugin(
  dir: string,
  options?: BuildPluginOptions & { watch?: false },
): Promise<BuildReport>;
