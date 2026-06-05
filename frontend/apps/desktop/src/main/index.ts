import path from "node:path";
import { Menu, app, ipcMain, nativeImage } from "electron";

// Override the default "Electron" app name shown in macOS menu bar and tray
app.setName("Valuz");
import { setupDeepLinkManager } from "./deep-link";
import { DEEP_LINK_PROTOCOL, parseDeepLink } from "./deep-link-utils";
import { desktopRuntime } from "./ipc/desktop";
import { registerIpcHandlers } from "./ipc";
import { buildAppMenu } from "./menu";
import {
  registerSystemLogIpc,
  startLogTail,
  stopLogTail,
} from "./services/system-logs";
import { createAppTray } from "./tray";
import { scheduleUpdateCheck, setupUpdater } from "./updater";
import { createMainWindow, getMainWindow } from "./windows";
import { DESKTOP_CHANNELS } from "../preload/channels";

let appTray: ReturnType<typeof createAppTray> | null = null;

const bootstrap = async () => {
  // Set dock icon (macOS) — squircle-masked variant so the dock tile renders
  // with macOS-standard rounded corners instead of a hard-edged square.
  if (process.platform === "darwin" && app.dock) {
    const iconPath = path.join(app.getAppPath(), "build", "iconRounded.png");
    app.dock.setIcon(nativeImage.createFromPath(iconPath));
  }

  registerIpcHandlers();
  // Backend log surface: register IPC channels + start tailing the
  // structured JSON file the backend writes (works in dev — where the
  // backend is launched externally — and prod — where Electron spawns
  // the bundled binary). See ``services/system-logs.ts``.
  registerSystemLogIpc();
  startLogTail();
  await createMainWindow();

  const updater = setupUpdater({ getMainWindow });
  // Renderer-driven manual check + restart-to-install. setupUpdater()
  // also wires the periodic auto-check (see scheduleUpdateCheck below);
  // these handlers exist so the UI can drive it on demand.
  ipcMain.handle(DESKTOP_CHANNELS.updaterCheck, () =>
    updater.checkForUpdates(),
  );
  ipcMain.handle(DESKTOP_CHANNELS.updaterQuitAndInstall, () => {
    updater.quitAndInstall();
  });
  Menu.setApplicationMenu(
    buildAppMenu({
      getMainWindow,
      checkForUpdates: updater.checkForUpdates,
    }),
  );

  appTray = createAppTray({
    getMainWindow,
    checkForUpdates: updater.checkForUpdates,
  });

  const deepLinkManager = setupDeepLinkManager({ getMainWindow });
  if (process.platform !== "darwin") {
    for (const arg of process.argv) {
      if (arg.startsWith(`${DEEP_LINK_PROTOCOL}://`)) {
        deepLinkManager.forwardDeepLink(arg);
      }
    }
  }

  await scheduleUpdateCheck(updater.checkForUpdates);
};

const gotSingleInstance = app.requestSingleInstanceLock();
if (!gotSingleInstance) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    const mainWindow = getMainWindow();
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.show();
      mainWindow.focus();
    }

    for (const arg of argv) {
      if (arg.startsWith(`${DEEP_LINK_PROTOCOL}://`)) {
        const parsed = parseDeepLink(arg);
        if (parsed) {
          getMainWindow()?.webContents.send("deep-link-received", parsed);
        }
      }
    }
  });

  void app.whenReady().then(bootstrap);
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (!getMainWindow()) {
    void createMainWindow();
  } else {
    getMainWindow()?.show();
  }
});

app.on("before-quit", () => {
  desktopRuntime.stopAllServices();
  stopLogTail();
  if (appTray) {
    appTray.destroy();
    appTray = null;
  }
});
