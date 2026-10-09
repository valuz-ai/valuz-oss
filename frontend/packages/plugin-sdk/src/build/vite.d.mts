/**
 * A Vite / Rollup plugin object (structural, so the SDK does not depend on
 * Vite's types). Hooks are typed loosely to stay assignable to ``vite.Plugin``.
 */
export interface ValuzVitePlugin {
  name: string;
  enforce?: "pre" | "post";
  apply?: "build" | "serve";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  config?: (this: any, ...args: any[]) => any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  resolveId?: (this: any, ...args: any[]) => any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  load?: (this: any, ...args: any[]) => any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  transform?: (this: any, ...args: any[]) => any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  generateBundle?: (this: any, ...args: any[]) => any;
}
export interface ValuzVitePluginOptions {
  /** Plugin directory (default: Vite's root). */
  root?: string;
  /** Default: the manifest id. */
  pluginId?: string;
  /** Source entry (default: as ``valuz-plugin build``). */
  entry?: string;
  /** Default true. */
  scopeStyles?: boolean;
}
export declare function valuzPlugin(options?: ValuzVitePluginOptions): ValuzVitePlugin[];
