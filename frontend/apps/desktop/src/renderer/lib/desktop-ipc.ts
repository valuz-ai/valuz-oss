type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

export const isElectron = (): boolean => getBridge() !== null;

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
export const revealInFinder = async (path: string): Promise<void> => {
  const bridge = getBridge();
  if (!bridge || !path) return;
  await bridge.invoke("open_in_finder", { path });
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
