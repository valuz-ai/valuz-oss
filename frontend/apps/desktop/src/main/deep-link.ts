import path from "node:path";
import { app, type BrowserWindow } from "electron";
import { DEEP_LINK_PROTOCOL, parseDeepLink } from "./deep-link-utils";

interface DeepLinkManagerOptions {
  getMainWindow: () => BrowserWindow | null;
}

export const setupDeepLinkManager = ({
  getMainWindow,
}: DeepLinkManagerOptions) => {
  // Production (packaged): register the URL scheme via Launch Services so
  // valuz-oss:// activates the existing app instance. Note: also requires the
  // bundle's Info.plist to declare `CFBundleURLTypes` for valuz — without
  // that, this call is fragile and may not stick.
  //
  // Dev: each worktree's `node_modules/.../Electron.app` shares the generic
  // `com.github.Electron` bundle id. If multiple worktrees register the same
  // scheme, macOS picks one nondeterministically — the symptom is "multiple
  // Electron windows open on a single valuz-oss:// click". Default to NOT
  // registering in dev. Opt in for one worktree at a time by exporting
  // `VALUZ_DEV_REGISTER_PROTOCOL=1`.
  if (app.isPackaged) {
    app.setAsDefaultProtocolClient(DEEP_LINK_PROTOCOL);
  } else if (process.env.VALUZ_DEV_REGISTER_PROTOCOL === "1") {
    app.setAsDefaultProtocolClient(DEEP_LINK_PROTOCOL, process.execPath, [
      path.resolve(process.argv[1] ?? "."),
    ]);
  }

  const forwardDeepLink = (url: string) => {
    console.log("[DeepLink] main process received URL:", url);
    const parsed = parseDeepLink(url);
    if (!parsed) {
      console.log("[DeepLink] failed to parse URL");
      return;
    }
    console.log("[DeepLink] parsed:", JSON.stringify(parsed));

    const window = getMainWindow();
    if (!window) {
      console.log("[DeepLink] no main window");
      return;
    }

    window.show();
    window.focus();
    window.webContents.send("deep-link-received", parsed);
    console.log("[DeepLink] sent to renderer");
  };

  app.on("open-url", (event, url) => {
    event.preventDefault();
    forwardDeepLink(url);
  });

  return { forwardDeepLink };
};
