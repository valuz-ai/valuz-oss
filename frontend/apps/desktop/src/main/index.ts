import path from "node:path";
import { Menu, app, ipcMain, nativeImage, protocol } from "electron";

// Override the default "Electron" app name shown in macOS menu bar and tray
app.setName("Valuz");

// Lift Chromium's 6-connections-per-host HTTP/1.1 cap for the loopback backend.
// The renderer holds several long-lived SSE streams to valuz-server (decision
// inbox, per-conversation event stream, Activity per-run streams) — with only
// 6 sockets, those streams plus a burst of polls starve the pool and every
// other fetch to 127.0.0.1 queues browser-side as (pending) with 0 bytes.
// Loopback only: connections to remote/LAN backends keep Chromium defaults.
// Must run before app "ready".
app.commandLine.appendSwitch("ignore-connections-limit", "127.0.0.1,localhost");

// The custom scheme that serves local files to our own renderer must be
// declared privileged BEFORE the app is ready (module-eval time).
import {
  LOCAL_FILE_SCHEME,
  registerLocalFileProtocolHandler,
} from "./local-file-protocol";

protocol.registerSchemesAsPrivileged([
  {
    scheme: LOCAL_FILE_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      bypassCSP: true,
    },
  },
]);
import { setupDeepLinkManager } from "./deep-link";
import { DEEP_LINK_PROTOCOL, parseDeepLink } from "./deep-link-utils";
import { desktopRuntime } from "./ipc/desktop";
import { registerIpcHandlers } from "./ipc";
import { buildAppMenu } from "./menu";
import { setLocale as setMenuI18nLocale } from "@valuz/shared/i18n";
import {
  registerSystemLogIpc,
  startLogTail,
  stopLogTail,
} from "./services/system-logs";
import { createAppTray } from "./tray";
import { scheduleUpdateCheck, setupUpdater } from "./updater";
import {
  closeUpdateWindow,
  createUpdateWindow,
  getUpdateWindow,
} from "./update-window";
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

  registerLocalFileProtocolHandler();
  registerIpcHandlers();
  // Backend log surface: register IPC channels + start tailing the
  // structured JSON file the backend writes (works in dev — where the
  // backend is launched externally — and prod — where Electron spawns
  // the bundled binary). See ``services/system-logs.ts``.
  registerSystemLogIpc();
  startLogTail();

  const updater = setupUpdater({
    getMainWindow,
    getUpdateWindow: () => getUpdateWindow(),
  });
  // Renderer-driven manual check + restart-to-install. setupUpdater()
  // also wires the periodic auto-check (see scheduleUpdateCheck below);
  // these handlers exist so the UI can drive it on demand.
  // Defaults to the About-page trigger (inline error, no toast). The update
  // toast's "retry" passes ``trigger: "menu"`` so a repeated check failure
  // surfaces back in the toast instead of silently vanishing.
  ipcMain.handle(
    DESKTOP_CHANNELS.updaterCheck,
    (_event, args?: { trigger?: string }) =>
      updater.checkForUpdates(args?.trigger === "menu" ? "menu" : "about"),
  );
  ipcMain.handle(DESKTOP_CHANNELS.updaterDownload, () =>
    updater.downloadUpdate(),
  );
  ipcMain.handle(DESKTOP_CHANNELS.updaterGetState, () =>
    updater.getUpdaterState(),
  );
  ipcMain.handle(DESKTOP_CHANNELS.updaterShowWindow, () => {
    const state = updater.getUpdaterState();
    createUpdateWindow(state.version ?? "unknown");
  });
  ipcMain.handle(DESKTOP_CHANNELS.updaterQuitAndInstall, () => {
    closeUpdateWindow();
    updater.quitAndInstall();
  });
  // The native menu is built in the main process, which can't read the
  // renderer's localStorage — seed it from the OS language so it's not stuck
  // on the i18n default, then let the renderer report the actual in-app
  // locale over IPC (set_menu_locale) and rebuild.
  setMenuI18nLocale(
    app.getLocale().toLowerCase().startsWith("zh") ? "zh-CN" : "en-US",
  );
  const applyAppMenu = () =>
    Menu.setApplicationMenu(
      buildAppMenu({
        getMainWindow,
        checkForUpdates: () => updater.checkForUpdates("menu"),
      }),
    );
  applyAppMenu();
  ipcMain.handle(DESKTOP_CHANNELS.setMenuLocale, (_event, payload) => {
    const locale = (payload as { locale?: string } | undefined)?.locale;
    if (locale === "en-US" || locale === "zh-CN") {
      setMenuI18nLocale(locale);
      applyAppMenu();
    }
  });

  // Create the window LAST — once every ipcMain handler above is registered.
  // The renderer invokes ``set_menu_locale`` as soon as it loads (and on each
  // locale change); creating the window earlier raced that invoke against this
  // handler's registration → "No handler registered for 'set_menu_locale'".
  await createMainWindow();

  appTray = createAppTray({
    getMainWindow,
    checkForUpdates: () => updater.checkForUpdates("menu"),
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

// Upper bound on quit-time cleanup so a wedged sidecar can never block the app
// (or the updater's install-on-quit) from exiting. Comfortably above the
// sidecar's own SIGTERM→SIGKILL grace window.
const QUIT_CLEANUP_BUDGET_MS = 8000;
let quitCleanupComplete = false;

app.on("before-quit", (event) => {
  // Second pass (after cleanup): let the real quit proceed so the updater's
  // install-on-quit still runs. We re-issue app.quit() (NOT app.exit()) for
  // exactly this reason.
  if (quitCleanupComplete) return;

  event.preventDefault();
  void (async () => {
    try {
      // Await the process-tree teardown so children release their files before
      // we exit / the installer swaps them. Windows kills the tree synchronously
      // (taskkill /T /F); POSIX awaits the SIGTERM→SIGKILL group shutdown.
      await Promise.race([
        desktopRuntime.stopAllServices(),
        new Promise((resolve) => setTimeout(resolve, QUIT_CLEANUP_BUDGET_MS)),
      ]);
    } catch {
      // Never let cleanup failure trap the app in a non-quitting state.
    } finally {
      stopLogTail();
      if (appTray) {
        appTray.destroy();
        appTray = null;
      }
      quitCleanupComplete = true;
      app.quit();
    }
  })();
});
