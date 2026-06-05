import path from "node:path";
import { BrowserWindow, app } from "electron";
import { openExternalIfSafe } from "./security";

const getRendererUrl = () =>
  process.env.VITE_DEV_SERVER_URL ??
  `file://${path.join(app.getAppPath(), "dist", "index.html")}`;
const getPreloadPath = () =>
  path.join(app.getAppPath(), "dist-electron", "preload.js");
const getIconPath = () =>
  path.join(app.getAppPath(), "build", "iconRounded.png");

let mainWindow: BrowserWindow | null = null;

export const getMainWindow = () => mainWindow;

export const createMainWindow = async () => {
  mainWindow = new BrowserWindow({
    title: "Valuz",
    width: 1440,
    height: 900,
    show: false,
    titleBarStyle: "hidden",
    // TopBar h-36 配 traffic light 居中：(36-13)/2 ≈ 12。
    trafficLightPosition: { x: 10, y: 12 },
    icon: getIconPath(),
    backgroundColor: "#F8F9FB",
    webPreferences: {
      preload: getPreloadPath(),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });

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
