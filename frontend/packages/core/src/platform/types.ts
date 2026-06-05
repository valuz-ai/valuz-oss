/**
 * Platform capability surface — abstracts Electron-only features
 * (file picker, reveal-in-finder, CLI login, …) so pages work
 * in both desktop and webui.
 *
 * Desktop provides via `ElectronPlatformProvider`.
 * Webui provides via `WebPlatformProvider` (no-ops).
 */

export interface PlatformCapabilities {
  selectDirectory: () => Promise<string | null>;
  copyFiles: (
    sources: string[],
    destDir: string,
  ) => Promise<{ copied: number; errors: string[] }>;
  deleteFile: (path: string) => Promise<{ success: boolean; error?: string }>;
  revealInFinder: (path: string) => Promise<void>;
  quitApp: () => Promise<void>;
  openNewWindow: () => Promise<void>;
  isElectron: boolean;
  checkCliLogin?: (tool: string) => Promise<unknown>;
  launchCliLogin?: (tool: string) => Promise<unknown>;
}
