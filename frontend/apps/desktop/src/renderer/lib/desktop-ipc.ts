type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

export const isElectron = (): boolean => getBridge() !== null;

/**
 * Tell the main process which UI locale is active so the native menu bar
 * (built in the main process, which can't read this renderer's
 * localStorage) matches the in-app language. No-op outside Electron.
 */
export const setMenuLocale = async (locale: string): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("set_menu_locale", { locale });
};

const selectDirectoryViaInput = (): Promise<string | null> =>
  new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.setAttribute("webkitdirectory", "");
    input.setAttribute("directory", "");
    input.style.display = "none";
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (file) {
        const path = (file as File & { path?: string }).path;
        if (path) {
          const sep = path.includes("/") ? "/" : "\\";
          const parts = path.split(sep);
          parts.pop();
          resolve(parts.join(sep));
        } else {
          resolve(file.webkitRelativePath.split("/")[0] || null);
        }
      } else {
        resolve(null);
      }
      input.remove();
    });
    input.addEventListener("cancel", () => {
      resolve(null);
      input.remove();
    });
    document.body.appendChild(input);
    input.click();
  });

export const selectDirectory = async (): Promise<string | null> => {
  const bridge = getBridge();
  if (bridge) {
    const result = await bridge.invoke<{
      canceled: boolean;
      path: string | null;
    }>("select_directory");
    return result.canceled ? null : result.path;
  }
  return selectDirectoryViaInput();
};

export const copyFiles = async (
  sources: string[],
  destDir: string,
): Promise<{ copied: number; errors: string[] }> => {
  const bridge = getBridge();
  if (!bridge) return { copied: 0, errors: ["Not in Electron"] };
  return bridge.invoke<{ copied: number; errors: string[] }>("copy_files", {
    sources,
    destDir,
  });
};

export const deleteFile = async (
  path: string,
): Promise<{ success: boolean; error?: string }> => {
  const bridge = getBridge();
  if (!bridge) return { success: false, error: "Not in Electron" };
  return bridge.invoke<{ success: boolean; error?: string }>("delete_file", {
    path,
  });
};

/**
 * Brand-logo dropdown actions. ``quitApp`` calls Electron's ``app.quit()``
 * via IPC (renderer can't from sandbox); no-op in non-Electron contexts
 * so the menu can stay wired up uniformly.
 */
export const quitApp = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("app_quit");
};

/**
 * Full client restart (relaunch + quit) — Settings → Backup uses this after
 * staging a restore, so the relaunched backend applies it immediately.
 */
export const relaunchApp = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("app_relaunch");
};

/**
 * Spawn a new BrowserWindow beside the main one (same renderer, fresh
 * UI state). No-op outside Electron — the open-source web shell only
 * has one viewport.
 */
export const openNewWindow = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("window_open_new");
};

/**
 * Reveal a file in the host OS file manager (Finder on macOS, Explorer
 * on Windows). Used by the conversation page's per-turn diff card so
 * the user can jump from an Edit/Write summary row to the actual file.
 * No-op outside Electron — webui callers degrade silently.
 */
export const revealInFinder = async (path: string): Promise<string> => {
  const bridge = getBridge();
  if (!bridge || !path) return "";
  return (await bridge.invoke("open_in_finder", { path })) ?? "";
};

/**
 * Read a local file's text content over IPC (main-process ``read_file_content``,
 * large files truncated). Used to preview a ``kind==="local"`` text file without
 * the backend proxying bytes. Returns ``{ content: null }`` outside Electron.
 */
export const readFileContent = async (
  path: string,
): Promise<{ content: string | null; truncated: boolean }> => {
  const bridge = getBridge();
  if (!bridge || !path) return { content: null, truncated: false };
  return (await bridge.invoke("read_file_content", { path })) as {
    content: string | null;
    truncated: boolean;
  };
};

export type CliTool = "claude" | "codex";
export type CliLoginState = "logged_in" | "logged_out" | "unsupported";

export interface CliLoginStatus {
  installed: boolean;
  state: CliLoginState;
  cliPath: string | null;
}

export interface CliLoginLaunchResult {
  launched: boolean;
  error?: string;
}

const UNSUPPORTED_STATUS: CliLoginStatus = {
  installed: false,
  state: "unsupported",
  cliPath: null,
};

export const checkCliLogin = async (tool: CliTool): Promise<CliLoginStatus> => {
  const bridge = getBridge();
  if (!bridge) return UNSUPPORTED_STATUS;
  return bridge.invoke<CliLoginStatus>("cli_login_status", { tool });
};

export const launchCliLogin = async (
  tool: CliTool,
): Promise<CliLoginLaunchResult> => {
  const bridge = getBridge();
  if (!bridge) return { launched: false, error: "unsupported_platform" };
  return bridge.invoke<CliLoginLaunchResult>("cli_login_launch", { tool });
};

/**
 * Window control helpers — minimize, maximize/restore, close, and
 * state query.  Used by the custom WindowControls component in the
 * TopBar on Windows and Linux (macOS keeps native traffic-light buttons).
 */
export const windowMinimize = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("window_minimize");
};

export const windowMaximize = async (): Promise<boolean> => {
  const bridge = getBridge();
  if (!bridge) return false;
  return bridge.invoke<boolean>("window_maximize");
};

export const windowClose = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("window_close");
};

export const windowIsMaximized = async (): Promise<boolean> => {
  const bridge = getBridge();
  if (!bridge) return false;
  return bridge.invoke<boolean>("window_is_maximized");
};

export const windowReload = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("window_reload");
};

export const windowToggleDevTools = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("window_toggle_devtools");
};

export const windowToggleFullscreen = async (): Promise<void> => {
  const bridge = getBridge();
  if (!bridge) return;
  await bridge.invoke("window_toggle_fullscreen");
};

export const cliInstallToPath = async (): Promise<{
  success: boolean;
  error?: string;
}> => {
  const bridge = getBridge();
  if (!bridge) return { success: false, error: "Not in Electron" };
  return bridge.invoke<{ success: boolean; error?: string }>(
    "cli_install_to_path",
  );
};

export const cliUninstallFromPath = async (): Promise<{
  success: boolean;
  error?: string;
}> => {
  const bridge = getBridge();
  if (!bridge) return { success: false, error: "Not in Electron" };
  return bridge.invoke<{ success: boolean; error?: string }>(
    "cli_uninstall_from_path",
  );
};

export const cliInstallStatus = async (): Promise<{
  installed: boolean;
  path?: string;
}> => {
  const bridge = getBridge();
  if (!bridge) return { installed: false };
  return bridge.invoke<{ installed: boolean; path?: string }>(
    "cli_install_status",
  );
};
