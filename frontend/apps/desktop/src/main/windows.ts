import path from "node:path";
import { BrowserWindow, app } from "electron";
import { openExternalIfSafe } from "./security";
import { loadWindowState, trackWindowState } from "./window-state";

const getRendererUrl = () =>
  process.env.VITE_DEV_SERVER_URL ??
  `file://${path.join(app.getAppPath(), "dist", "index.html")}`;
const getPreloadPath = () =>
  path.join(app.getAppPath(), "dist-electron", "preload.js");
// Windows draws the BrowserWindow ``icon`` straight into the taskbar + title
// bar with no padding of its own, so the macOS-style ``iconRounded.png`` (a
// squircle with built-in margin + drop shadow) renders tiny there. Feed
// Windows the multi-size ``icon.ico`` (the mark fills the canvas) instead;
// macOS ignores this (it uses the bundle/.icns + ``app.dock.setIcon``) and
// Linux keeps the rounded PNG.
const getIconPath = () =>
  path.join(
    app.getAppPath(),
    "build",
    process.platform === "win32" ? "icon.ico" : "iconRounded.png",
  );

let mainWindow: BrowserWindow | null = null;

export const getMainWindow = () => mainWindow;

export const createMainWindow = async () => {
  // Remembered geometry when there is any, otherwise a size-capped, centered
  // default — see ``window-state-utils``.
  const { maximized, width, height, x, y } = loadWindowState();
  const windowOptions: Electron.BrowserWindowConstructorOptions = {
    title: "Valuz",
    width,
    height,
    x,
    y,
    show: false,
    icon: getIconPath(),
    backgroundColor: "#F8F9FB",
    webPreferences: {
      preload: getPreloadPath(),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  };

  // All platforms: hidden title bar for a unified custom TopBar.
  // macOS keeps native traffic-light buttons at a custom position.
  // Windows/Linux rely on custom window control buttons rendered in the
  // renderer TopBar.
  windowOptions.titleBarStyle = "hidden";
  if (process.platform === "darwin") {
    windowOptions.trafficLightPosition = { x: 10, y: 12 };
  }

  mainWindow = new BrowserWindow(windowOptions);
  if (maximized) {
    mainWindow.maximize();
  }
  trackWindowState(mainWindow);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void openExternalIfSafe(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      return;
    }
    // Allow same-origin navigation (Vite dev server reloads, in-app routing).
    // Only re-route truly external http(s) URLs to the system browser —
    // otherwise a renderer full reload would open the dev URL in Chrome.
    try {
      const current = new URL(mainWindow!.webContents.getURL());
      const next = new URL(url);
      if (current.origin === next.origin) {
        return;
      }
    } catch {
      // Fall through to external-open if URL parsing fails.
    }
    event.preventDefault();
    void openExternalIfSafe(url);
  });

  // Register `ready-to-show` BEFORE awaiting loadURL — otherwise the event
  // can fire while loadURL is resolving and the listener misses it, leaving
  // the window stuck at `show: false` until the user clicks the dock icon.
  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
    // macOS: terminal-launched Electron apps need explicit foregrounding —
    // `app.focus({ steal: true })` calls
    // [NSApp activateIgnoringOtherApps:YES], `moveTop()` raises the window
    // above its peers, and `setAlwaysOnTop` toggle nudges the WM to actually
    // bring it to the user's screen.
    if (process.platform === "darwin") {
      app.focus({ steal: true });
      mainWindow?.moveTop();
      mainWindow?.setAlwaysOnTop(true);
      setTimeout(() => mainWindow?.setAlwaysOnTop(false), 100);
    }
    mainWindow?.focus();
  });

  const rendererUrl = getRendererUrl();
  await mainWindow.loadURL(rendererUrl);

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  return mainWindow;
};
