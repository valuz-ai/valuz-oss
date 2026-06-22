import path from "node:path";
import { BrowserWindow, app, screen } from "electron";
import { openExternalIfSafe } from "./security";

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

const IDEAL_WIDTH = 1440;
const IDEAL_HEIGHT = 900;

// Windows/Linux don't auto-clamp oversize windows like macOS does — a fixed
// 1440×900 overflows small laptop displays (1366×768, scaled 1920×1080).
// Cap to 90% of the primary work area and center inside it.
const computeInitialBounds = () => {
  if (process.platform === "darwin") {
    return { width: IDEAL_WIDTH, height: IDEAL_HEIGHT, x: undefined, y: undefined };
  }
  const work = screen.getPrimaryDisplay().workArea;
  const width = Math.min(IDEAL_WIDTH, Math.floor(work.width * 0.9));
  const height = Math.min(IDEAL_HEIGHT, Math.floor(work.height * 0.9));
  const x = work.x + Math.floor((work.width - width) / 2);
  const y = work.y + Math.floor((work.height - height) / 2);
  return { width, height, x, y };
};

let mainWindow: BrowserWindow | null = null;

export const getMainWindow = () => mainWindow;

export const createMainWindow = async () => {
  const { width, height, x, y } = computeInitialBounds();
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
