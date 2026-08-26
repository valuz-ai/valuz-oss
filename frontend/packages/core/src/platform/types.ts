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
  /** Open a path with the OS. Resolves to the OS's complaint when it could
   *  not — empty string on success — so a caller can say WHY nothing opened
   *  instead of leaving the click silent. Hosts that cannot open anything
   *  resolve to "" as well; they have nothing to report. */
  revealInFinder: (path: string) => Promise<string>;
  /**
   * Read a local file's text content (for previewing a ``kind==="local"`` file
   * client-side — the backend never proxies bytes). Large files are truncated.
   * Returns ``{ content: null }`` on error / outside Electron.
   */
  readFileContent?: (
    path: string,
  ) => Promise<{ content: string | null; truncated: boolean }>;
  quitApp: () => Promise<void>;
  /**
   * Full client restart (relaunch + quit). Desktop-only; used after staging
   * a backup restore so the relaunched backend applies it immediately.
   * Absent in webui — callers must feature-detect.
   */
  relaunchApp?: () => Promise<void>;
  openNewWindow: () => Promise<void>;
  isElectron: boolean;
  /** ``true`` on macOS — used to reserve space for the traffic-light cluster. */
  isMac: boolean;
  checkCliLogin?: (tool: string) => Promise<unknown>;
  launchCliLogin?: (tool: string) => Promise<unknown>;
  /** Minimize the window. No-op outside Electron. */
  windowMinimize?: () => Promise<void>;
  /** Toggle maximize/restore. Returns ``true`` if now maximized. */
  windowMaximize?: () => Promise<boolean>;
  /** Close the window. No-op outside Electron. */
  windowClose?: () => Promise<void>;
  /** Query whether the window is currently maximized. */
  windowIsMaximized?: () => Promise<boolean>;
  /** Reload the renderer page. */
  windowReload?: () => Promise<void>;
  /** Toggle Chrome DevTools. */
  windowToggleDevTools?: () => Promise<void>;
  /** Toggle full-screen mode. */
  windowToggleFullscreen?: () => Promise<void>;
  /** Install the CLI to PATH. Returns success/error. */
  cliInstallToPath?: () => Promise<{ success: boolean; error?: string }>;
  /** Uninstall the CLI from PATH. Returns success/error. */
  cliUninstallFromPath?: () => Promise<{ success: boolean; error?: string }>;
  /** Check if CLI is installed. */
  cliInstallStatus?: () => Promise<{ installed: boolean; path?: string }>;
}
