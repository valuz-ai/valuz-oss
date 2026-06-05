import { t } from "@valuz/shared/i18n";
import { app, dialog, ipcMain, shell } from "electron";
import { spawn } from "node:child_process";
import { readFile, copyFile, mkdir, unlink } from "node:fs/promises";
import { join, dirname } from "node:path";
import { getMainWindow } from "../windows";
import { openExternalIfSafe } from "../security";
import { desktopRuntime } from "./desktop";
import { serviceHandlers } from "./services";
import {
  getCliStatus,
  launchTerminalWithCommand,
  type CliTool,
} from "./cli-login";
import {
  getCliInstallStatus,
  installCliToPath,
  uninstallCliFromPath,
} from "./install-cli";

export const registerIpcHandlers = () => {
  const handlers = serviceHandlers(desktopRuntime);

  for (const [channel, handler] of Object.entries(handlers)) {
    ipcMain.handle(channel, handler);
  }

  ipcMain.handle("select_directory", async () => {
    const win = getMainWindow();
    const opts: Electron.OpenDialogOptions = {
      properties: ["openDirectory", "createDirectory"],
    };
    const result = win
      ? await dialog.showOpenDialog(win, opts)
      : await dialog.showOpenDialog(opts);
    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true, path: null };
    }
    return { canceled: false, path: result.filePaths[0] };
  });

  ipcMain.handle("open_in_finder", async (_event, args: { path: string }) => {
    if (args?.path) {
      await shell.openPath(args.path);
    }
  });

  ipcMain.handle(
    "open_external_url",
    async (_event, args: { url?: string }) => {
      if (!args?.url) {
        return false;
      }
      return openExternalIfSafe(args.url);
    },
  );

  ipcMain.handle(
    "read_file_content",
    async (_event, args: { path: string }) => {
      if (!args?.path) return { content: null };
      try {
        const buf = await readFile(args.path);
        if (buf.length > 100 * 1024) {
          return {
            content:
              buf.subarray(0, 100 * 1024).toString("utf-8") +
              "\n… (file too large, truncated)",
          };
        }
        return { content: buf.toString("utf-8") };
      } catch {
        return { content: null };
      }
    },
  );

  ipcMain.handle(
    "copy_files",
    async (_event, args: { sources: string[]; destDir: string }) => {
      if (!args?.sources?.length || !args?.destDir) {
        return { copied: 0, errors: [] };
      }
      let copied = 0;
      const errors: string[] = [];
      for (const src of args.sources) {
        try {
          const dest = join(args.destDir, src.split(/[/\\]/).pop()!);
          await mkdir(dirname(dest), { recursive: true });
          await copyFile(src, dest);
          copied++;
        } catch (err) {
          errors.push(
            `${src}: ${err instanceof Error ? err.message : t("common.copy" as Parameters<typeof t>[0]) + " " + t("common.failed" as Parameters<typeof t>[0])}`,
          );
        }
      }
      return { copied, errors };
    },
  );

  ipcMain.handle("delete_file", async (_event, args: { path: string }) => {
    if (!args?.path) return { success: false, error: "No path" };
    try {
      await unlink(args.path);
      return { success: true };
    } catch (err) {
      return {
        success: false,
        error:
          err instanceof Error
            ? err.message
            : t("common.deleteFailed" as Parameters<typeof t>[0]),
      };
    }
  });

  // Brand-logo dropdown's "关闭" item — quits the entire client and
  // every spawned sidecar. ``app.quit()`` triggers ``before-quit``,
  // which already calls ``desktopRuntime.stopAllServices()`` (see
  // ``main/index.ts``), so background processes (agent server, etc.)
  // get torn down before the Electron process exits. Renderer cannot
  // call ``app.quit()`` directly from sandbox.
  ipcMain.handle("app_quit", async () => {
    app.quit();
  });

  // Brand-logo dropdown's "新窗口" item — spawns a brand-new client
  // INSTANCE (separate Electron process), not just another
  // BrowserWindow inside this one. Each instance has its own main
  // process, its own sidecar services, its own IPC bus.
  //
  // ``process.execPath`` is the right binary in both modes:
  //   - dev: the locally-installed Electron binary; we replay
  //     ``process.argv.slice(1)`` so it loads the same renderer +
  //     preload as the parent dev process.
  //   - packaged: the app bundle's executable; no extra args needed,
  //     the bundle knows which renderer to load.
  // ``detached: true`` + ``unref()`` lets the child outlive the
  // parent if the parent exits first; ``stdio: 'ignore'`` keeps the
  // child's logs from spamming the parent's terminal.
  ipcMain.handle("window_open_new", async () => {
    const args = app.isPackaged ? [] : process.argv.slice(1);
    const child = spawn(process.execPath, args, {
      detached: true,
      stdio: "ignore",
    });
    child.unref();
  });

  ipcMain.handle(
    "cli_login_status",
    async (_event, args: { tool?: CliTool }) => {
      if (args?.tool !== "claude" && args?.tool !== "codex") {
        return { installed: false, state: "unsupported", cliPath: null };
      }
      return getCliStatus(args.tool);
    },
  );

  ipcMain.handle(
    "cli_login_launch",
    async (_event, args: { tool?: CliTool }) => {
      if (args?.tool !== "claude" && args?.tool !== "codex") {
        return { launched: false, error: "invalid_tool" };
      }
      return launchTerminalWithCommand(args.tool);
    },
  );

  ipcMain.handle("cli_install_status", async () => {
    return getCliInstallStatus();
  });

  ipcMain.handle("cli_install_to_path", async () => {
    return installCliToPath();
  });

  ipcMain.handle("cli_uninstall_from_path", async () => {
    return uninstallCliFromPath();
  });
};
