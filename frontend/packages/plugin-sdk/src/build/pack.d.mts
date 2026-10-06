export { PackError, packFileSet } from "./fileset.mjs";

export interface PackResult {
  path: string;
  sha256: string;
  size: number;
  id: string;
  version: string;
  files: string[];
  warnings: string[];
}
export declare function packPlugin(dir: string, options?: { outDir?: string }): PackResult;
